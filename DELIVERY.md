---
title: wcx 交付说明
date: 2026-04-18
version: 0.1.0
---

# wcx — 微信公众号文章抓取工具

本地 CLI + Claude Skill，一次登录，长期可用。基于 `cgi-bin/appmsg?action=list_ex` 接口（与 wechat-article-exporter 同源路径）。

## 交付物

### 1. 本地 CLI（`wcx`）

项目路径：`~/projects/wcx/`

    wcx/
    ├── cli.py         # Typer + Rich 交互式 CLI
    ├── fetcher.py     # cgi-bin/appmsg 客户端（curl_cffi Chrome120 指纹）
    ├── article.py     # 正文抓取 + HTML→Markdown
    ├── cache.py       # SQLite 缓存（断点续跑）
    ├── config.py      # token/cookie 持久化到 ~/.config/wcx/
    └── exporters.py   # MD/HTML/JSON/CSV 导出

全局命令：`~/.local/bin/wcx`（symlink 到 venv）

### 2. Claude Skill

路径：`~/.claude/skills/wcx/SKILL.md`

已注册。在 Claude Code 里说「抓一下 xxx 公众号最新 10 篇」会自动触发。

## 命令速查

| 命令 | 说明 |
|------|------|
| `wcx login` | 一次性设置 token/cookie（含 6 步指引 + 冒烟验证） |
| `wcx logout` | 清除凭证 |
| `wcx status` | 配置 + 缓存状态 |
| `wcx search <名称>` | 搜索候选账号 |
| `wcx fetch <名称>` | 抓取列表（`-n N` 限量；`--content` 同抓正文） |
| `wcx list <名称>` | 查看缓存 |
| `wcx export <名称>` | 导出到文件（`-f md/json/csv/all`；`-a md/html/none`） |

## 三步上手

    wcx login                                  # 交互式录入 token/cookie
    wcx fetch 人民日报 --limit 10 --content     # 抓最新 10 篇（含正文）
    wcx export 人民日报 --out ./output          # 导出成文件

## UX 亮点

| 细节 | 实现 |
|------|------|
| 频控友好 | 默认 5-15s 随机间隔；`ret=200013` → RateLimitError + 明确提示等 1 小时 |
| 断点续跑 | 所有抓取入 SQLite，Ctrl+C 不丢数据，重跑不重复 |
| 进度可视 | Rich 双进度条（元数据 + 正文分别显示 ETA、剩余时间） |
| 错误分层 | Auth / RateLimit / NotFound 三种独立文案，每种都告诉用户下一步怎么做 |
| 登录引导 | `wcx login` 显示 6 步获取 token 指引 + 立即冒烟验证 |
| 去重保护 | `iter_all_articles` 内置 `seen_aids` 集合，防止分页边界重复 |
| 导出自由 | 索引（md/json/csv/all） × 文章（md/html/none）任意组合；`all` 模式一次出三份 |

## 关键技术选型

- **`curl-cffi`**：模拟 Chrome 120 TLS 指纹，绕过基础 TLS 检测
- **`typer` + `rich`**：现代 CLI + 终端美化（进度条、表格、Panel）
- **`SQLite`**：零依赖持久化，天然支持断点续跑
- **`markdownify`**：HTML→Markdown，LLM/知识库友好
- **`platformdirs`**：跨平台配置目录（macOS: `~/Library/Application Support/wcx/`）

## 风控与合规

**默认参数已是保守值**（5-15s/页）。如果触发 `200013`：

- 立即停机 ≥ 1 小时
- 不要高频重试
- 考虑 IP 代理池（本工具暂未内置）

**法律红线**（报告已详述）：

- ✅ 自用 / 学术研究 / 个人知识库
- ❌ 商业二次分发 / 镜像站 / 高频爬取
- 商用走正规平台：西瓜数据 / 新榜 / 次幂数据

## 已知限制（v0.1）

- `wcx login` 只支持手动填 token（自动 QR 登录 + 刷新是 v0.2）
- 正文抓取未处理 WeChat 的 `visibility:hidden` CSS 动态显示（已 strip style，但某些图片懒加载可能缺失）
- 未内置 IP 代理池（需要重度用户可自行通过 `HTTPS_PROXY` 环境变量注入，`curl_cffi` 支持）
- 未写测试（遵循 CLAUDE.md 规定）— 首次使用时你需要亲自跑一遍验证

## 下一步（v0.2 候选）

1. 自动 QR 登录 + cookie 定时刷新
2. `wcx watch <名称>` 常驻模式，轮询新文章
3. 内置代理池（`wcx config proxy add socks5://...`）
4. 正文重试队列（link 过期自动用缓存 HTML 回退）
5. MCP server 形态（直接被 Claude Code 调用，不经 CLI）

## 相关资源

- 研究报告：`~/Documents/WeChat_Article_Fetching_Research_20260418/`
- 上游参考：
    - [wechat-article-exporter](https://github.com/wechat-article/wechat-article-exporter) — 8.6k star，同源接口
    - [wechatDownload](https://github.com/qiye45/wechatDownload) — v4.4，MCP/Skill 集成
    - [wechat-download-api](https://github.com/tmwgsicp/wechat-download-api) — SOCKS5 代理池方案
