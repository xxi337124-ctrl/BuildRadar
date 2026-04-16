"""调用 Claude API 生成结构化日报。"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict

import anthropic

from config_loader import load_config


SYSTEM_PROMPT = """你是 BuildRadar 的首席分析师，一个面向独立开发者和 SaaS 创始人的每日趋势信号雷达。

你的任务是分析来自 6 个平台（Hacker News、GitHub Trending、Product Hunt、HuggingFace、Google Trends、Reddit）的原始数据，交叉验证信号，生成一份简洁、可执行的每日报告。

分析原则：
1. 信号交叉验证：当同一个话题/项目/趋势出现在 2 个以上平台时，标记为强信号
2. 重视"抱怨"：开发者的吐槽 = 未被满足的需求 = 构建机会
3. 可执行优先：每个发现都要回答"作为独立开发者，我能用这个做什么"
4. 数据驱动：每个观点必须引用具体数据（星数、票数、评论数、搜索趋势）
5. 简洁：整份报告控制在 2000 字以内，开发者没时间读长文
"""


USER_PROMPT_TEMPLATE = """今天是 {date}。以下是从 6 个平台采集的原始数据：

```json
{raw_data_json}
```

请生成今天的 BuildRadar 报告，严格使用以下 Markdown 结构：

# BuildRadar Daily — {date}

## 🔥 今日 3 大信号

对于每条信号，使用以下格式：
### 信号 N：[标题]
- **信号强度**：★★★（3 个平台验证） / ★★（2 个平台） / ★（单一平台）
- **数据来源**：列出具体的平台和数据点（如 "HN 523 points + GitHub 53K stars + Google Trends +950%"）
- **一句话概述**：这个趋势意味着什么
- **构建机会**：独立开发者可以怎么利用这个信号

## 🛠 本周该构建什么

给出 1 个最具体、最可执行的构建建议，包含：
- **做什么**：一句话描述产品
- **为什么现在做**：引用今天数据中的具体信号
- **目标用户**：谁会用，他们现在的痛点是什么（引用 Reddit/HN 中的真实抱怨）
- **最小 MVP**：只需要包含哪些功能，可以砍掉什么
- **技术栈建议**：用什么技术栈，为什么
- **验证方法**：发布到哪里获取第一批用户，怎么判断是否值得继续

## 📊 信号雷达

用表格展示今天采集到的关键数据点：

| 平台 | 项目/话题 | 热度指标 | 趋势 |
|------|----------|---------|------|
| HN | ... | 523 points | 🔺 |
| GitHub | ... | 2,150 stars/day | 🔺 |
| ... | ... | ... | ... |

只列出最重要的 15 条，按信号强度排序。趋势用 🔺（上升）🔻（下降）🔸（新出现）标注。

## 🔗 值得关注的链接

列出今天数据中最值得点击的 5 个链接，每条附一句话说明为什么值得看。

---

要求：
- 中文输出
- 不要虚构任何数据，所有数字必须来自提供的原始数据
- 如果某个平台的数据缺失（例如标注 skipped 或 error），跳过，不要编造
- 信号强度严格基于跨平台出现次数
- 整体风格：直接、数据驱动、无废话
- 不要在最开头输出任何多余解释，直接从 "# BuildRadar Daily — {date}" 开始
"""


def truncate_data(raw_data: Dict[str, Any], max_chars: int = 30000) -> Dict[str, Any]:
    """
    将 raw_data JSON 字符数控制在 max_chars 以内。
    策略：按优先级逐级截断各来源的条目数量，优先保留高分/高星条目。
    """
    data = copy.deepcopy(raw_data)

    # 各来源的"列表键"和排序键
    limits = [
        # (path, sort_key, initial_limit)
        (("hackernews", "front_page"), "points", 25),
        (("hackernews", "show_hn"), "points", 10),
        (("hackernews", "ask_hn"), "points", 5),
        (("github_trending", "repositories"), "stars_today", 30),
        (("producthunt", "products"), "votes_count", 15),
        (("huggingface", "models"), "likes", 15),
        (("reddit", "posts"), "score", 25),
        (("google_trends", "trends"), None, 20),
    ]

    def _apply_limit(limit_overrides: Dict[tuple, int]) -> Dict[str, Any]:
        d = copy.deepcopy(raw_data)
        for (path, sort_key, default_limit) in limits:
            src, key = path
            if src not in d or not isinstance(d[src], dict):
                continue
            lst = d[src].get(key)
            if not isinstance(lst, list):
                continue
            limit = limit_overrides.get(path, default_limit)
            if sort_key:
                lst = sorted(lst, key=lambda x: x.get(sort_key, 0) or 0, reverse=True)
            d[src][key] = lst[:limit]
        return d

    current = {p: lim for (p, _k, lim) in limits}
    data = _apply_limit(current)
    text = json.dumps(data, ensure_ascii=False)

    # 若超长，迭代缩减
    shrink_order = [
        ("reddit", "posts"),
        ("github_trending", "repositories"),
        ("hackernews", "front_page"),
        ("huggingface", "models"),
        ("producthunt", "products"),
        ("hackernews", "show_hn"),
        ("hackernews", "ask_hn"),
        ("google_trends", "trends"),
    ]

    max_iters = 30
    while len(text) > max_chars and max_iters > 0:
        max_iters -= 1
        shrunk_any = False
        for path in shrink_order:
            if current.get(path, 0) > 3:
                current[path] = max(3, int(current[path] * 0.7))
                shrunk_any = True
                break
        if not shrunk_any:
            break
        data = _apply_limit(current)
        text = json.dumps(data, ensure_ascii=False)

    # 最后兜底硬截断
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
        # 解析失败就保留截断 data 对象
        try:
            data = json.loads(text.rsplit("}", 1)[0] + "}")
        except Exception:
            pass
    return data


class ReportGenerator:
    """用 Claude API 生成结构化日报。"""

    def __init__(self) -> None:
        cfg = load_config()["analyzer"]
        self.model = cfg.get("model", "claude-sonnet-4-20250514")
        self.max_tokens = int(cfg.get("max_tokens", 6000))
        self.max_input_chars = int(cfg.get("max_input_chars", 30000))
        # anthropic.Anthropic() 会自动从 ANTHROPIC_API_KEY 读取
        self.client = anthropic.Anthropic()

    def generate(self, date: str, raw_data: Dict[str, Any]) -> str:
        truncated = truncate_data(raw_data, max_chars=self.max_input_chars)
        raw_json = json.dumps(truncated, ensure_ascii=False, indent=2)

        user_prompt = USER_PROMPT_TEMPLATE.format(date=date, raw_data_json=raw_json)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # 合并所有 text 块
        parts = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()


def build_mock_report(date: str, raw_data: Dict[str, Any]) -> str:
    """
    当没有 ANTHROPIC_API_KEY 时，本地兜底生成一个简易报告用于调试。
    """
    lines = []
    lines.append(f"# BuildRadar Daily — {date}")
    lines.append("")
    lines.append("> ⚠️ 未检测到 `ANTHROPIC_API_KEY`，本报告为离线兜底版本，仅展示原始数据骨架，未做 LLM 交叉分析。")
    lines.append("")
    lines.append("## 🔥 今日 3 大信号")
    lines.append("")
    lines.append("- 无法生成（需要 Claude API）。")
    lines.append("")
    lines.append("## 🛠 本周该构建什么")
    lines.append("")
    lines.append("- 无法生成（需要 Claude API）。")
    lines.append("")
    lines.append("## 📊 信号雷达")
    lines.append("")
    lines.append("| 平台 | 项目/话题 | 热度指标 | 趋势 |")
    lines.append("|------|----------|---------|------|")

    # HN
    hn = raw_data.get("hackernews") or {}
    for p in (hn.get("front_page") or [])[:5]:
        title = (p.get("title") or "").replace("|", "/")[:60]
        lines.append(f"| HN | {title} | {p.get('points',0)} points | 🔺 |")

    # GitHub
    gh = raw_data.get("github_trending") or {}
    for r in (gh.get("repositories") or [])[:5]:
        name = (r.get("repo_name") or "").replace("|", "/")[:60]
        lines.append(f"| GitHub | {name} | {r.get('stars_today',0)} stars today | 🔺 |")

    # HuggingFace
    hf = raw_data.get("huggingface") or {}
    for m in (hf.get("models") or [])[:3]:
        name = (m.get("model_name") or "").replace("|", "/")[:60]
        lines.append(f"| HuggingFace | {name} | {m.get('likes',0)} likes | 🔺 |")

    # Reddit
    rd = raw_data.get("reddit") or {}
    for p in (rd.get("posts") or [])[:3]:
        title = (p.get("title") or "").replace("|", "/")[:60]
        lines.append(f"| Reddit r/{p.get('subreddit','')} | {title} | {p.get('score',0)} upvotes | 🔺 |")

    lines.append("")
    lines.append("## 🔗 值得关注的链接")
    lines.append("")
    count = 0
    for p in (hn.get("front_page") or [])[:3]:
        lines.append(f"- [{p.get('title','')}]({p.get('url','')}) — HN {p.get('points',0)} points")
        count += 1
    for r in (gh.get("repositories") or [])[:2]:
        lines.append(f"- [{r.get('repo_name','')}]({r.get('repo_url','')}) — GitHub {r.get('stars_today',0)} stars today")
        count += 1
    if count == 0:
        lines.append("- 暂无数据。")
    lines.append("")
    return "\n".join(lines)
