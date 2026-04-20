# wcx — 微信公众号文章抓取 CLI

**一句话**：本地 CLI，抓取任意微信公众号的全部历史文章（或最新 N 篇），导出 Markdown/HTML/JSON。

> 基于 `cgi-bin/appmsg?action=list_ex` 接口（与 [wechat-article-exporter](https://github.com/wechat-article/wechat-article-exporter) 同源）。需要一个自己的微信订阅号/服务号用于取 token。

## 特性

- **一次登录，长期可用**：token/cookie 存在 `~/.config/wcx/`
- **断点续跑**：所有抓取都写 SQLite，中断了继续跑不重复
- **尊重频控**：默认 5-15 秒随机间隔，遇 200013 自动停
- **导出灵活**：Markdown（LLM/知识库友好）、HTML（样式还原）、JSON、CSV
- **友好反馈**：Rich 进度条 + 彩色输出 + 清晰错误提示
- **Claude Skill 集成**：可在 Claude Code 里自然语言调用

## 安装

```bash
cd ~/projects/wcx
pip install -e .
```

需要 Python 3.10+。

## 快速开始

```bash
# 1. 一次性登录（提示获取 token/cookie 的方法）
wcx login

# 2. 搜索公众号
wcx search 人民日报

# 3. 抓取最新 10 篇（元数据 + 正文）
wcx fetch 人民日报 --limit 10 --content

# 4. 看一眼抓了啥
wcx list 人民日报

# 5. 导出成 Markdown 文件
wcx export 人民日报 --out ./output
```

## 完整命令

| 命令 | 说明 |
|------|------|
| `wcx login` | 交互式设置 token 和 cookie |
| `wcx logout` | 清除凭证 |
| `wcx status` | 显示配置 + 缓存状态 |
| `wcx search <名称>` | 搜索公众号，显示候选列表 |
| `wcx fetch <名称>` | 抓取文章列表（`-n N` 限量、`--content` 同时抓正文） |
| `wcx list <名称>` | 查看缓存中的文章 |
| `wcx export <名称>` | 导出到文件（`-f md/json/csv`, `-a md/html/none`） |

## 如何获取 token 和 cookie

运行 `wcx login` 会显示步骤：

1. 浏览器登录 [mp.weixin.qq.com](https://mp.weixin.qq.com)（需个人订阅号/服务号）
2. 进入：**图文素材 → 新建图文 → 超链接 → 查找文章**
3. F12 打开 DevTools → Network
4. 在搜索框里搜任意账号，找到 `appmsg?action=list_ex` 请求
5. URL 里复制 `token=XXXXX` 的值
6. Request Headers 里复制完整 `Cookie` 字段

## 风控与限流

WeChat 对 `cgi-bin/appmsg` 有频控：

- **短时间高频**：`ret=200013`，停 1 小时以上
- **单日量大**：账号临时禁用搜索，24 小时
- **持续滥用**：订阅号功能受限

默认参数已是保守值（5-15 秒间隔）。如需更激进，自担风险：`wcx fetch xxx --min-delay 2 --max-delay 5`。

## 法律与合规

**这是一个技术研究工具**。使用前请确认：

- ✅ 仅抓取你有权访问的公众号（自己的、有授权的、或公开研究场景）
- ✅ 不要做商业二次分发 / 镜像站
- ❌ 不要高频爬取伤害平台 / 其他用户
- ❌ 商业项目请走正规数据供应商（西瓜数据、新榜、次幂数据）

2021 年杭州互联网法院已有爬取微信公众号数据被判赔 60 万的判例。个人学习研究 OK，商用务必合规。

## 架构

```
wcx/
├── cli.py          # Typer + Rich CLI
├── fetcher.py      # cgi-bin/appmsg 客户端
├── article.py      # 正文抓取 + Markdown 转换
├── cache.py        # SQLite 缓存
├── config.py       # token/cookie 存储
└── exporters.py    # MD/HTML/JSON/CSV 导出
```

## License

MIT
