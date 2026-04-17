"""
机会原材料提取器（纯 Python，不依赖 LLM）。

从 Reddit、HackerNews 等讨论型数据中筛选出以下三类帖子：

1. complaint（抱怨）：用户在吐槽现有工具 —— 典型"未被满足的需求"
2. seeking_alternative（求替代）：用户明确在找更好 / 自托管 / 开源的版本
3. shipping（自建展示）：Show HN、r/SideProject 里自曝的项目 + 社区反馈

从 GitHub Trending 中额外筛选"有无商业版本"未知、疑似纯开源的快速增长项目，
作为"可能有商业化机会"的参考原材料。

这个模块只做**筛选和结构化**，不做判断。最终决定"该不该做"由 LLM 根据
这些结构化材料 + scored_signals 给出。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 模式
# ---------------------------------------------------------------------------

COMPLAINT_PATTERNS = [
    # 直接抱怨
    r"\b(is|are) (broken|terrible|awful|garbage|slow|buggy)\b",
    r"\bdoesn'?t (work|support|handle)\b",
    r"\b(hate|tired of|sick of|fed up|frustrated with)\b",
    r"\b(too|so) (expensive|slow|complicated|buggy)\b",
    # 切换/替换意图
    r"\b(switching|switched|moving|moved|leaving) (from|away from)\b",
    r"\breplacing (my|our|the)\b",
    # 定价痛点
    r"\$\d{2,}[^\d]",
    r"\b\d+\s?(usd|dollars)\b",
    r"\bper month\b|\bmonthly fee\b|\bsubscription\b",
    # 显式吐槽
    r"\bwhy (is|does|are)\b.*\bso\b",
    r"\bwhy is there no\b",
]

SEEKING_ALTERNATIVE_PATTERNS = [
    r"\balternatives? to\b",
    r"\b(best|any|good) (open.?source|self.?hosted|free)\b",
    r"\blooking for (a|an|the)\b",
    r"\banyone (built|know|tried|recommend)\b",
    r"\bwish there was\b",
    r"\bneed a better\b",
    r"\brecommend(ations?)?\b.{0,20}(tool|app|saas|platform)",
]

SHIPPING_PATTERNS = [
    r"\bshow hn\b",
    r"\bi built\b|\bi made\b|\bbuilt (a|an|my)\b",
    r"\bjust (shipped|launched|released)\b",
    r"\b(launched|shipped|released) (my|a|an|our)\b",
    r"\bopen.?sourc(ing|ed)\b",
    r"\bfeedback\b.{0,20}(please|wanted|appreciated)",
    r"\broast my\b",
]

COMPLAINT_RE = [re.compile(p, re.IGNORECASE) for p in COMPLAINT_PATTERNS]
SEEKING_RE = [re.compile(p, re.IGNORECASE) for p in SEEKING_ALTERNATIVE_PATTERNS]
SHIPPING_RE = [re.compile(p, re.IGNORECASE) for p in SHIPPING_PATTERNS]


def _matches(text: str, regexes: List[re.Pattern]) -> List[str]:
    hits = []
    for r in regexes:
        m = r.search(text or "")
        if m:
            hits.append(m.group(0))
    return hits


def _classify_post(text: str) -> Optional[str]:
    """返回 'complaint' / 'seeking_alternative' / 'shipping'，无命中返回 None。"""
    if not text:
        return None
    # 优先级：抱怨 > 求替代 > 展示
    if _matches(text, COMPLAINT_RE):
        return "complaint"
    if _matches(text, SEEKING_RE):
        return "seeking_alternative"
    if _matches(text, SHIPPING_RE):
        return "shipping"
    return None


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def _extract_from_reddit(raw_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    rd = raw_data.get("reddit") or {}
    for p in (rd.get("posts") or []):
        if not isinstance(p, dict):
            continue
        title = p.get("title") or ""
        # Reddit 的 self-text 采集器不一定有，稳妥只用 title
        category = _classify_post(title)
        if not category:
            continue
        out.append({
            "source": "reddit",
            "category": category,
            "subreddit": p.get("subreddit") or "",
            "title": title,
            "url": p.get("reddit_url") or p.get("url") or "",
            "score": int(p.get("score") or 0),
            "num_comments": int(p.get("num_comments") or 0),
            "evidence_phrases": _matches(
                title,
                COMPLAINT_RE if category == "complaint"
                else SEEKING_RE if category == "seeking_alternative"
                else SHIPPING_RE,
            )[:3],
        })
    return out


def _extract_from_hn(raw_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    hn = raw_data.get("hackernews") or {}

    # Show HN 全部归为 shipping（而且评论数本身是反馈信号）
    for p in (hn.get("show_hn") or []):
        if not isinstance(p, dict):
            continue
        title = p.get("title") or ""
        if not title:
            continue
        out.append({
            "source": "hackernews",
            "category": "shipping",
            "bucket": "show_hn",
            "title": title,
            "url": p.get("url") or p.get("hn_url") or "",
            "hn_url": p.get("hn_url") or "",
            "score": int(p.get("points") or 0),
            "num_comments": int(p.get("num_comments") or 0),
            "evidence_phrases": ["Show HN"],
        })

    # Ask HN：只有在标题明确命中 complaint / seeking_alternative / shipping 模式时才纳入
    # （避免把"Ask HN: I'm going blind"这种人生咨询误判为找替代工具）
    for p in (hn.get("ask_hn") or []):
        if not isinstance(p, dict):
            continue
        title = p.get("title") or ""
        category = _classify_post(title)
        if not category:
            continue
        out.append({
            "source": "hackernews",
            "category": category,
            "bucket": "ask_hn",
            "title": title,
            "url": p.get("url") or p.get("hn_url") or "",
            "hn_url": p.get("hn_url") or "",
            "score": int(p.get("points") or 0),
            "num_comments": int(p.get("num_comments") or 0),
            "evidence_phrases": _matches(
                title,
                COMPLAINT_RE if category == "complaint"
                else SEEKING_RE if category == "seeking_alternative"
                else SHIPPING_RE,
            )[:3],
        })

    # front page：只挑标题里命中 complaint / seeking 的
    for p in (hn.get("front_page") or []):
        if not isinstance(p, dict):
            continue
        title = p.get("title") or ""
        category = _classify_post(title)
        if category not in ("complaint", "seeking_alternative"):
            continue
        out.append({
            "source": "hackernews",
            "category": category,
            "bucket": "front_page",
            "title": title,
            "url": p.get("url") or p.get("hn_url") or "",
            "hn_url": p.get("hn_url") or "",
            "score": int(p.get("points") or 0),
            "num_comments": int(p.get("num_comments") or 0),
            "evidence_phrases": _matches(
                title,
                COMPLAINT_RE if category == "complaint" else SEEKING_RE,
            )[:3],
        })

    return out


def _extract_from_github(raw_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    筛选"快速增长且疑似纯开源（无商业版本标签）"的项目作为 build-on-top-of 原料。
    我们没法严格判断是否有 SaaS 版本，但可以看 description 关键词避开大厂/已商业化项目。
    """
    out: List[Dict[str, Any]] = []
    gh = raw_data.get("github_trending") or {}

    BIG_CORP_PREFIXES = {
        "microsoft", "google", "meta", "facebook", "apple", "amazon", "aws",
        "openai", "anthropic", "alibaba", "tencent", "bytedance", "huawei",
        "nvidia", "cloudflare", "vercel", "stripe", "supabase",
    }
    COMMERCIAL_HINTS = ("pricing", "paid plan", "saas", "commercial", "enterprise")

    for r in (gh.get("repositories") or []):
        if not isinstance(r, dict):
            continue
        repo = (r.get("repo_name") or "")
        owner = repo.split("/", 1)[0].lower() if "/" in repo else ""
        desc = (r.get("description") or "").lower()
        stars_today = int(r.get("stars_today") or 0)

        if stars_today < 100:
            continue
        if owner in BIG_CORP_PREFIXES:
            continue
        if any(h in desc for h in COMMERCIAL_HINTS):
            continue

        out.append({
            "source": "github_trending",
            "category": "rising_oss",
            "title": repo,
            "url": r.get("repo_url") or f"https://github.com/{repo}",
            "description": r.get("description") or "",
            "language": r.get("language") or "",
            "stars_today": stars_today,
            "total_stars": int(r.get("total_stars") or 0),
        })

    # 按 stars_today 降序
    out.sort(key=lambda x: x.get("stars_today", 0), reverse=True)
    return out


def extract_opportunities(
    raw_data: Dict[str, Any],
    scored_signals: Optional[List[Dict[str, Any]]] = None,
    *,
    max_per_category: int = 8,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    返回结构化的"机会原材料"：
    {
      "complaints": [...],            # 抱怨型帖子
      "seeking_alternatives": [...],  # 求替代方案
      "shipping": [...],              # Show HN / 自建展示
      "rising_oss": [...],            # 疑似纯开源的快速增长项目
    }
    每个列表按热度（score / stars_today）降序，截取前 max_per_category 条。
    """
    reddit_items = _extract_from_reddit(raw_data)
    hn_items = _extract_from_hn(raw_data)
    gh_items = _extract_from_github(raw_data)

    buckets: Dict[str, List[Dict[str, Any]]] = {
        "complaints": [],
        "seeking_alternatives": [],
        "shipping": [],
        "rising_oss": gh_items[:max_per_category],
    }

    for item in reddit_items + hn_items:
        cat = item.get("category")
        if cat == "complaint":
            buckets["complaints"].append(item)
        elif cat == "seeking_alternative":
            buckets["seeking_alternatives"].append(item)
        elif cat == "shipping":
            buckets["shipping"].append(item)

    # 按热度排序并截断
    for key in ("complaints", "seeking_alternatives", "shipping"):
        buckets[key].sort(key=lambda x: x.get("score", 0), reverse=True)
        buckets[key] = buckets[key][:max_per_category]

    return buckets


__all__ = ["extract_opportunities"]
