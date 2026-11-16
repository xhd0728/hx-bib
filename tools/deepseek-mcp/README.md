# DeepSeek Search & Fetch MCP 工具

一个零依赖的 MCP（Model Context Protocol）服务器，封装 DeepSeek 官方 Web API：

| 工具 | 后端接口 | 用途 |
| --- | --- | --- |
| `deepseek_search` | `POST https://api.deepseek.com/v1/tools/search` | 联网搜索，支持 `site:` / `-site:` / `"精确短语"` 语法，返回标题、URL、摘要、站点、日期、权威度（-1..4）与相关性分（0..3） |
| `deepseek_fetch` | `POST https://api.deepseek.com/v1/tools/fetch` | 抓取任意网页并转换为 Markdown（标题 + 正文） |

只用 Python 标准库（`urllib` + `json`），**无需 pip 安装任何依赖**，Python 3.8+ 均可运行。

## API Key

按以下顺序读取：

1. 环境变量 `DEEPSEEK_API_KEY`；
2. 与本文件同目录的 `api_key.txt`（首个非空行；文件已被 `.gitignore` 忽略）。

本机已写入 `api_key.txt`，开箱即用。

## 注册到 MCP 客户端

服务器通过 **stdio** 通信。模板见 [`mcp-config.example.json`](mcp-config.example.json)，使用本机 conda 环境 `bib`（Python 3.12）：

```json
{
  "mcpServers": {
    "deepseek-search": {
      "command": "conda",
      "args": [
        "run", "--no-capture-output", "-n", "bib",
        "python", "tools/deepseek-mcp/server.py"
      ]
    }
  }
}
```

如果 `conda` 不在 PATH 里，把 `command` 换成环境的 python 绝对路径（`conda env list` 可查，例如
`C:\\Users\\qysys\\miniconda3\\envs\\bib\\python.exe`），`args` 只保留
`["F:\\Workspace\\citation-refactor\\tools\\deepseek-mcp\\server.py"]`。

**Claude Code（本项目）**：把上面 JSON 保存为仓库根目录的 `.mcp.json`，之后在该目录打开会话即可使用。

**Claude Desktop**：合并进 `claude_desktop_config.json`（Windows 通常在
`%APPDATA%\\Claude\\claude_desktop_config.json`），`args` 中的脚本路径改为绝对路径，然后完全退出并重启应用。

**其他客户端**（Cline、Cursor 等）：同样填写 `command` + `args` 即可，均为标准 stdio MCP 配置。

## 手动测试

无需任何客户端，直接向 stdin 喂 JSON-RPC 即可：

```bash
python server.py <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"deepseek_search","arguments":{"query":"arXiv citation hallucination","count":2}}}
EOF
```

设置 `DEEPSEEK_MCP_DEBUG=1` 可把请求日志打到 stderr。

## 工具参数

`deepseek_search`

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `query` | string，必填 | 查询词（1–128 字符），支持 `site:` / `-site:` / `"..."` |
| `count` | int | 结果条数（1–50，默认 10） |
| `ori_question` | string | 可选，用户原始问题，帮助重排 |
| `localization` | string | 可选，地区提示（如 `CN`）；留空按 IP 判断 |
| `need_content` | bool | 同时返回每条的页面正文（响应更大） |

`deepseek_fetch`

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `url` | string，必填 | 绝对 http(s) URL（不允许带凭据） |
| `max_chars` | int | 正文截断长度（默认 20000，0 = 不截断） |
