"""wcx CLI — the main user entry point.

Commands:
    wcx login              Set up token & cookie interactively
    wcx search <query>     Search accounts by name, pick one
    wcx list <query>       List articles (cached or fresh)
    wcx fetch <query>      Fetch full article list + contents
    wcx export <query>     Export cached articles to MD/HTML/JSON
    wcx status             Show config & cache stats
    wcx logout             Clear credentials
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import article as article_mod
from . import cache, config, fetcher

app = typer.Typer(
    help="wcx — 微信公众号文章抓取工具（本地 CLI，尊重频控）",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err = Console(stderr=True, style="red")


def _version_callback(value: bool):
    if value:
        from . import __version__
        console.print(f"wcx {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback,
        is_eager=True, help="显示版本并退出。",
    ),
):
    """wcx — 微信公众号文章抓取工具。"""
    pass


def _get_fetcher() -> fetcher.Fetcher:
    creds = config.load_credentials()
    if not creds:
        err.print("[red]✗[/] 尚未登录。运行 [cyan]wcx login[/] 设置 token/cookie。")
        raise typer.Exit(1)
    return fetcher.Fetcher(creds.token, creds.cookie)


def _friendly_error(e: Exception) -> None:
    if isinstance(e, fetcher.AuthError):
        err.print(f"[red]✗ 认证失败：[/]{e}\n\n运行 [cyan]wcx login[/] 重新设置凭证。")
    elif isinstance(e, fetcher.RateLimitError):
        err.print(f"[red]✗ 触发风控：[/]{e}\n\n建议等 1 小时后再试，或降低频率。")
    elif isinstance(e, fetcher.NotFoundError):
        err.print(f"[yellow]? 找不到：[/]{e}")
    else:
        err.print(f"[red]✗ 错误：[/]{e}")


@app.command()
def login(
    token: Optional[str] = typer.Option(None, "--token", "-t", help="后台 token"),
    cookie: Optional[str] = typer.Option(None, "--cookie", "-c", help="完整 Cookie"),
):
    """配置微信公众号后台 token 和 cookie（一次性，本地存储）。"""
    console.print(
        Panel.fit(
            "[bold]获取 token 和 cookie 的步骤：[/]\n"
            "1. 浏览器登录 [cyan]https://mp.weixin.qq.com[/]（需个人订阅号/服务号）\n"
            "2. 进入：[yellow]图文素材 → 新建图文 → 超链接 → 查找文章[/]\n"
            "3. 打开 DevTools（F12）→ Network\n"
            "4. 在搜索框搜任意账号，找到 [cyan]appmsg?action=list_ex[/] 请求\n"
            "5. 从 URL 复制 [cyan]token=XXXXX[/] 的值\n"
            "6. 从 Request Headers 复制完整 [cyan]Cookie[/] 字段",
            title="📘 登录指引",
            border_style="cyan",
        )
    )
    if not token:
        token = Prompt.ask("[bold]Token[/]").strip()
    if not cookie:
        cookie = Prompt.ask("[bold]Cookie[/]").strip()
    if not token or not cookie:
        err.print("[red]✗ token 和 cookie 不能为空[/]")
        raise typer.Exit(1)

    config.save_credentials(config.Credentials(token=token, cookie=cookie))
    console.print(f"[green]✓[/] 已保存到 [dim]{config.CONFIG_PATH}[/]")

    # smoke test
    try:
        f = fetcher.Fetcher(token, cookie)
        results = f.search_biz("人民日报")
        if results:
            console.print(f"[green]✓[/] 凭证有效（测试搜索返回 {len(results)} 条）")
    except Exception as e:
        err.print(f"[yellow]⚠ 凭证测试失败：[/]{e}")


@app.command()
def logout():
    """清除保存的凭证。"""
    if Confirm.ask("确认清除 token/cookie？", default=False):
        config.clear_credentials()
        console.print("[green]✓[/] 已清除")


@app.command()
def status():
    """显示配置和缓存状态。"""
    creds = config.load_credentials()
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("配置目录", str(config.CONFIG_DIR))
    t.add_row("缓存数据库", str(config.CACHE_DB))
    t.add_row("登录状态", "[green]✓ 已登录[/]" if creds else "[red]✗ 未登录[/]")

    with cache.connect() as conn:
        n_acc = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        n_art = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        n_full = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE content_md IS NOT NULL"
        ).fetchone()[0]
    t.add_row("缓存账号数", str(n_acc))
    t.add_row("缓存文章数", f"{n_art}（含正文 {n_full}）")
    console.print(Panel(t, title="wcx status", border_style="cyan"))


@app.command()
def search(
    query: str = typer.Argument(..., help="公众号名称"),
):
    """搜索公众号，返回候选列表。"""
    try:
        f = _get_fetcher()
        results = f.search_biz(query)
    except Exception as e:
        _friendly_error(e)
        raise typer.Exit(1)

    if not results:
        console.print(f"[yellow]没有找到与 [bold]{query}[/] 匹配的公众号[/]")
        return

    t = Table(title=f"搜索：{query}", title_style="bold cyan")
    t.add_column("#", style="dim")
    t.add_column("昵称", style="bold")
    t.add_column("别名")
    t.add_column("fakeid", style="dim")
    t.add_column("简介")
    for i, acc in enumerate(results, 1):
        sig = (acc.signature or "").replace("\n", " ")
        if len(sig) > 40:
            sig = sig[:40] + "…"
        t.add_row(str(i), acc.nickname, acc.alias, acc.fakeid, sig)
    console.print(t)


@app.command()
def fetch(
    query: str = typer.Argument(..., help="公众号名称或 fakeid"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="最多抓取几篇"),
    with_content: bool = typer.Option(False, "--content", help="同时抓取正文"),
    page_size: int = typer.Option(5, "--page-size", help="每页条数（1-5）"),
    min_delay: float = typer.Option(5.0, "--min-delay", help="最小请求间隔（秒）"),
    max_delay: float = typer.Option(15.0, "--max-delay", help="最大请求间隔（秒）"),
):
    """抓取公众号的文章列表（写入本地缓存）。"""
    try:
        f = _get_fetcher()
        account = f.resolve(query)
    except Exception as e:
        _friendly_error(e)
        raise typer.Exit(1)

    console.print(
        Panel.fit(
            f"[bold]{account.nickname}[/] [dim]({account.fakeid})[/]\n"
            f"{account.signature or '[dim]（无简介）[/dim]'}",
            title="🎯 目标账号",
            border_style="green",
        )
    )

    with cache.connect() as conn:
        cache.upsert_account(conn, account.to_dict())

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[cyan]{task.completed}/{task.total}[/]"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        )
        task_id = None
        count = 0

        def on_page(begin: int, fetched: int, total: int) -> None:
            nonlocal task_id
            if task_id is None:
                shown_total = min(total, limit) if limit else total
                task_id = progress.add_task(
                    f"抓取 [bold]{account.nickname}[/] 文章列表", total=shown_total
                )
            progress.update(task_id, advance=fetched)

        try:
            with progress:
                for art in f.iter_all_articles(
                    account.fakeid,
                    max_items=limit,
                    page_size=page_size,
                    min_delay=min_delay,
                    max_delay=max_delay,
                    on_page=on_page,
                ):
                    cache.upsert_article(conn, art.to_dict())
                    count += 1
        except KeyboardInterrupt:
            console.print("\n[yellow]⚠ 已中断，已抓取的条目已保存到缓存[/]")
        except Exception as e:
            _friendly_error(e)
            console.print(f"[yellow]（已保存 {count} 条到缓存，可稍后续跑）[/]")
            raise typer.Exit(1)

        console.print(f"[green]✓[/] 已入库 [bold]{count}[/] 篇元数据")

        if with_content:
            rows = cache.list_articles(conn, account.fakeid, limit=limit)
            need = [r for r in rows if not (r["content_md"] or "").strip()]
            if not need:
                console.print("[dim]所有文章已有缓存正文[/]")
                return
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[cyan]{task.completed}/{task.total}[/]"),
                TimeElapsedColumn(),
                console=console,
            ) as prog:
                tid = prog.add_task("抓取正文", total=len(need))
                for r in need:
                    try:
                        html = article_mod.fetch_article_html(r["link"])
                        inner, md = article_mod.extract_content(html)
                        if not inner.strip() and not md.strip():
                            raise RuntimeError("解析出的正文为空")
                        cache.set_article_content(conn, r["aid"], inner, md)
                    except Exception as e:  # noqa: BLE001
                        err.print(f"[red]✗[/] {r['title']}: {e}")
                    prog.update(tid, advance=1)
                    time.sleep(1.0)  # polite


@app.command(name="list")
def list_cmd(
    query: str = typer.Argument(..., help="公众号名称或 fakeid"),
    limit: int = typer.Option(20, "--limit", "-n", help="显示多少条"),
):
    """显示缓存中该公众号的文章列表。"""
    try:
        f = _get_fetcher()
        account = f.resolve(query)
    except Exception as e:
        _friendly_error(e)
        raise typer.Exit(1)

    with cache.connect() as conn:
        rows = cache.list_articles(conn, account.fakeid, limit=limit)

    if not rows:
        console.print(
            f"[yellow]缓存中没有 [bold]{account.nickname}[/] 的文章。"
            f"\n运行 [cyan]wcx fetch {query}[/] 开始抓取[/]"
        )
        return

    from datetime import datetime

    t = Table(title=f"{account.nickname} — 最近 {len(rows)} 篇", title_style="bold cyan")
    t.add_column("日期", style="dim")
    t.add_column("标题", style="bold")
    t.add_column("作者")
    t.add_column("正文", justify="center")
    for r in rows:
        date = datetime.fromtimestamp(r["create_time"]).strftime("%Y-%m-%d") if r["create_time"] else "-"
        has_content = "[green]✓[/]" if r["content_md"] else "[dim]·[/]"
        t.add_row(date, r["title"], r["author"] or "", has_content)
    console.print(t)


@app.command()
def export(
    query: str = typer.Argument(..., help="公众号名称或 fakeid"),
    out: Path = typer.Option(Path("./wcx-export"), "--out", "-o", help="输出目录"),
    fmt: str = typer.Option("all", "--format", "-f", help="索引格式：all|md|json|csv（默认 all 同时输出三种）"),
    articles_format: str = typer.Option(
        "md", "--articles", "-a", help="文章格式：md|html|none"
    ),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="仅导出最近 N 篇"),
    since: Optional[str] = typer.Option(
        None,
        "--since",
        help="仅导出此日期之后发布的文章：YYYY-MM-DD 或相对值如 10d / 2w / 1m",
    ),
):
    """导出缓存中的文章到文件。"""
    from . import exporters

    try:
        f = _get_fetcher()
        account = f.resolve(query)
    except Exception as e:
        _friendly_error(e)
        raise typer.Exit(1)

    since_ts: Optional[int] = None
    if since:
        import re as _re
        from datetime import datetime as _dt, timedelta as _td

        m = _re.fullmatch(r"(\d+)([dwm])", since)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            delta = _td(days=n * {"d": 1, "w": 7, "m": 30}[unit])
            since_ts = int((_dt.now() - delta).timestamp())
        else:
            try:
                since_ts = int(_dt.strptime(since, "%Y-%m-%d").timestamp())
            except ValueError:
                console.print(f"[red]--since 格式无效：{since}[/]")
                raise typer.Exit(1)

    with cache.connect() as conn:
        acc_row = cache.get_account(conn, account.fakeid)
        rows = cache.list_articles(conn, account.fakeid, limit=limit)

    if since_ts is not None:
        rows = [r for r in rows if (r["create_time"] or 0) >= since_ts]

    if not rows:
        console.print(
            f"[yellow]没有可导出的内容。先运行 [cyan]wcx fetch {query}[/][/]"
        )
        return

    target_dir = out / exporters._safe_filename(account.nickname)
    target_dir.mkdir(parents=True, exist_ok=True)

    with console.status(f"导出到 [cyan]{target_dir}[/] …", spinner="dots"):
        idx_path = exporters.export_index(rows, acc_row or account.to_dict(), target_dir, fmt=fmt)
        written = [idx_path]

        if articles_format != "none":
            art_dir = target_dir / "articles"
            for r in rows:
                if articles_format == "md":
                    written.append(exporters.export_article_markdown(r, art_dir))
                elif articles_format == "html":
                    written.append(exporters.export_article_html(r, art_dir))

    console.print(
        Panel.fit(
            f"[green]✓[/] 导出完成\n"
            f"目录：[cyan]{target_dir}[/]\n"
            f"索引：[dim]{idx_path.name}[/]\n"
            f"文章：[bold]{len(written) - 1}[/] 个文件",
            title="📦 导出",
            border_style="green",
        )
    )


@app.command()
def version():
    """显示版本。"""
    from . import __version__
    console.print(f"wcx {__version__}")


PROGRESS_PREFIX = "__WXMP_FETCH_PROGRESS__"


def _parse_date_range(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        start = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise RuntimeError(f"日期格式应为 YYYY-MM-DD：{value}") from exc
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


@app.command(name="search-accounts-json")
def search_accounts_json(
    query: str = typer.Argument(..., help="公众号名称"),
):
    """搜索公众号，返回 JSON 数组（机器接口）。"""
    try:
        creds = config.load_credentials()
        if not creds:
            raise RuntimeError("尚未登录，请先扫码登录")
        f = fetcher.Fetcher(creds.token, creds.cookie)
        results = f.search_biz(query)
        print(json.dumps([acc.to_dict() for acc in results], ensure_ascii=False))
    except fetcher.AuthError as e:
        raise SystemExit(f"认证失败：{e}")
    except fetcher.RateLimitError as e:
        raise SystemExit(f"触发风控：{e}")
    except Exception as e:
        raise SystemExit(str(e))


@app.command(name="fetch-article-content-json")
def fetch_article_content_json(
    link: str = typer.Argument(..., help="文章 URL"),
):
    """抓取单篇文章正文，返回 JSON（机器接口）。"""
    try:
        html = article_mod.fetch_article_html(link)
        inner, md = article_mod.extract_content(html)
        print(json.dumps({"html": inner, "md": md}, ensure_ascii=False))
    except Exception as e:
        raise SystemExit(str(e))


@app.command(name="fetch-selected-account-json")
def fetch_selected_account_json(
    account_json: str = typer.Argument(..., help="账号 JSON 字符串"),
    limit: int = typer.Argument(
        ...,
        help="最多抓取篇数（forward/backward）；audit 模式下作为已知区间上限的保险值",
    ),
    with_content: str = typer.Argument(..., help="是否抓取正文：0 或 1"),
    mode: str = typer.Option(
        "forward",
        "--mode",
        help="抓取方向：forward=从最新开始（默认），backward=从本地最老一篇之后继续向旧抓，audit=重扫已有区间并补漏",
    ),
    audit_date: str | None = typer.Option(
        None,
        "--audit-date",
        help="audit 模式下只检测指定日期当天的文章，格式 YYYY-MM-DD",
    ),
):
    """抓取指定公众号的文章列表+正文，带进度协议（机器接口）。"""

    def _text(value):
        return value or ""

    account = None  # forward-declare for emit

    def emit(stage, status, message, current=None, total=None, title=None):
        print(PROGRESS_PREFIX + json.dumps({
            "fakeid": account.fakeid if account else "",
            "nickname": account.nickname if account else "",
            "stage": stage,
            "status": status,
            "message": message,
            "current": current,
            "total": total,
            "title": title,
        }, ensure_ascii=False), flush=True)

    try:
        payload = json.loads(account_json)
        fetch_limit = limit
        do_content = with_content == "1"
        mode_normalized = (mode or "forward").strip().lower()
        if mode_normalized not in {"forward", "backward", "audit"}:
            raise RuntimeError(f"未知抓取模式：{mode}")
        if audit_date and mode_normalized != "audit":
            raise RuntimeError("--audit-date 只能与 --mode audit 一起使用")
        audit_range = (
            _parse_date_range(audit_date) if mode_normalized == "audit" else None
        )

        creds = config.load_credentials()
        if not creds:
            raise RuntimeError("尚未登录，请先扫码登录")

        account = fetcher.Account(
            fakeid=_text(payload.get("fakeid")).strip(),
            nickname=_text(payload.get("nickname")).strip(),
            alias=_text(payload.get("alias")).strip(),
            signature=_text(payload.get("signature")).strip(),
            round_head_img=_text(
                payload.get("avatar") or payload.get("round_head_img")
            ).strip(),
        )
        if not account.fakeid or not account.nickname:
            raise RuntimeError("公众号选择缺少 fakeid 或昵称")

        audit_date_label = audit_date.strip() if audit_date else ""
        target_desc = f"，检测日期={audit_date_label}" if audit_date_label else ""
        emit(
            "prepare",
            "done",
            f"已确认目标公众号：{account.nickname}（mode={mode_normalized}{target_desc}）",
        )

        f = fetcher.Fetcher(creds.token, creds.cookie)
        count = 0
        content_count = 0

        with cache.connect() as conn:
            emit("account", "running", "正在写入账号信息")
            cache.upsert_account(conn, account.to_dict())
            emit("account", "done", "账号信息已写入本地缓存")

            local_count = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE fakeid = ?",
                (account.fakeid,),
            ).fetchone()[0]
            local_aids: set[str] = {
                row[0]
                for row in conn.execute(
                    "SELECT aid FROM articles WHERE fakeid = ?",
                    (account.fakeid,),
                ).fetchall()
            }

            if mode_normalized == "forward":
                start_begin = 0
                page_max_items = fetch_limit
                article_total = fetch_limit
                stage_label = "向前续抓"
            elif mode_normalized == "backward":
                start_begin = local_count
                page_max_items = fetch_limit
                article_total = fetch_limit
                stage_label = "向后续抓"
            else:  # audit
                start_begin = 0
                page_max_items = (
                    fetch_limit if audit_range else max(local_count, fetch_limit)
                )
                article_total = (
                    page_max_items if audit_range else max(local_count, fetch_limit)
                )
                stage_label = (
                    f"完备性检测 {audit_date_label} 当天"
                    if audit_range
                    else "完备性回扫"
                )

            def on_page(begin, fetched, total):
                nonlocal article_total
                if mode_normalized == "audit":
                    article_total = max(min(total, page_max_items), 1)
                else:
                    article_total = min(total, fetch_limit) if fetch_limit else total
                emit(
                    "articles",
                    "running",
                    f"{stage_label}：读取 begin={begin}，本页 {fetched} 篇",
                    count,
                    article_total,
                )

            emit(
                "articles",
                "running",
                f"{stage_label}：从 begin={start_begin} 开始请求文章索引",
                0,
                article_total,
            )

            new_inserts = 0
            matched_count = 0
            matched_aids: list[str] = []
            for art in f.iter_all_articles(
                account.fakeid,
                max_items=page_max_items,
                page_size=5,
                start_begin=start_begin,
                on_page=on_page,
            ):
                count += 1
                if audit_range:
                    day_start, day_end = audit_range
                    if art.create_time >= day_end:
                        emit(
                            "articles",
                            "running",
                            f"{stage_label}：已扫描 {count}/{article_total}，等待进入当天区间",
                            count,
                            article_total,
                            art.title,
                        )
                        continue
                    if art.create_time < day_start:
                        emit(
                            "articles",
                            "running",
                            f"{stage_label}：已越过当天边界",
                            count,
                            article_total,
                            art.title,
                        )
                        break

                is_new = art.aid not in local_aids
                cache.upsert_article(conn, art.to_dict())
                matched_count += 1
                matched_aids.append(art.aid)
                if is_new:
                    local_aids.add(art.aid)
                    new_inserts += 1
                emit(
                    "articles",
                    "running",
                    f"{stage_label}：已扫描 {count}/{article_total}，匹配 {matched_count} 篇，新增 {new_inserts} 篇",
                    count,
                    article_total,
                    art.title,
                )

            done_msg = (
                f"{stage_label}完成：扫描 {count} 篇，"
                f"{'新增' if mode_normalized != 'audit' else '补漏'} {new_inserts} 篇"
            )
            if audit_range:
                done_msg = (
                    f"{stage_label}完成：扫描 {count} 篇，"
                    f"当天 {matched_count} 篇，补漏 {new_inserts} 篇"
                )
            emit(
                "articles",
                "done",
                done_msg,
                count,
                article_total,
            )

            if do_content:
                if audit_range:
                    placeholders = ",".join("?" for _ in matched_aids)
                    rows = (
                        conn.execute(
                            f"SELECT * FROM articles WHERE aid IN ({placeholders}) ORDER BY create_time DESC",
                            matched_aids,
                        ).fetchall()
                        if matched_aids
                        else []
                    )
                else:
                    rows = cache.list_articles(conn, account.fakeid, limit=fetch_limit)
                need = [r for r in rows if not (r["content_md"] or "").strip()]
                emit(
                    "content",
                    "running",
                    f"待抓取正文 {len(need)} 篇",
                    0,
                    len(need),
                )
                for row in need:
                    try:
                        emit(
                            "content",
                            "running",
                            f"正在抓取正文 {content_count + 1}/{len(need)}",
                            content_count,
                            len(need),
                            row["title"],
                        )
                        html = article_mod.fetch_article_html(row["link"])
                        inner, md = article_mod.extract_content(html)
                        if not inner.strip() and not md.strip():
                            raise RuntimeError("解析出的正文为空")
                        cache.set_article_content(conn, row["aid"], inner, md)
                        content_count += 1
                        emit(
                            "content",
                            "running",
                            f"正文已写入 {content_count}/{len(need)}",
                            content_count,
                            len(need),
                            row["title"],
                        )
                    except Exception as e:
                        emit(
                            "content",
                            "warning",
                            f"正文抓取失败：{e}",
                            content_count,
                            len(need),
                            row["title"],
                        )
                        print(f"{row['title']}: {e}", file=sys.stderr)
                    time.sleep(1.0)
                emit(
                    "content",
                    "done",
                    f"正文抓取完成 {content_count}/{len(need)} 篇",
                    content_count,
                    len(need),
                )

        action_word = "补漏" if mode_normalized == "audit" else "新增"
        complete_message = (
            f"已完成（mode={mode_normalized}）：扫描 {count} 篇，"
            f"{action_word} {new_inserts} 篇，正文 {content_count} 篇"
        )
        if audit_range:
            complete_message = (
                f"已完成（mode={mode_normalized}, audit_date={audit_date_label}）："
                f"扫描 {count} 篇，当天 {matched_count} 篇，补漏 {new_inserts} 篇，正文 {content_count} 篇"
            )
        emit(
            "complete",
            "done",
            complete_message,
            count,
            count,
        )
        print(json.dumps({
            "fakeid": account.fakeid,
            "nickname": account.nickname,
            "mode": mode_normalized,
            "audit_date": audit_date_label or None,
            "scanned": count,
            "matched_count": matched_count,
            "new_inserts": new_inserts,
            "count": count,  # kept for backwards compat
            "content_count": content_count,
        }, ensure_ascii=False))
    except fetcher.AuthError as e:
        raise SystemExit(f"认证失败：{e}")
    except fetcher.RateLimitError as e:
        raise SystemExit(f"触发风控：{e}")
    except Exception as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    app()
