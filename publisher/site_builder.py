"""用 Jinja2 + markdown 生成静态 HTML 网站。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Dict, List

import markdown as md_lib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from config_loader import load_config

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
REPORTS_DIR = ROOT / "data" / "reports"
SITE_DIR = ROOT / "site"


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_frontmatter(md_text: str) -> tuple[Dict[str, str], str]:
    """简单解析 YAML frontmatter（只支持 key: value 结构）。"""
    m = FRONTMATTER_RE.match(md_text)
    if not m:
        return {}, md_text
    fm_text, body = m.group(1), m.group(2)
    fm: Dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, body


def _make_summary(body_html: str, max_chars: int = 220) -> str:
    """从 HTML 中抽取第一段纯文本作为摘要。"""
    # 粗粒度去标签
    text = re.sub(r"<[^>]+>", " ", body_html)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def _render_markdown(body: str) -> str:
    return md_lib.markdown(
        body,
        extensions=["extra", "tables", "fenced_code", "toc", "sane_lists"],
        output_format="html5",
    )


def _load_reports() -> List[Dict[str, Any]]:
    """读取 data/reports/ 下所有 .md 文件并转换为 HTML。"""
    reports: List[Dict[str, Any]] = []
    if not REPORTS_DIR.exists():
        return reports
    for path in sorted(REPORTS_DIR.glob("*.md"), reverse=True):
        text = path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        date_str = fm.get("date") or path.stem
        title = fm.get("title") or f"BuildRadar Daily — {date_str}"
        body_html = _render_markdown(body)
        summary = _make_summary(body_html)
        reports.append({
            "date": date_str,
            "title": title,
            "body_html": body_html,
            "summary": summary,
            "slug": path.stem,
            "filename": f"reports/{path.stem}.html",
        })
    # 按 date 倒序
    reports.sort(key=lambda r: r["date"], reverse=True)
    return reports


def build_site() -> None:
    """构建完整的静态站点到 site/ 目录。"""
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "reports").mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    config = load_config()
    site_ctx = config.get("site", {})

    reports = _load_reports()

    # 渲染每篇报告
    report_tpl = env.get_template("report.html")
    for i, r in enumerate(reports):
        prev_report = reports[i + 1] if i + 1 < len(reports) else None
        next_report = reports[i - 1] if i - 1 >= 0 else None
        html = report_tpl.render(
            site=site_ctx,
            report=r,
            prev_report=prev_report,
            next_report=next_report,
        )
        out_path = SITE_DIR / "reports" / f"{r['slug']}.html"
        out_path.write_text(html, encoding="utf-8")

    # 渲染首页
    index_tpl = env.get_template("index.html")
    index_html = index_tpl.render(site=site_ctx, reports=reports)
    (SITE_DIR / "index.html").write_text(index_html, encoding="utf-8")

    # 渲染订阅页
    try:
        sub_tpl = env.get_template("subscribe.html")
        sub_html = sub_tpl.render(site=site_ctx)
        (SITE_DIR / "subscribe.html").write_text(sub_html, encoding="utf-8")
    except Exception:
        pass

    # 复制 .nojekyll 以保证 GitHub Pages 不做 Jekyll 处理
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
