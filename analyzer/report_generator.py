"""
调用 Claude API 把结构化信号变成"决策型日报"。

与旧实现的核心区别：
- 输入不再是 raw_data 的大杂烩，而是 signal_scorer / opportunity_extractor
  / signal_history 产出的结构化数据（通常只有 5-8K 字符）。
- Prompt 围绕"该构建什么"组织，而不是"发生了什么"。
- 报告固定 3 板块：构建机会 / 信号简报 / 上周回顾。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import anthropic

from config_loader import load_config


SYSTEM_PROMPT = """你是 BuildRadar 的分析师。你的唯一职责是帮助独立开发者和超级个体回答一个问题：
"今天我应该构建什么？"

你不是新闻记者——不要报道发生了什么。
你不是趋势分析师——不要罗列趋势。
你是一个有数据支撑的联合创始人，每天给出可执行的构建建议。

写作原则：
1. 每个观点必须直接回答"所以我该做什么"
2. 永远先说结论，再说证据
3. 不废话。如果一段话删掉不影响决策，就删掉
4. 明确说"不要做什么"和"做什么"一样重要
5. 信号等级严格基于输入中 signals[].grade 字段，不要自己编
6. 全文控制在 1500 字以内——读者在用手机看邮件，5 分钟必须看完
7. 竞品分析要诚实——如果一个机会已经很拥挤，直接说"这个别做"
8. 禁止虚构任何数据、链接、人名、价格、具体用户帖子。只引用输入里真实存在的内容
"""


USER_PROMPT_TEMPLATE = """今天是 {date}。

以下是今天的**结构化信号数据**（已由规则引擎做完跨平台交叉评分和筛选）：

## scored_signals（跨平台验证过的强信号，按等级排序）
```json
{signals_json}
```

## opportunities（从 Reddit / HackerNews 筛选出的"机会原材料"）
```json
{opportunities_json}
```

## trends（与最近 7 天的对比）
```json
{trends_json}
```

## last_week（上周推荐过的信号，用于回顾）
```json
{last_week_json}
```

---

请严格按以下 Markdown 结构输出今天的报告。**直接从 `# BuildRadar — {date}` 开始，不要任何开场白。**

# BuildRadar — {date}

## 一、今天的构建机会

选出 1-2 个最值得做的机会（优先从 signals 中 `type: "opportunity"` 或 `type: "rising_tool"` 的 A/S 级信号里来，结合 opportunities 里的抱怨/求替代原材料）。如果今天数据里没有值得做的机会，就诚实地说"今天没有 A 级以上的构建机会值得行动，建议继续观察"——**不要为了凑版面强行编造**。

每个机会用下面这个精确结构：

### 机会 N：[一句话描述产品——不超过 20 字]

- **信号等级**：S / A / B（直接引用 signals 里的 grade）
- **为什么是现在**：用 2-3 句话说明时间窗口。必须引用 signals/opportunities 里的**具体数据**（比如"HN 1199 points + GitHub 7939 stars/day"或"r/SaaS 有用户抱怨 Sendible $400/月太贵"）
- **已有竞品**：列出 1-3 个已存在的竞品名（**仅限输入数据里真实出现的**），诚实说它们差在哪。如果竞品拥挤，写"赛道拥挤，建议绕道"
- **最小 MVP**：
  - 做：3-5 个核心功能，用动词开头
  - 不做：2-3 个明确砍掉的功能
  - 技术栈：具体到框架（如 "Next.js + Supabase + OpenAI API"）
  - 预估时间：周末 / 1 周 / 2 周
- **第一批用户在哪**：具体到 subreddit / HN 帖子 / Discord —— 只能引用输入里出现过的来源
- **收入模型**：定价 + 理由。如"$19/月，对标 Buffer 的 $15 档但加一个 AI 生成功能"

## 二、信号简报

把 signals 里 B 级以上的信号按 type 分组，**每条 1-2 句话**，不展开。

**🔨 值得深挖的机会**（type: opportunity）
- [primary_keyword]（[grade] 级，{{platforms}}）：一句话说明，附 1 个具体数据点

**📈 正在崛起的工具/技术**（type: rising_tool）
- [primary_keyword]（[grade] 级）：一句话

**🔄 市场变化**（type: market_shift）
- [primary_keyword]（[grade] 级）：一句话

**⚠️ 应该避开的热点**（type: hype_warning，或 rising_tool 但 evidence 全来自大厂）
- [primary_keyword]：为什么要避开（一句话）

如果某个类别今天没有信号，**整个小节不要出现**（不要写"今天没有"）。

## 三、上周回顾

根据 trends 和 last_week 数据：
- 如果 last_week 为空（第一期）：写"本期为第一期，下周开始追踪信号演化。"
- 否则按以下格式列出：
  - **[primary_keyword]**：[trend 含义] —— 比如"上周 B 级 → 今天 A 级，🔺 升温"、"连续 3 天 A 级，🔥 持续热门"、"上周 A 级 → 今天未出现，🔻 冷却"
- 最多列 3-5 条最重要的变化。不要把没变化的信号列出来。

---

记住：
- 全文 1500 字以内
- 数据全部来自上面的 JSON，绝不编造
- 每个机会都必须能让读者"合上手机就知道今天动手做什么"
"""


class ReportGenerator:
    """用 Claude 把结构化信号变成决策型日报。"""

    def __init__(self) -> None:
        cfg = load_config()["analyzer"]
        self.model = cfg.get("model", "claude-sonnet-4-20250514")
        self.max_tokens = int(cfg.get("max_tokens", 6000))
        # anthropic.Anthropic() 会自动从 ANTHROPIC_API_KEY 读取
        self.client = anthropic.Anthropic()

    def generate(
        self,
        date: str,
        signals: List[Dict[str, Any]],
        opportunities: Dict[str, List[Dict[str, Any]]],
        trends: List[Dict[str, Any]],
        last_week: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        user_prompt = USER_PROMPT_TEMPLATE.format(
            date=date,
            signals_json=json.dumps(signals, ensure_ascii=False, indent=2),
            opportunities_json=json.dumps(opportunities, ensure_ascii=False, indent=2),
            trends_json=json.dumps(trends, ensure_ascii=False, indent=2),
            last_week_json=json.dumps(last_week or [], ensure_ascii=False, indent=2),
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        parts: List[str] = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# 离线 mock（无 ANTHROPIC_API_KEY 时使用）
# ---------------------------------------------------------------------------

_TYPE_LABEL = {
    "opportunity": "🔨 值得深挖的机会",
    "rising_tool": "📈 正在崛起的工具/技术",
    "market_shift": "🔄 市场变化",
    "hype_warning": "⚠️ 应该避开的热点",
}

_TREND_LABEL = {
    "rising": "🔺 升温",
    "cooling": "🔻 冷却",
    "persistent": "🔥 持续热门",
    "new": "🆕 新出现",
    "steady": "— 平稳",
}


def build_mock_report(
    date: str,
    signals: List[Dict[str, Any]],
    opportunities: Dict[str, List[Dict[str, Any]]],
    trends: List[Dict[str, Any]],
    last_week: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    无 LLM 时的兜底：基于结构化信号直接拼成可读的 Markdown 报告。
    这一版不是"凑数占位"，而是真的展示当天 B+ 信号、机会原材料和趋势变化，
    让读者即使没 API key 也能看出信号雷达的价值。
    """
    lines: List[str] = []
    lines.append(f"# BuildRadar — {date}")
    lines.append("")
    lines.append(
        "> ⚠️ 未检测到 `ANTHROPIC_API_KEY`，本报告为规则引擎直出版本——"
        "只展示跨平台交叉验证的信号，未做 LLM 的「构建机会」深度分析。"
    )
    lines.append("")

    # -------------------- 一、今天的构建机会 --------------------
    lines.append("## 一、今天的构建机会")
    lines.append("")
    op_signals = [s for s in signals if s.get("type") == "opportunity" and s.get("grade") in ("S", "A")]
    if not op_signals:
        lines.append("今天没有 A 级以上的明确构建机会信号。建议继续观察，避免为了行动而行动。")
        lines.append("")
        # 下面 fallback 到 rising_tool 也展示一下，至少让读者看到方向
        fallback = [s for s in signals if s.get("grade") in ("S", "A")][:1]
        if fallback:
            lines.append("**相关趋势参考**（非直接机会，需要你自己挖掘）：")
            for s in fallback:
                lines.append(
                    f"- **{s.get('primary_keyword')}**（{s.get('grade')} 级，"
                    f"{len(s.get('platforms', []))} 平台验证）"
                )
            lines.append("")
    else:
        for i, s in enumerate(op_signals[:2], 1):
            lines.append(f"### 机会 {i}：围绕 {s.get('primary_keyword')} 构建")
            lines.append("")
            lines.append(f"- **信号等级**：{s.get('grade')} 级 —— {s.get('platform_count')} 个平台交叉验证")
            lines.append(f"- **为什么是现在**：")
            for e in s.get("evidence", [])[:3]:
                lines.append(f"  - {e.get('platform')}: {e.get('title', '')[:70]} — {e.get('metric', '')}")
            lines.append("- **注**：具体 MVP / 竞品 / 定价分析需要 LLM，当前为规则引擎兜底版本。")
            lines.append("")

    # -------------------- 二、信号简报 --------------------
    lines.append("## 二、信号简报")
    lines.append("")
    if not signals:
        lines.append("今天没有 B 级以上的跨平台信号。")
        lines.append("")
    else:
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for s in signals:
            by_type.setdefault(s.get("type", "market_shift"), []).append(s)
        for t in ("opportunity", "rising_tool", "market_shift", "hype_warning"):
            items = by_type.get(t) or []
            if not items:
                continue
            lines.append(f"**{_TYPE_LABEL.get(t, t)}**")
            lines.append("")
            for s in items[:5]:
                plats = "·".join(s.get("platforms", []))
                top_ev = (s.get("evidence") or [{}])[0]
                metric = top_ev.get("metric", "")
                lines.append(
                    f"- **{s.get('primary_keyword')}**（{s.get('grade')} 级，{plats}）"
                    f" — {top_ev.get('title', '')[:60]}（{metric}）"
                )
            lines.append("")

    # -------------------- 三、上周回顾 --------------------
    lines.append("## 三、上周回顾")
    lines.append("")
    if not last_week:
        lines.append("本期为第一期，下周开始追踪信号演化。")
        lines.append("")
    else:
        changed = [t for t in trends if t.get("trend") in ("rising", "cooling", "persistent")]
        if not changed:
            lines.append("今天的信号与上周相比无显著变化。")
            lines.append("")
        else:
            for t in changed[:5]:
                label = _TREND_LABEL.get(t.get("trend", "steady"), "")
                lines.append(
                    f"- **{t.get('primary_keyword')}**：{label}"
                    f"（上周最高 {t.get('previous_best_grade')} 级 → 今天 {t.get('grade_today')} 级，"
                    f"近 7 天出现 {t.get('appeared_in_last_n_days')} 次）"
                )
            lines.append("")

    # -------------------- 附：信号证据表 --------------------
    if signals:
        lines.append("---")
        lines.append("")
        lines.append("## 附：信号证据表")
        lines.append("")
        lines.append("| 信号 | 等级 | 类型 | 平台数 | Top 证据 |")
        lines.append("|------|------|------|--------|----------|")
        for s in signals[:15]:
            top_ev = (s.get("evidence") or [{}])[0]
            title = (top_ev.get("title") or "")[:50].replace("|", "/")
            metric = (top_ev.get("metric") or "").replace("|", "/")
            lines.append(
                f"| {s.get('primary_keyword')} | {s.get('grade')} | {s.get('type')} |"
                f" {s.get('platform_count')} | {title} ({metric}) |"
            )
        lines.append("")

    return "\n".join(lines)


__all__ = ["ReportGenerator", "build_mock_report"]
