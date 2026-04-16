"""GitHub Trending 采集器（爬 HTML 页面）。"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx
from bs4 import BeautifulSoup

from config_loader import load_config

TRENDING_URL = "https://github.com/trending/{lang}"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class GitHubTrendingCollector:
    """采集 GitHub Trending 多个语言的仓库。"""

    def __init__(self) -> None:
        cfg = load_config()["collectors"]["github"]
        self.languages: List[str] = cfg.get("languages", [""])
        self.since: str = cfg.get("since", "daily")
        self.interval = float(cfg.get("request_interval_seconds", 2))

    @staticmethod
    def _clean_text(el) -> str:
        if el is None:
            return ""
        return re.sub(r"\s+", " ", el.get_text(strip=True)).strip()

    @staticmethod
    def _parse_number(txt: str) -> int:
        if not txt:
            return 0
        t = txt.replace(",", "").strip().lower()
        try:
            if t.endswith("k"):
                return int(float(t[:-1]) * 1000)
            if t.endswith("m"):
                return int(float(t[:-1]) * 1_000_000)
            return int(re.sub(r"[^0-9]", "", t) or 0)
        except ValueError:
            return 0

    def _parse_page(self, html: str, language_query: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        repos: List[Dict[str, Any]] = []

        for article in soup.select("article.Box-row"):
            # repo name: <h2><a href="/owner/name">
            a_tag = article.select_one("h2 a")
            if not a_tag:
                continue
            href = a_tag.get("href", "").strip()
            repo_name = href.lstrip("/").strip()
            if not repo_name:
                continue

            desc_tag = article.select_one("p")
            description = self._clean_text(desc_tag)

            # 语言
            lang_tag = article.select_one('span[itemprop="programmingLanguage"]')
            language = self._clean_text(lang_tag) or language_query or ""

            # stars_today：<span class="d-inline-block float-sm-right">... stars today</span>
            total_stars = 0
            forks = 0
            # 总星数链接：href 以 /stargazers 结尾
            for a in article.select("a.Link--muted"):
                href2 = a.get("href", "")
                num_txt = self._clean_text(a)
                num = self._parse_number(num_txt)
                if href2.endswith("/stargazers"):
                    total_stars = num
                elif href2.endswith("/forks"):
                    forks = num

            stars_today = 0
            today_tag = article.select_one("span.d-inline-block.float-sm-right")
            if today_tag:
                m = re.search(r"([\d,]+)\s+stars?\s+today", self._clean_text(today_tag), re.I)
                if m:
                    stars_today = self._parse_number(m.group(1))
                else:
                    # 也可能是 "this week"
                    m2 = re.search(r"([\d,]+)\s+stars?\s+(today|this week|this month)", self._clean_text(today_tag), re.I)
                    if m2:
                        stars_today = self._parse_number(m2.group(1))

            repos.append({
                "repo_name": repo_name,
                "description": description,
                "language": language,
                "stars_today": stars_today,
                "total_stars": total_stars,
                "forks": forks,
                "repo_url": f"https://github.com/{repo_name}",
            })

        return repos

    def collect(self) -> Dict[str, Any]:
        beijing_tz = timezone(timedelta(hours=8))
        collected_at = datetime.now(beijing_tz).isoformat()

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }

        all_repos: List[Dict[str, Any]] = []
        seen = set()

        with httpx.Client(headers=headers, follow_redirects=True, timeout=30.0) as client:
            for idx, lang in enumerate(self.languages):
                url = TRENDING_URL.format(lang=lang)
                params = {"since": self.since}
                try:
                    resp = client.get(url, params=params)
                    if resp.status_code != 200:
                        continue
                    parsed = self._parse_page(resp.text, lang)
                    for r in parsed:
                        if r["repo_name"] in seen:
                            continue
                        seen.add(r["repo_name"])
                        all_repos.append(r)
                except Exception:
                    # 单个语言失败，继续其他
                    continue

                # 限流（不在最后一个后等待）
                if idx < len(self.languages) - 1:
                    time.sleep(self.interval)

        # 按 stars_today 降序
        all_repos.sort(key=lambda x: x["stars_today"], reverse=True)

        return {
            "source": "github_trending",
            "collected_at": collected_at,
            "repositories": all_repos,
        }
