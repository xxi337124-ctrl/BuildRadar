"""Product Hunt 采集器（GraphQL API v2）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

from config_loader import get_env, load_config

PH_GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"


QUERY_TEMPLATE = """
{
  posts(order: VOTES, first: %d) {
    edges {
      node {
        id
        name
        tagline
        description
        votesCount
        commentsCount
        url
        website
        createdAt
        topics {
          edges {
            node {
              name
            }
          }
        }
      }
    }
  }
}
"""


class ProductHuntCollector:
    """采集 Product Hunt 当日热门产品。"""

    def __init__(self) -> None:
        cfg = load_config()["collectors"]["producthunt"]
        self.posts_count = int(cfg.get("posts_count", 20))
        self.token = get_env("PH_TOKEN")

    def collect(self) -> Dict[str, Any]:
        beijing_tz = timezone(timedelta(hours=8))
        collected_at = datetime.now(beijing_tz).isoformat()

        if not self.token:
            return {
                "source": "producthunt",
                "collected_at": collected_at,
                "skipped": True,
                "reason": "PH_TOKEN not set",
                "products": [],
            }

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "BuildRadar/1.0",
        }
        query = QUERY_TEMPLATE % self.posts_count

        with httpx.Client(headers=headers, timeout=30.0) as client:
            resp = client.post(PH_GRAPHQL_URL, json={"query": query})
            resp.raise_for_status()
            data = resp.json()

        edges = (((data.get("data") or {}).get("posts") or {}).get("edges") or [])

        products: List[Dict[str, Any]] = []
        for edge in edges:
            node = edge.get("node") or {}
            topics_list = [
                (t.get("node") or {}).get("name")
                for t in ((node.get("topics") or {}).get("edges") or [])
                if (t.get("node") or {}).get("name")
            ]
            products.append({
                "name": node.get("name"),
                "tagline": node.get("tagline"),
                "description": (node.get("description") or "")[:500],
                "votes_count": node.get("votesCount") or 0,
                "comments_count": node.get("commentsCount") or 0,
                "url": node.get("url"),
                "website": node.get("website"),
                "topics": topics_list,
                "created_at": node.get("createdAt"),
            })

        products.sort(key=lambda x: x["votes_count"], reverse=True)

        return {
            "source": "producthunt",
            "collected_at": collected_at,
            "products": products,
        }
