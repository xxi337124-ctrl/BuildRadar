"""HuggingFace Trending 采集器（优先使用 JSON API）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

from config_loader import load_config

HF_API_URL = "https://huggingface.co/api/models"


class HuggingFaceCollector:
    """采集 HuggingFace trending 模型。"""

    def __init__(self) -> None:
        cfg = load_config()["collectors"]["huggingface"]
        self.top_count = int(cfg.get("top_count", 20))

    def collect(self) -> Dict[str, Any]:
        beijing_tz = timezone(timedelta(hours=8))
        collected_at = datetime.now(beijing_tz).isoformat()

        headers = {
            "User-Agent": "BuildRadar/1.0",
            "Accept": "application/json",
        }

        # HuggingFace 官方 API 的 `sort` 参数支持: likes7d / likes30d / likes / downloads / createdAt / trendingScore。
        # 试多种 sort 值以最大化拿到"热门"数据。
        sort_candidates = ["likes7d", "trendingScore", "likes"]

        data = None
        last_exc: Exception | None = None
        with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
            for sort_key in sort_candidates:
                try:
                    resp = client.get(
                        HF_API_URL,
                        params={"sort": sort_key, "limit": self.top_count},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        break
                except Exception as e:
                    last_exc = e
                    continue

        if data is None:
            if last_exc:
                raise last_exc
            raise RuntimeError("HuggingFace API returned no usable response")

        models: List[Dict[str, Any]] = []
        for item in data:
            model_id = item.get("modelId") or item.get("id") or ""
            if not model_id:
                continue
            if "/" in model_id:
                author = model_id.split("/", 1)[0]
            else:
                author = item.get("author") or ""
            models.append({
                "model_name": model_id,
                "author": author,
                "pipeline_tag": item.get("pipeline_tag"),
                "likes": item.get("likes") or 0,
                "downloads": item.get("downloads") or 0,
                "trending_score": item.get("trendingScore") or 0,
                "last_modified": item.get("lastModified"),
                "model_url": f"https://huggingface.co/{model_id}",
            })

        # 按 likes 降序（API 已按 trending 排序，保留原顺序更合理；此处额外加一个备用 key）
        return {
            "source": "huggingface",
            "collected_at": collected_at,
            "models": models,
        }
