"""Reddit 采集器（公开 JSON 端点，无 OAuth）。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

from config_loader import load_config

REDDIT_ENDPOINTS = [
    "https://www.reddit.com/r/{sub}/hot.json",
    "https://old.reddit.com/r/{sub}/hot.json",
]

# Reddit 对无 UA 或普通 python-requests 类 UA 严格，使用浏览器型 UA 最稳
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class RedditCollector:
    """采集多个 subreddit 的 hot 帖。"""

    def __init__(self) -> None:
        cfg = load_config()["collectors"]["reddit"]
        self.subreddits: List[str] = cfg.get("subreddits", [])
        self.posts_per_sub = int(cfg.get("posts_per_subreddit", 10))
        self.min_score = int(cfg.get("min_score", 20))
        self.interval = float(cfg.get("request_interval_seconds", 3))

    def _fetch_sub(self, client: httpx.Client, sub: str) -> List[Dict[str, Any]]:
        last_status = None
        for tpl in REDDIT_ENDPOINTS:
            try:
                resp = client.get(
                    tpl.format(sub=sub),
                    params={"limit": self.posts_per_sub, "raw_json": 1},
                    timeout=30.0,
                )
                last_status = resp.status_code
                if resp.status_code != 200:
                    continue
                data = resp.json()
                posts: List[Dict[str, Any]] = []
                for child in (data.get("data") or {}).get("children") or []:
                    d = child.get("data") or {}
                    score = d.get("score") or 0
                    if score < self.min_score:
                        continue
                    if d.get("stickied"):
                        continue
                    selftext = d.get("selftext") or ""
                    permalink = d.get("permalink") or ""
                    posts.append({
                        "title": d.get("title") or "",
                        "selftext": selftext[:500],
                        "score": score,
                        "num_comments": d.get("num_comments") or 0,
                        "subreddit": d.get("subreddit") or sub,
                        "permalink": permalink,
                        "url": d.get("url") or f"https://www.reddit.com{permalink}",
                        "created_utc": d.get("created_utc"),
                    })
                return posts
            except Exception:
                continue
        # 所有端点都失败
        return []

    def collect(self) -> Dict[str, Any]:
        beijing_tz = timezone(timedelta(hours=8))
        collected_at = datetime.now(beijing_tz).isoformat()

        headers = {
            "User-Agent": BROWSER_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        all_posts: List[Dict[str, Any]] = []
        failed_subs: List[str] = []
        with httpx.Client(headers=headers, follow_redirects=True) as client:
            for idx, sub in enumerate(self.subreddits):
                try:
                    posts = self._fetch_sub(client, sub)
                    if posts:
                        all_posts.extend(posts)
                    else:
                        failed_subs.append(sub)
                except Exception:
                    failed_subs.append(sub)
                if idx < len(self.subreddits) - 1:
                    time.sleep(self.interval)

        all_posts.sort(key=lambda x: x["score"], reverse=True)

        result: Dict[str, Any] = {
            "source": "reddit",
            "collected_at": collected_at,
            "posts": all_posts,
        }
        if failed_subs:
            result["failed_subreddits"] = failed_subs
        return result
