"""Markdown / 原始数据保存工具。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_REPORTS = ROOT / "data" / "reports"


def save_raw_data(date: str, raw_data: Dict[str, Any]) -> str:
    """将原始采集数据保存为 JSON。"""
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    path = DATA_RAW / f"{date}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)
    return str(path)


def save_report(date: str, markdown: str) -> str:
    """将报告 Markdown 保存到 data/reports/{date}.md，自动添加 YAML frontmatter。"""
    DATA_REPORTS.mkdir(parents=True, exist_ok=True)
    path = DATA_REPORTS / f"{date}.md"

    # 如果 markdown 本身没有 frontmatter，就自动添加
    content = markdown.strip()
    if not content.startswith("---"):
        frontmatter = (
            "---\n"
            f'title: "BuildRadar Daily — {date}"\n'
            f'date: "{date}"\n'
            "---\n\n"
        )
        content = frontmatter + content + "\n"
    else:
        content = content + "\n"

    with path.open("w", encoding="utf-8") as f:
        f.write(content)
    return str(path)


def report_exists(date: str) -> bool:
    return (DATA_REPORTS / f"{date}.md").exists()
