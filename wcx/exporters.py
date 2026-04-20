"""Export cached articles to files."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path


def _safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r"[/\\:*?\"<>|\n\r\t]", "_", name).strip()
    return name[:max_len] or "untitled"


def _ts(ts: int) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def export_index(
    rows: list[sqlite3.Row],
    account: sqlite3.Row | dict,
    out_dir: Path,
    *,
    fmt: str = "all",
) -> Path:
    """Write an index of all articles in md + json + csv.

    fmt: all (default) | md | json | csv — kept for backward compat; `all` emits all three.
    Returns the primary path (index.md when available, else the single format written).
    """
    import csv

    out_dir.mkdir(parents=True, exist_ok=True)
    nickname = account["nickname"] if isinstance(account, sqlite3.Row) else account.get("nickname", "")
    exported_at = datetime.now().isoformat(timespec="seconds")
    formats = {"md", "json", "csv"} if fmt == "all" else {fmt}
    primary: Path | None = None

    if "json" in formats:
        path = out_dir / "index.json"
        payload = {
            "account": dict(account) if isinstance(account, sqlite3.Row) else account,
            "articles": [dict(r) for r in rows],
            "exported_at": exported_at,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        primary = primary or path

    if "csv" in formats:
        path = out_dir / "index.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "title", "link", "author", "digest"])
            for r in rows:
                writer.writerow(
                    [_ts(r["create_time"]), r["title"], r["link"], r["author"] or "", r["digest"] or ""]
                )
        primary = primary or path

    if "md" in formats:
        path = out_dir / "index.md"
        lines = [
            f"# {nickname} — 文章索引",
            "",
            f"- 共 **{len(rows)}** 篇",
            f"- 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "- 结构化数据：[index.json](./index.json) · [index.csv](./index.csv)",
            "",
            "| 日期 | 标题 | 作者 |",
            "|------|------|------|",
        ]
        for r in rows:
            title = re.sub(r"\s+", " ", r["title"]).replace("|", "\\|").strip()
            author = re.sub(r"\s+", " ", r["author"] or "").strip()
            lines.append(f"| {_ts(r['create_time'])} | [{title}]({r['link']}) | {author} |")
        path.write_text("\n".join(lines), encoding="utf-8")
        primary = path

    assert primary is not None, f"unknown fmt: {fmt}"
    return primary


def export_article_markdown(row: sqlite3.Row, out_dir: Path) -> Path:
    """Write one article as a Markdown file with frontmatter."""
    out_dir.mkdir(parents=True, exist_ok=True)
    date = _ts(row["create_time"])
    filename = f"{date}_{_safe_filename(row['title'])}.md"
    path = out_dir / filename

    md = row["content_md"] or f"*（正文尚未抓取，原链接：{row['link']}）*"
    frontmatter = [
        "---",
        f"title: {json.dumps(row['title'], ensure_ascii=False)}",
        f"date: {date}",
        f"author: {row['author'] or ''}",
        f"link: {row['link']}",
        f"digest: {json.dumps(row['digest'] or '', ensure_ascii=False)}",
        "---",
        "",
        md,
    ]
    path.write_text("\n".join(frontmatter), encoding="utf-8")
    return path


def export_article_html(row: sqlite3.Row, out_dir: Path) -> Path:
    """Write one article as a standalone HTML file (WeChat styling preserved)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    date = _ts(row["create_time"])
    filename = f"{date}_{_safe_filename(row['title'])}.html"
    path = out_dir / filename

    content = row["content_html"] or f"<p><em>（正文尚未抓取）</em></p>"
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{row['title']}</title>
<style>
  body {{ max-width: 720px; margin: 2rem auto; padding: 0 1rem; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; line-height: 1.7; color: #222; }}
  .meta {{ color: #888; font-size: 0.9rem; border-bottom: 1px solid #eee; padding-bottom: 0.8rem; margin-bottom: 1.2rem; }}
  img {{ max-width: 100%; height: auto; }}
  h1 {{ font-size: 1.6rem; line-height: 1.4; }}
  a {{ color: #CC785C; }}
</style>
</head>
<body>
  <h1>{row['title']}</h1>
  <div class="meta">{row['author'] or ''} · {date} · <a href="{row['link']}">原文</a></div>
  {content}
</body>
</html>"""
    path.write_text(html, encoding="utf-8")
    return path
