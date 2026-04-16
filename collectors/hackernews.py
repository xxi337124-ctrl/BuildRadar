"""Hacker News 采集器（使用 Algolia HN Search API）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

from config_loader import load_config

ALGOLIA_BASE = "https://hn.algolia.com/api/v1/search"


class HackerNewsCollector:
    """采集 HN front_page、Show HN、Ask HN。"""

    def __init__(self) -> None:
        cfg = load_config()["collectors"]["hackernews"]
        self.top_count = int(cfg.get("top_stories_count", 30))
        self.min_points = int(cfg.get("min_points", 50))
        self.min_comments = int(cfg.get("min_comments", 10))
        self.show_hn_count = int(cfg.get("show_hn_count", 20))
        self.ask_hn_count = int(cfg.get("ask_hn_count", 10))

    def _fetch(self, client: httpx.Client, tag: str, hits: int) -> List[Dict[str, Any]]:
        resp = client.get(
            ALGOLIA_BASE,
            params={"tags": tag, "hitsPerPage": hits},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json().get("hits", [])

    @staticmethod
    def _normalize(hit: Dict[str, Any]) -> Dict[str, Any]:
        object_id = hit.get("objectID")
        return {
            "title": hit.get("title") or hit.get("story_title") or "",
            "url": hit.get("url") or hit.get("story_url") or f"https://news.ycombinator.com/item?id={object_id}",
            "points": hit.get("points") or 0,
            "num_comments": hit.get("num_comments") or 0,
            "author": hit.get("author"),
            "created_at": hit.get("created_at"),
            "hn_url": f"https://news.ycombinator.com/item?id={object_id}",
        }

    def collect(self) -> Dict[str, Any]:
        beijing_tz = timezone(timedelta(hours=8))
        collected_at = datetime.now(beijing_tz).isoformat()

        with httpx.Client(
            headers={"User-Agent": "BuildRadar/1.0"},
            follow_redirects=True,
        ) as client:
            # front_page
            front_hits = self._fetch(client, "front_page", self.top_count)
            front_page = [
                self._normalize(h) for h in front_hits
                if (h.get("points") or 0) >= self.min_points
                and (h.get("num_comments") or 0) >= self.min_comments
            ]

            # Show HN
            show_hits = self._fetch(client, "show_hn", self.show_hn_count)
            show_hn = [self._normalize(h) for h in show_hits]

            # Ask HN
            ask_hits = self._fetch(client, "ask_hn", self.ask_hn_count)
            ask_hn = [self._normalize(h) for h in ask_hits]

        # 排序：按 points 降序
        front_page.sort(key=lambda x: x["points"], reverse=True)
        show_hn.sort(key=lambda x: x["points"], reverse=True)
        ask_hn.sort(key=lambda x: x["points"], reverse=True)

        return {
            "source": "hackernews",
            "collected_at": collected_at,
            "front_page": front_page,
            "show_hn": show_hn,
            "ask_hn": ask_hn,
        }
