"""
信号交叉评分引擎（纯 Python，不依赖 LLM）。

核心思路：
1. 从 6 个平台的 raw_data 中提取"项目 / 关键词 / 话题"作为候选实体，
   并保留原始来源条目作为 evidence。
2. 实体名做标准化（小写、去符号、拆分 owner/repo）后，用 n-gram + 同义词表
   做模糊聚类，把跨平台指向同一个东西的信号合并成一个 signal cluster。
3. 依据 cluster 覆盖的 distinct platform 数量评级：
     4-6 平台 → S ；3 → A ；2 → B ；1 → C（丢弃）
4. 给每个 cluster 附带一个 type 标签（rising_tool / opportunity /
   market_shift / hype_warning），供报告生成器使用。

这个模块的产物是"可审计"的信号列表 —— 每条信号都带原始 evidence，
LLM 不需要自己去原始数据里翻找，只需要把结构化信号变成人话。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 文本处理工具
# ---------------------------------------------------------------------------

# 通用停用词（轻量，只为避免把 "the / with / for" 当实体）
_STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "with", "without", "for", "to", "in",
    "on", "of", "at", "by", "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "do", "does", "did", "this", "that", "these", "those",
    "it", "its", "as", "from", "into", "out", "about", "over", "your", "you",
    "we", "our", "they", "their", "he", "she", "his", "her", "them", "not",
    "no", "yes", "can", "will", "would", "should", "could", "may", "might",
    "must", "new", "best", "top", "how", "what", "why", "when", "where",
    "which", "who", "whose", "show", "ask", "hn", "just", "like", "also",
    "one", "two", "three", "get", "using", "use", "used", "more", "most",
    "all", "any", "some", "than", "then", "so", "up", "down", "off", "only",
    "very", "free", "make", "making", "made", "help", "helps", "helping",
    "way", "ways", "time", "times", "data", "info", "guide", "tips", "intro",
    "introducing", "introduction", "announce", "announcing", "release",
    "released", "update", "updates", "version", "my", "i", "me", "im",
    "about", "why", "now", "today", "still", "ever", "even", "via", "vs",
    "after", "before", "while", "because", "need", "needs", "want", "wants",
    "here", "there", "much", "many", "few", "good", "great", "better", "bad",
    "first", "last", "next", "old", "own", "our", "you", "yours", "built",
    "building", "builds", "runs", "running", "run", "making", "made",
    # 噪音高频名词
    "app", "apps", "tool", "tools", "code", "code-", "open-source", "open",
    "source", "api", "apis", "project", "projects", "startup", "startups",
    "product", "products", "user", "users", "model", "models", "thing",
    "things", "someone", "anyone", "everything", "something", "nothing",
    "people", "company", "companies", "team", "teams", "developer",
    "developers", "engineer", "engineers", "work", "works", "working",
    "looking", "look", "looks", "trying", "try", "tried", "think", "thinks",
    "thinking", "thought", "said", "say", "says", "know", "known", "knows",
    "please", "thanks", "hello", "hi", "anyone", "anybody", "everyone",
    "something", "everything", "nothing", "anything", "see", "seen", "saw",
    "show", "shown", "shows", "give", "gives", "given", "gave", "take",
    "taken", "takes", "took", "feel", "feels", "felt", "tell", "tells",
    "told", "come", "comes", "came", "go", "goes", "went", "gone",
    "put", "puts", "got", "set", "sets", "keep", "kept", "let", "lets",
    "find", "finds", "found", "call", "calls", "called", "seem", "seems",
    "seemed", "turn", "turns", "turned", "start", "starts", "started",
    "stop", "stops", "stopped", "end", "ends", "ended", "begin", "begins",
    "began", "talk", "talks", "talked", "ask", "asks", "asked", "write",
    "writes", "wrote", "written", "read", "reads", "reading", "guys",
    "guy", "dude", "man", "woman", "girl", "boy", "kid", "kids", "day",
    "days", "week", "weeks", "month", "months", "year", "years",
}

# 轻量同义词 / 品牌归一化表：key 是出现的候选词，value 是标准化实体名
# （全部 lowercase）。报告分析中若发现新的同名变体可继续补充。
_SYNONYMS: Dict[str, str] = {
    # Anthropic 生态
    "claude": "claude",
    "anthropic": "claude",
    "claude-code": "claude code",
    "claudecode": "claude code",
    "claude-mem": "claude code",
    "claude opus": "claude",
    "opus": "claude",
    "sonnet": "claude",
    "haiku": "claude",
    # OpenAI
    "openai": "openai",
    "chatgpt": "openai",
    "gpt": "openai",
    "gpt-4": "openai",
    "gpt-5": "openai",
    "codex": "openai codex",
    # Google
    "gemini": "gemini",
    "bard": "gemini",
    # Qwen / 阿里
    "qwen": "qwen",
    "qwen3": "qwen",
    "qwen3.6": "qwen",
    # Meta Llama
    "llama": "llama",
    "meta-llama": "llama",
    # 中国开源
    "deepseek": "deepseek",
    "glm": "glm",
    "minimax": "minimax",
    "kimi": "kimi",
    "moonshot": "kimi",
    # Agent 生态
    "agent": "ai agent",
    "agents": "ai agent",
    "ai-agent": "ai agent",
    "ai agent": "ai agent",
    "autonomous-agent": "ai agent",
    # 工具类
    "self-hosted": "self-hosted",
    "selfhosted": "self-hosted",
    "self hosted": "self-hosted",
    "open-source": "open source",
    "opensource": "open source",
    "vibe-coding": "vibe coding",
    "vibecoding": "vibe coding",
}


_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+\-\.]{1,}")


def _tokenize(text: str) -> List[str]:
    """粗粒度分词，保留 `gpt-5`, `claude-code` 这种带连字符的术语。"""
    if not text:
        return []
    tokens = _WORD_RE.findall(text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) >= 2]


def _normalize(name: str) -> str:
    """标准化实体名（走同义词表 + 去首尾符号）。"""
    s = (name or "").strip().lower()
    s = re.sub(r"[_/]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _SYNONYMS.get(s, s)


def _ngrams(tokens: List[str], n: int) -> List[str]:
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


# ---------------------------------------------------------------------------
# Evidence 提取：从 raw_data 各来源抽取候选（name, evidence）
# ---------------------------------------------------------------------------

def _hn_candidates(raw_data: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    hn = raw_data.get("hackernews") or {}
    for bucket in ("front_page", "show_hn", "ask_hn"):
        for item in (hn.get(bucket) or []):
            if not isinstance(item, dict):
                continue
            title = item.get("title") or ""
            if not title:
                continue
            evidence = {
                "platform": "hackernews",
                "bucket": bucket,
                "title": title,
                "url": item.get("url") or item.get("hn_url") or "",
                "metric": f"{item.get('points', 0)} points · {item.get('num_comments', 0)} comments",
                "score": int(item.get("points") or 0),
            }
            out.append((title, evidence))
    return out


def _github_candidates(raw_data: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    gh = raw_data.get("github_trending") or {}
    for r in (gh.get("repositories") or []):
        if not isinstance(r, dict):
            continue
        repo = r.get("repo_name") or ""
        if not repo:
            continue
        # 同时把 owner/repo 和 repo-only 作为候选名，便于匹配
        repo_only = repo.split("/", 1)[-1]
        desc = r.get("description") or ""
        evidence = {
            "platform": "github_trending",
            "title": repo,
            "url": r.get("repo_url") or f"https://github.com/{repo}",
            "description": desc,
            "language": r.get("language") or "",
            "metric": f"{r.get('stars_today', 0)} stars today · {r.get('total_stars', 0)} total",
            "score": int(r.get("stars_today") or 0),
        }
        # repo-only 名字通常是项目名本身
        out.append((repo_only, evidence))
        # 描述里抽的高频词也可以是候选（但权重低，交给 ngram 聚合决定）
    return out


def _ph_candidates(raw_data: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    ph = raw_data.get("producthunt") or {}
    for p in (ph.get("products") or []):
        if not isinstance(p, dict):
            continue
        name = p.get("name") or ""
        if not name:
            continue
        evidence = {
            "platform": "producthunt",
            "title": name,
            "url": p.get("url") or "",
            "description": p.get("tagline") or "",
            "metric": f"{p.get('votes_count', 0)} votes",
            "score": int(p.get("votes_count") or 0),
        }
        out.append((name, evidence))
    return out


def _hf_candidates(raw_data: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    hf = raw_data.get("huggingface") or {}
    for m in (hf.get("models") or []):
        if not isinstance(m, dict):
            continue
        name = m.get("model_name") or ""
        if not name:
            continue
        repo_only = name.split("/", 1)[-1]
        evidence = {
            "platform": "huggingface",
            "title": name,
            "url": m.get("model_url") or f"https://huggingface.co/{name}",
            "metric": f"{m.get('likes', 0)} likes · {m.get('downloads', 0)} downloads",
            "score": int(m.get("likes") or 0),
        }
        out.append((repo_only, evidence))
    return out


def _reddit_candidates(raw_data: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    rd = raw_data.get("reddit") or {}
    for p in (rd.get("posts") or []):
        if not isinstance(p, dict):
            continue
        title = p.get("title") or ""
        if not title:
            continue
        evidence = {
            "platform": "reddit",
            "subreddit": p.get("subreddit") or "",
            "title": title,
            "url": p.get("url") or p.get("reddit_url") or "",
            "metric": f"{p.get('score', 0)} upvotes · {p.get('num_comments', 0)} comments",
            "score": int(p.get("score") or 0),
        }
        out.append((title, evidence))
    return out


def _gtrends_candidates(raw_data: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    gt = raw_data.get("google_trends") or {}
    # 兼容两种结构
    items = gt.get("trends") or gt.get("results") or []
    for t in items:
        if not isinstance(t, dict):
            continue
        kw = t.get("keyword") or t.get("query") or ""
        if not kw:
            continue
        evidence = {
            "platform": "google_trends",
            "title": kw,
            "url": t.get("url") or "",
            "metric": t.get("interest_summary") or t.get("trend") or "trending",
            "score": int(t.get("value") or t.get("interest") or 1),
        }
        out.append((kw, evidence))
    return out


def _collect_candidates(raw_data: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    return (
        _hn_candidates(raw_data)
        + _github_candidates(raw_data)
        + _ph_candidates(raw_data)
        + _hf_candidates(raw_data)
        + _reddit_candidates(raw_data)
        + _gtrends_candidates(raw_data)
    )


# ---------------------------------------------------------------------------
# 聚类：把候选合并成 signal cluster
# ---------------------------------------------------------------------------

def _candidate_terms(name: str, description: str = "") -> List[str]:
    """
    为一条候选产出用于匹配的 term 集合：
    - 标准化后的整名（走同义词表）
    - name 的 unigram / bigram
    - description 里出现的、经同义词表归一的高频词
    """
    terms: List[str] = []

    full_norm = _normalize(name)
    if full_norm and full_norm not in _STOPWORDS:
        terms.append(full_norm)

    base_tokens = _tokenize(name)
    # 对单个 token 走同义词表（比如 "claude" → "claude"）
    for t in base_tokens:
        norm = _SYNONYMS.get(t, t)
        if norm and norm not in _STOPWORDS and len(norm) >= 2:
            terms.append(norm)

    # bigram（覆盖 "ai agent", "vibe coding", "open source" 这种）
    for bg in _ngrams(base_tokens, 2):
        norm = _SYNONYMS.get(bg, bg)
        if norm in _SYNONYMS.values() or norm in _SYNONYMS:
            terms.append(_SYNONYMS.get(norm, norm))

    # description 只抽同义词表命中的（避免噪声）
    for t in _tokenize(description):
        if t in _SYNONYMS:
            terms.append(_SYNONYMS[t])

    # 去重保序
    seen: Set[str] = set()
    out: List[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _cluster_id(term: str) -> str:
    h = hashlib.md5(term.encode("utf-8")).hexdigest()[:8]
    # 生成可读 slug
    slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-") or "signal"
    return f"{slug}-{h}"


# ---------------------------------------------------------------------------
# 类型标签：给每个 cluster 贴 rising_tool / opportunity / market_shift / hype_warning
# ---------------------------------------------------------------------------

_COMPLAINT_PATTERNS = [
    "is broken", "broken", "doesn't work", "doesnt work", "not working",
    "switching from", "switched from", "leaving", "alternative to", "replacing",
    "hate", "frustrated", "annoying", "sick of", "tired of", "fed up",
    "paying", "too expensive", "$", "per month", "monthly fee", "pricing",
    "wish there was", "need a better", "looking for", "anyone built",
    "why is there no", "does anyone know",
]

_OPPORTUNITY_PATTERNS = [
    "show hn", "i built", "i made", "launched", "just shipped", "built a",
    "made a", "open sourcing", "open-sourcing", "releasing", "released my",
    "feedback", "roast", "review my",
]

_HYPE_PATTERNS = [
    "announces", "acquires", "raises", "valuation", "series ", "funding",
    "billion", "million users", "launch event", "keynote",
]


def _classify(cluster_evidence: List[Dict[str, Any]]) -> str:
    """基于 evidence 中的文本线索粗略贴类型标签。"""
    blob = " ".join(
        (e.get("title") or "") + " " + (e.get("description") or "")
        for e in cluster_evidence
    ).lower()

    has_reddit_or_hn_complaint = any(
        (e.get("platform") in ("reddit", "hackernews"))
        and any(p in ((e.get("title") or "") + " " + (e.get("description") or "")).lower()
                for p in _COMPLAINT_PATTERNS)
        for e in cluster_evidence
    )
    has_show_hn = any(
        e.get("platform") == "hackernews" and (e.get("bucket") == "show_hn")
        for e in cluster_evidence
    ) or any(p in blob for p in _OPPORTUNITY_PATTERNS)

    has_github = any(e.get("platform") == "github_trending" for e in cluster_evidence)
    has_hf = any(e.get("platform") == "huggingface" for e in cluster_evidence)

    # 优先级：opportunity > rising_tool > hype_warning > market_shift
    if has_reddit_or_hn_complaint:
        return "opportunity"
    if has_show_hn and (has_github or has_hf):
        return "opportunity"
    if has_github or has_hf:
        return "rising_tool"
    if any(p in blob for p in _HYPE_PATTERNS):
        return "hype_warning"
    return "market_shift"


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def score_signals(
    raw_data: Dict[str, Any],
    *,
    min_grade: str = "B",
    max_signals: int = 30,
) -> List[Dict[str, Any]]:
    """
    对 raw_data 做跨平台交叉评分。

    返回按 grade 降序的信号列表，默认只返回 B 级及以上（C 级丢弃）。
    每条信号形如：
        {
          "signal_id": "...",
          "keywords": [...],
          "platforms": ["hackernews", "github_trending", ...],
          "platform_count": 3,
          "grade": "A",
          "type": "rising_tool",
          "top_score": 1251,
          "evidence": [ {platform, title, url, metric, ...}, ... ]
        }
    """
    candidates = _collect_candidates(raw_data)

    # term → set of candidate indices（同一个 term 被哪些候选命中）
    term_to_indices: Dict[str, Set[int]] = defaultdict(set)
    idx_to_evidence: Dict[int, Dict[str, Any]] = {}

    for i, (name, evidence) in enumerate(candidates):
        idx_to_evidence[i] = evidence
        for term in _candidate_terms(name, evidence.get("description") or ""):
            term_to_indices[term].add(i)

    # 每个 term 形成一个潜在 cluster；我们按 term 聚合，然后按 distinct platform 过滤
    raw_clusters: List[Dict[str, Any]] = []
    for term, idx_set in term_to_indices.items():
        # 单字符或纯数字，跳过
        if len(term) < 2:
            continue
        # 太泛的 term 跳过（走停用词 + 一次再保险）
        if term in _STOPWORDS:
            continue

        platforms: Set[str] = set()
        evidences: List[Dict[str, Any]] = []
        for i in idx_set:
            ev = idx_to_evidence[i]
            platforms.add(ev["platform"])
            evidences.append(ev)

        if len(platforms) < 2:
            # C 级丢弃
            continue

        # 每个平台只保留得分最高的 1-2 条 evidence，避免列表太长
        by_plat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for ev in evidences:
            by_plat[ev["platform"]].append(ev)
        top_evidences: List[Dict[str, Any]] = []
        for plat, evs in by_plat.items():
            evs_sorted = sorted(evs, key=lambda x: x.get("score", 0), reverse=True)
            top_evidences.extend(evs_sorted[:2])

        top_score = max((ev.get("score", 0) or 0) for ev in top_evidences) if top_evidences else 0

        raw_clusters.append({
            "term": term,
            "platforms": sorted(platforms),
            "platform_count": len(platforms),
            "evidence": top_evidences,
            "top_score": top_score,
        })

    # 合并"term 级 cluster"——如果两个 term 覆盖的 evidence 集合高度重叠，视为同一个 cluster
    # 这里用简单策略：按 term 字符串中的核心词（取最长 token）去重，保留 platform_count 高的
    def _core(term: str) -> str:
        toks = [t for t in re.split(r"[\s\-_/]", term) if t and t not in _STOPWORDS]
        if not toks:
            return term
        return max(toks, key=len)

    dedup: Dict[str, Dict[str, Any]] = {}
    for c in raw_clusters:
        core = _SYNONYMS.get(c["term"], _core(c["term"]))
        prev = dedup.get(core)
        if prev is None:
            c2 = dict(c)
            c2["keywords"] = [c["term"]]
            dedup[core] = c2
            continue
        # 合并
        merged_platforms = set(prev["platforms"]) | set(c["platforms"])
        merged_evidence = prev["evidence"] + c["evidence"]
        # evidence 去重（同 url）
        seen_urls: Set[str] = set()
        uniq_ev: List[Dict[str, Any]] = []
        for ev in merged_evidence:
            k = (ev.get("platform"), ev.get("url") or ev.get("title"))
            if k in seen_urls:
                continue
            seen_urls.add(k)
            uniq_ev.append(ev)
        prev["platforms"] = sorted(merged_platforms)
        prev["platform_count"] = len(merged_platforms)
        prev["evidence"] = uniq_ev
        prev["top_score"] = max(prev["top_score"], c["top_score"])
        if c["term"] not in prev["keywords"]:
            prev["keywords"].append(c["term"])

    clusters = list(dedup.values())

    # 评级
    def _grade(n: int) -> str:
        if n >= 4:
            return "S"
        if n == 3:
            return "A"
        if n == 2:
            return "B"
        return "C"

    grade_order = {"S": 0, "A": 1, "B": 2, "C": 3}
    min_order = grade_order.get(min_grade.upper(), 2)

    signals: List[Dict[str, Any]] = []
    for c in clusters:
        grade = _grade(c["platform_count"])
        if grade_order[grade] > min_order:
            continue
        stype = _classify(c["evidence"])
        primary = c["keywords"][0]
        signals.append({
            "signal_id": _cluster_id(primary),
            "keywords": c["keywords"][:5],
            "primary_keyword": primary,
            "platforms": c["platforms"],
            "platform_count": c["platform_count"],
            "grade": grade,
            "type": stype,
            "top_score": c["top_score"],
            "evidence": c["evidence"][:8],  # 限制每条信号最多 8 个证据
        })

    # 排序：先按 grade (S>A>B)，再按 platform_count，再按 top_score
    signals.sort(
        key=lambda s: (grade_order[s["grade"]], -s["platform_count"], -s["top_score"])
    )

    return signals[:max_signals]


__all__ = ["score_signals"]
