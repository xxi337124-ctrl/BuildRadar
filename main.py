"""
BuildRadar 主流程
用法：
  python main.py              # 正常运行（如果今日报告已存在则跳过生成，但仍重建站点）
  python main.py --force      # 强制重新采集和生成
  python main.py --site-only  # 只重建站点，不采集、不生成
  python main.py --skip-llm   # 采集并保存原始数据，但不调用 LLM（本地快速调试）
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from config_loader import get_env, load_config
from collectors.hackernews import HackerNewsCollector
from collectors.github_trending import GitHubTrendingCollector
from collectors.producthunt import ProductHuntCollector
from collectors.huggingface import HuggingFaceCollector
from collectors.google_trends import GoogleTrendsCollector
from collectors.reddit import RedditCollector
from analyzer.report_generator import ReportGenerator, build_mock_report
from analyzer.signal_scorer import score_signals
from analyzer.opportunity_extractor import extract_opportunities
from analyzer.signal_history import (
    load_signal_history,
    update_signal_history,
    compute_trends,
    previous_reports_summary,
)
from publisher.markdown_writer import save_report, save_raw_data, report_exists
from publisher.site_builder import build_site


# 英文停用词（只用于从标题/描述中提取相对有意义的词）
STOPWORDS = {
    "the","a","an","and","or","but","with","without","for","to","in","on","of","at","by",
    "is","are","was","were","be","been","being","has","have","had","do","does","did",
    "this","that","these","those","it","its","as","from","into","out","about","over",
    "your","you","we","our","they","their","he","she","his","her","them",
    "not","no","yes","can","will","would","should","could","may","might","must",
    "new","best","top","how","what","why","when","where","which","who","whose",
    "show","ask","hn","just","like","also","one","two","get","using","use","used",
    "more","most","all","any","some","than","then","so","up","down","off","only","very",
    "free","open","source","ai","code","app","apps","tool","tools","build","builds",
    "make","making","made","help","helps","helping","way","ways","time","times","data",
    "info","guide","tips","intro","introducing","introduction","announce","announcing",
    "release","released","update","updates","version","v1","v2","v3",
}


def _extract_words(text: str) -> List[str]:
    text = (text or "").lower()
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", text)
    return [w for w in words if w not in STOPWORDS and not w.isdigit()]


def extract_keywords(raw_data: Dict[str, Any]) -> List[str]:
    """
    从已采集的数据中提取高频关键词 + 固定关键词，用于 Google Trends 查询。
    """
    cfg = load_config()["collectors"]["google_trends"]
    fixed: List[str] = list(cfg.get("fixed_keywords") or [])
    max_trending = int(cfg.get("max_trending_keywords", 10))

    # 汇总文本：HN 标题 + GitHub 仓库名/描述 + PH 名/slogan + HF 名 + Reddit 标题
    texts: List[str] = []

    hn = raw_data.get("hackernews") or {}
    for item in (hn.get("front_page") or []) + (hn.get("show_hn") or []):
        if item.get("title"):
            texts.append(item["title"])

    gh = raw_data.get("github_trending") or {}
    for r in (gh.get("repositories") or []):
        # 只取仓库名中的 name 部分
        name = (r.get("repo_name") or "").split("/", 1)[-1]
        texts.append(name.replace("-", " ").replace("_", " "))
        if r.get("description"):
            texts.append(r["description"])

    ph = raw_data.get("producthunt") or {}
    for p in (ph.get("products") or []):
        if p.get("name"):
            texts.append(p["name"])
        if p.get("tagline"):
            texts.append(p["tagline"])

    hf = raw_data.get("huggingface") or {}
    for m in (hf.get("models") or []):
        name = (m.get("model_name") or "").split("/", 1)[-1]
        texts.append(name.replace("-", " ").replace("_", " "))

    rd = raw_data.get("reddit") or {}
    for p in (rd.get("posts") or []):
        if p.get("title"):
            texts.append(p["title"])

    counter: Counter[str] = Counter()
    for t in texts:
        for w in _extract_words(t):
            counter[w] += 1

    # 至少出现 2 次的词视为高频
    top_words = [w for w, c in counter.most_common(max_trending * 3) if c >= 2][:max_trending]

    # 合并固定关键词在前，去重保留顺序
    merged: List[str] = []
    seen = set()
    for kw in fixed + top_words:
        k = kw.strip()
        if not k or k.lower() in seen:
            continue
        seen.add(k.lower())
        merged.append(k)
    return merged


def _collect_all(verbose: bool = True) -> Dict[str, Any]:
    raw_data: Dict[str, Any] = {}

    collectors = [
        ("hackernews", HackerNewsCollector),
        ("github_trending", GitHubTrendingCollector),
        ("producthunt", ProductHuntCollector),
        ("huggingface", HuggingFaceCollector),
        ("reddit", RedditCollector),
    ]

    for name, cls in collectors:
        try:
            if verbose:
                print(f"  Collecting {name}...")
            raw_data[name] = cls().collect()
            if verbose:
                print(f"  ✓ {name} done")
        except Exception as e:
            if verbose:
                print(f"  ✗ {name} failed: {e}")
            raw_data[name] = {"error": str(e), "source": name}

    # Google Trends（依赖其他结果的关键词）
    try:
        if verbose:
            print("  Collecting google_trends...")
        keywords = extract_keywords(raw_data)
        if verbose:
            print(f"    keywords: {keywords}")
        gt = GoogleTrendsCollector()
        raw_data["google_trends"] = gt.collect(keywords)
        if verbose:
            print("  ✓ google_trends done")
    except Exception as e:
        if verbose:
            print(f"  ✗ google_trends failed: {e}")
        raw_data["google_trends"] = {"error": str(e), "source": "google_trends"}

    return raw_data


def main() -> int:
    parser = argparse.ArgumentParser(description="BuildRadar daily pipeline")
    parser.add_argument("--force", action="store_true", help="忽略已存在的今日报告，强制重新生成")
    parser.add_argument("--site-only", action="store_true", help="只重建站点，不采集、不生成")
    parser.add_argument("--skip-llm", action="store_true", help="不调用 LLM，使用本地兜底模板生成报告")
    args = parser.parse_args()

    # 今日日期（北京时间）
    beijing_tz = timezone(timedelta(hours=8))
    today = datetime.now(beijing_tz).strftime("%Y-%m-%d")
    print(f"=== BuildRadar Daily Report: {today} ===")

    if args.site_only:
        print("  [--site-only] Building site from existing reports...")
        build_site()
        print("  ✓ Site built")
        return 0

    if report_exists(today) and not args.force:
        print(f"  Report for {today} already exists; skipping collect & generate (use --force to override).")
        print("  Rebuilding site anyway...")
        build_site()
        print("  ✓ Site built")
        return 0

    # 1. 采集
    raw_data = _collect_all(verbose=True)

    # 2. 保存原始数据
    raw_path = save_raw_data(today, raw_data)
    print(f"  ✓ Raw data saved -> {raw_path}")

    # 3. 信号评分（纯 Python，不依赖 LLM）
    print("  Scoring signals (cross-platform)...")
    signals = score_signals(raw_data, min_grade="B", max_signals=30)
    grade_counts: Dict[str, int] = {}
    for s in signals:
        grade_counts[s["grade"]] = grade_counts.get(s["grade"], 0) + 1
    print(f"  ✓ {len(signals)} signals (B+): " + ", ".join(
        f"{g}={c}" for g, c in sorted(grade_counts.items())
    ))

    # 4. 机会原材料提取
    print("  Extracting opportunity raw materials...")
    opportunities = extract_opportunities(raw_data, signals)
    print("  ✓ opportunities: " + ", ".join(
        f"{k}={len(v)}" for k, v in opportunities.items()
    ))

    # 5. 读取历史信号 → 计算趋势变化 → 上周回顾素材
    history = load_signal_history()
    trends = compute_trends(signals, history, today, lookback_days=7)
    last_week = previous_reports_summary(history, today, lookback_days=7, max_per_day=3)

    # 6. 生成报告
    print("  Generating report...")
    use_llm = not args.skip_llm and bool(get_env("ANTHROPIC_API_KEY"))
    if use_llm:
        try:
            generator = ReportGenerator()
            report_md = generator.generate(
                date=today,
                signals=signals,
                opportunities=opportunities,
                trends=trends,
                last_week=last_week,
            )
        except Exception as e:
            print(f"  ✗ LLM generation failed: {e}")
            print("  Falling back to rule-engine mock report...")
            report_md = build_mock_report(today, signals, opportunities, trends, last_week)
    else:
        if args.skip_llm:
            print("  --skip-llm specified; using rule-engine mock report.")
        else:
            print("  ANTHROPIC_API_KEY not set; using rule-engine mock report.")
        report_md = build_mock_report(today, signals, opportunities, trends, last_week)

    report_path = save_report(today, report_md)
    print(f"  ✓ Report saved -> {report_path}")

    # 7. 更新信号历史（在保存报告之后，确保报告生成成功才追加）
    update_signal_history(today, signals)
    print("  ✓ Signal history updated -> data/signals.json")

    # 8. 构建静态网站
    print("  Building site...")
    build_site()
    print("  ✓ Site built")

    print(f"=== Done! Report: data/reports/{today}.md ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
