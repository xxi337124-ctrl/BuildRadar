"""统一的配置加载工具。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"


@lru_cache(maxsize=1)
def load_config() -> Dict[str, Any]:
    """加载并缓存 config.yaml。"""
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_env(name: str, default: str | None = None) -> str | None:
    """从环境变量读取，兼容空字符串。"""
    val = os.environ.get(name, default)
    if val is not None and val.strip() == "":
        return None
    return val
