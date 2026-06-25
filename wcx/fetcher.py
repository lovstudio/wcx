"""WeChat appmsg API client.

Uses the `cgi-bin/appmsg?action=list_ex` endpoint (same as wechat-article-exporter).
Requires the user to be logged into their own WeChat Official Account backend
and provide the `token` and cookie from that session.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Iterator

from curl_cffi import requests as cffi_requests

BASE = "https://mp.weixin.qq.com"
SEARCH_BIZ_URL = f"{BASE}/cgi-bin/searchbiz"
APPMSG_URL = f"{BASE}/cgi-bin/appmsg"

DEFAULT_HEADERS = {
    "Referer": f"{BASE}/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


class WCXError(Exception):
    """Base exception."""


class AuthError(WCXError):
    """Token/cookie invalid or expired."""


class RateLimitError(WCXError):
    """Hit WeChat frequency control (ret=200013)."""


class NotFoundError(WCXError):
    """No matching account."""


@dataclass
class Account:
    fakeid: str
    nickname: str
    alias: str = ""
    signature: str = ""
    round_head_img: str = ""

    def to_dict(self) -> dict:
        return {
            "fakeid": self.fakeid,
            "nickname": self.nickname,
            "alias": self.alias,
            "signature": self.signature,
            "round_head_img": self.round_head_img,
        }


@dataclass
class ArticleMeta:
    aid: str
    fakeid: str
    title: str
    link: str
    digest: str = ""
    cover: str = ""
    author: str = ""
    create_time: int = 0
    update_time: int = 0

    def to_dict(self) -> dict:
        return {
            "aid": self.aid,
            "fakeid": self.fakeid,
            "title": self.title,
            "link": self.link,
            "digest": self.digest,
            "cover": self.cover,
            "author": self.author,
            "create_time": self.create_time,
            "update_time": self.update_time,
        }


class Fetcher:
    def __init__(self, token: str, cookie: str, *, impersonate: str = "chrome120"):
        self.token = token
        self.cookie = cookie
        self.impersonate = impersonate
        self._session = cffi_requests.Session(
            impersonate=impersonate,
            headers={**DEFAULT_HEADERS, "Cookie": cookie},
        )

    def _get(self, url: str, params: dict) -> dict:
        resp = self._session.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            raise WCXError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        ret = data.get("base_resp", {}).get("ret", 0)
        if ret == 0:
            return data
        msg = data.get("base_resp", {}).get("err_msg", "")
        if ret == 200013:
            raise RateLimitError(f"Rate limited (ret=200013): {msg}. Wait >= 1 hour.")
        if ret in (200003, 200002, 200008):
            raise AuthError(f"Auth failed (ret={ret}): {msg}. Re-login needed.")
        raise WCXError(f"API error ret={ret}: {msg}")

    def search_biz(self, query: str, *, begin: int = 0, count: int = 5) -> list[Account]:
        """Search accounts by name. Returns candidate list."""
        data = self._get(
            SEARCH_BIZ_URL,
            params={
                "action": "search_biz",
                "begin": begin,
                "count": count,
                "query": query,
                "token": self.token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
            },
        )
        accounts = []
        for item in data.get("list", []):
            accounts.append(
                Account(
                    fakeid=item.get("fakeid", ""),
                    nickname=item.get("nickname", ""),
                    alias=item.get("alias", ""),
                    signature=item.get("signature", ""),
                    round_head_img=item.get("round_head_img", ""),
                )
            )
        return accounts

    def resolve(self, query: str) -> Account:
        """Resolve name/fakeid to an account. Prefers exact nickname match."""
        results = self.search_biz(query)
        if not results:
            raise NotFoundError(f"No account found for: {query}")
        # exact match wins
        for acc in results:
            if acc.nickname == query or acc.alias == query or acc.fakeid == query:
                return acc
        return results[0]

    def list_articles(
        self,
        fakeid: str,
        *,
        begin: int = 0,
        count: int = 5,
    ) -> tuple[list[ArticleMeta], int]:
        """Fetch one page. Returns (articles, total)."""
        data = self._get(
            APPMSG_URL,
            params={
                "action": "list_ex",
                "begin": begin,
                "count": count,
                "fakeid": fakeid,
                "type": 9,
                "query": "",
                "token": self.token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
            },
        )
        total = int(data.get("app_msg_cnt", 0))
        articles: list[ArticleMeta] = []
        for item in data.get("app_msg_list", []):
            articles.append(
                ArticleMeta(
                    aid=str(item.get("aid") or item.get("app_msg_id") or item.get("appmsgid", "")),
                    fakeid=fakeid,
                    title=item.get("title", ""),
                    link=item.get("link", ""),
                    digest=item.get("digest", ""),
                    cover=item.get("cover", ""),
                    author=item.get("author_name") or item.get("author", ""),
                    create_time=int(item.get("create_time", 0)),
                    update_time=int(item.get("update_time", 0)),
                )
            )
        return articles, total

    def iter_all_articles(
        self,
        fakeid: str,
        *,
        max_items: int | None = None,
        page_size: int = 5,
        min_delay: float = 5.0,
        max_delay: float = 15.0,
        start_begin: int = 0,
        on_page: Callable[[int, int, int], None] | None = None,
    ) -> Iterator[ArticleMeta]:
        """Paginate through articles with polite delay.

        start_begin: server-side offset to start from (0 = newest).
        max_items: stop after this many yielded items (None = until total).
        on_page(begin, fetched_this_page, total) called after each page.
        """
        begin = start_begin
        yielded = 0
        seen_aids: set[str] = set()
        while True:
            articles, total = self.list_articles(fakeid, begin=begin, count=page_size)
            if on_page:
                on_page(begin, len(articles), total)
            if not articles:
                break
            for art in articles:
                if art.aid in seen_aids:
                    continue
                seen_aids.add(art.aid)
                yield art
                yielded += 1
                if max_items and yielded >= max_items:
                    return
            begin += page_size
            if begin >= total:
                break
            time.sleep(random.uniform(min_delay, max_delay))
