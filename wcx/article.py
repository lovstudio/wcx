"""Fetch & parse article HTML from mp.weixin.qq.com public article URLs."""
from __future__ import annotations

import ast
import html as html_lib
import re

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from markdownify import markdownify


def fetch_article_html(url: str, *, impersonate: str = "chrome120") -> str:
    """Fetch the public article page HTML (no auth needed for public articles).

    WeChat returns a 301 on http:// that curl_cffi's default impersonated session
    mishandles as 501. Normalize to https:// and enable redirect following.
    """
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    resp = cffi_requests.get(url, impersonate=impersonate, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    return resp.text


def extract_content(html: str) -> tuple[str, str]:
    """Extract the article content div. Returns (inner_html, markdown)."""
    soup = BeautifulSoup(html, "lxml")
    content = soup.find("div", id="js_content") or soup.find("div", class_="rich_media_content")
    if content:
        # WeChat hides content via CSS visibility:hidden until JS runs — strip that
        if content.has_attr("style"):
            del content["style"]
        inner_html = str(content)
        md = markdownify(inner_html, heading_style="ATX", strip=["script", "style"])
        md = re.sub(r"\n{3,}", "\n\n", md).strip()
        return inner_html, md

    return extract_text_content(html)


def extract_text_content(html: str) -> tuple[str, str]:
    """Extract text-only share pages rendered from cgiDataNew.content_noencode."""
    match = re.search(r"content_noencode\s*[:=]\s*([\"'])(.*?)\1", html, re.S)
    if not match:
        return "", ""

    try:
        text = ast.literal_eval(f"{match.group(1)}{match.group(2)}{match.group(1)}")
    except (SyntaxError, ValueError):
        text = match.group(2)

    text = html_lib.unescape(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return "", ""

    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    inner_html = (
        '<div id="js_content">'
        + "".join(f"<p>{html_lib.escape(part).replace(chr(10), '<br/>')}</p>" for part in paragraphs)
        + "</div>"
    )
    md = re.sub(r"\n{3,}", "\n\n", text).strip()
    return inner_html, md


def extract_meta(html: str) -> dict:
    """Extract title/author/publish_time from article page."""
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.find("h1", class_="rich_media_title") or soup.find("h2", class_="rich_media_title")
    title = title_el.get_text(strip=True) if title_el else ""
    author_el = soup.find("a", id="js_name") or soup.find("span", class_="rich_media_meta_text")
    author = author_el.get_text(strip=True) if author_el else ""
    return {"title": title, "author": author}
