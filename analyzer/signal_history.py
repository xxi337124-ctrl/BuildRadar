"""
信号历史追踪：把每天的 scored_signals 持久化到 data/signals.json，
并提供"与最近 7 天比较"的趋势分析工具。

这是"上周回顾"板块的数据来源。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


SIGNALS_PATH = os.path.join("data", "signals.json")

# 等级映射（用于比较升降）
_GRADE_RANK = {"S": 3, "A": 2, "B": 1, "C": 0}


def load_signal_history(path: str = SIGNALS_PATH) -> Dict[str, List[Dict[str, Any]]]:
    """读取累积信号库。文件不存在则返回空 dict。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _signal_summary(signal: Dict[str, Any]) -> Dict[str, Any]:
    """把一条 scored signal 压缩成只保留追踪需要的字段，避免 signals.json 膨胀。"""
    return {
        "signal_id": signal.get("signal_id"),
        "primary_keyword": signal.get("primary_keyword"),
        "keywords": signal.get("keywords", []),
        "platform_count": signal.get("platform_count", 0),
        "grade": signal.get("grade"),
        "type": signal.get("type"),
        "top_score": signal.get("top_score", 0),
    }


def update_signal_history(
    date: str,
    scored_signals: List[Dict[str, Any]],
    path: str = SIGNALS_PATH,
) -> None:
    """把当天的信号追加到历史库中，覆盖同日期的旧数据。"""
    history = load_signal_history(path)
    history[date] = [_signal_summary(s) for s in scored_signals]

    # 只保留最近 90 天，防止无限膨胀
    cutoff = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
    history = {d: v for d, v in history.items() if d >= cutoff}

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def compute_trends(
    today_signals: List[Dict[str, Any]],
    history: Dict[str, List[Dict[str, Any]]],
    today: str,
    lookback_days: int = 7,
) -> List[Dict[str, Any]]:
    """
    比较今天的信号和过去 lookback_days 天，标注趋势变化。
    对每条今日信号返回：
      {
        "signal_id": ...,
        "primary_keyword": ...,
        "grade_today": "A",
        "trend": "rising" / "cooling" / "persistent" / "new",
        "appeared_in_last_n_days": 3,
        "previous_best_grade": "B"  # 过去最高等级
      }
    """
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    window = [
        (today_dt - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(1, lookback_days + 1)
    ]

    trends: List[Dict[str, Any]] = []
    for sig in today_signals:
        key = (sig.get("primary_keyword") or "").lower()
        if not key:
            continue
        appearances = 0
        prev_best = "C"
        for d in window:
            day_sigs = history.get(d, [])
            for ds in day_sigs:
                if (ds.get("primary_keyword") or "").lower() == key:
                    appearances += 1
                    if _GRADE_RANK.get(ds.get("grade", "C"), 0) > _GRADE_RANK.get(prev_best, 0):
                        prev_best = ds.get("grade", "C")
                    break

        today_rank = _GRADE_RANK.get(sig.get("grade", "C"), 0)
        prev_rank = _GRADE_RANK.get(prev_best, 0)

        if appearances == 0:
            trend = "new"
        elif today_rank > prev_rank:
            trend = "rising"
        elif today_rank < prev_rank:
            trend = "cooling"
        elif appearances >= 3:
            trend = "persistent"
        else:
            trend = "steady"

        trends.append({
            "signal_id": sig.get("signal_id"),
            "primary_keyword": sig.get("primary_keyword"),
            "grade_today": sig.get("grade"),
            "trend": trend,
            "appeared_in_last_n_days": appearances,
            "previous_best_grade": prev_best,
        })
    return trends


def previous_reports_summary(
    history: Dict[str, List[Dict[str, Any]]],
    today: str,
    lookback_days: int = 7,
    max_per_day: int = 3,
) -> List[Dict[str, Any]]:
    """
    为"上周回顾"板块提供素材：最近 lookback_days 天每天的 top N 信号概要。
    """
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    out: List[Dict[str, Any]] = []
    for i in range(1, lookback_days + 1):
        d = (today_dt - timedelta(days=i)).strftime("%Y-%m-%d")
        day_sigs = history.get(d, [])
        if not day_sigs:
            continue
        # 只取 A 级及以上
        top = [s for s in day_sigs if _GRADE_RANK.get(s.get("grade", "C"), 0) >= 2]
        top = top[:max_per_day]
        if top:
            out.append({"date": d, "signals": top})
    return out


__all__ = [
    "load_signal_history",
    "update_signal_history",
    "compute_trends",
    "previous_reports_summary",
]
