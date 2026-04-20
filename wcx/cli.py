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

import time
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
            need = [r for r in rows if r["content_md"] is None]
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


if __name__ == "__main__":
    app()
