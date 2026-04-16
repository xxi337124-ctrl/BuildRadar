"""Google Trends 采集器（通过 SerpApi）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

from config_loader import get_env

SERPAPI_URL = "https://serpapi.com/search.json"


class GoogleTrendsCollector:
    """通过 SerpApi 调用 Google Trends。"""

    def __init__(self) -> None:
        self.api_key = get_env("SERPAPI_KEY")

    def _fetch_keyword(self, client: httpx.Client, keyword: str) -> Dict[str, Any] | None:
        params = {
            "engine": "google_trends",
            "q": keyword,
            "date": "now 7-d",
            "data_type": "TIMESERIES",
            "api_key": self.api_key,
        }
        try:
            resp = client.get(SERPAPI_URL, params=params, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return {
                "keyword": keyword,
                "error": str(e),
            }

        timeline = (data.get("interest_over_time") or {}).get("timeline_data") or []
        values: List[int] = []
        for point in timeline:
            vals = point.get("values") or []
            if vals:
                v = vals[0].get("extracted_value")
                if isinstance(v, (int, float)):
                    values.append(int(v))

        if not values:
            return {
                "keyword": keyword,
                "trend_direction": "unknown",
                "peak_value": 0,
                "current_value": 0,
                "change_percent": "N/A",
            }

        peak = max(values)
        current = values[-1]
        # 用前 1/4 的平均值 vs 最后 1/4 的平均值判断趋势
        n = len(values)
        head = values[: max(1, n // 4)]
        tail = values[-max(1, n // 4):]
        head_avg = sum(head) / len(head)
        tail_avg = sum(tail) / len(tail)

        if head_avg == 0:
            change_percent = "+∞%" if tail_avg > 0 else "0%"
            direction = "rising" if tail_avg > 0 else "flat"
        else:
            pct = (tail_avg - head_avg) / head_avg * 100
            change_percent = f"{pct:+.0f}%"
            if pct > 15:
                direction = "rising"
            elif pct < -15:
                direction = "falling"
            else:
                direction = "flat"

        return {
            "keyword": keyword,
            "trend_direction": direction,
            "peak_value": peak,
            "current_value": current,
            "change_percent": change_percent,
        }

    def collect(self, keywords: List[str]) -> Dict[str, Any]:
        beijing_tz = timezone(timedelta(hours=8))
        collected_at = datetime.now(beijing_tz).isoformat()

        if not self.api_key:
            return {
                "source": "google_trends",
                "collected_at": collected_at,
                "skipped": True,
                "reason": "SERPAPI_KEY not set",
                "trends": [],
            }

        results: List[Dict[str, Any]] = []
        # 去重并保留顺序
        seen = set()
        uniq_keywords = []
        for kw in keywords:
            k = (kw or "").strip()
            if not k or k.lower() in seen:
                continue
            seen.add(k.lower())
            uniq_keywords.append(k)

        with httpx.Client(headers={"User-Agent": "BuildRadar/1.0"}) as client:
            for kw in uniq_keywords:
                r = self._fetch_keyword(client, kw)
                if r is not None:
                    results.append(r)

        return {
            "source": "google_trends",
            "collected_at": collected_at,
            "trends": results,
        }
