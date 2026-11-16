#!/usr/bin/env python3
"""DeepSeek Search & Fetch — a zero-dependency MCP server (stdio).

Exposes two tools backed by the official DeepSeek Web API:

  * deepseek_search — POST https://api.deepseek.com/v1/tools/search
  * deepseek_fetch  — POST https://api.deepseek.com/v1/tools/fetch

Only the Python standard library is used, so it runs anywhere with
Python 3.8+ — no pip install required.

The API key is resolved in this order:
  1. the DEEPSEEK_API_KEY environment variable,
  2. a file named ``api_key.txt`` next to this script (first non-empty line).

Run:  python server.py          (speaks MCP over stdin/stdout)
Debug: set DEEPSEEK_MCP_DEBUG=1 to log requests to stderr.
"""

import json
import os
import sys
import urllib.error
import urllib.request

SERVER_NAME = "deepseek-search"
SERVER_VERSION = "1.0.0"
API_BASE = "https://api.deepseek.com"
PROTOCOL_VERSION = "2024-11-05"  # fallback if the client asks for something else

SEARCH_TIMEOUT = 60
FETCH_TIMEOUT = 90


def log(msg: str) -> None:
    if os.environ.get("DEEPSEEK_MCP_DEBUG"):
        print(f"[deepseek-mcp] {msg}", file=sys.stderr, flush=True)


def api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_key.txt")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    return ""


class ApiError(Exception):
    """Raised with a user-facing message when the DeepSeek API call fails."""


def _post(path: str, payload: dict, timeout: int) -> dict:
    key = api_key()
    if not key:
        raise ApiError(
            "No DeepSeek API key configured. Set the DEEPSEEK_API_KEY environment "
            "variable or put the key in api_key.txt next to server.py."
        )
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_BASE + path,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    log(f"POST {path} payload={payload}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:800]
        raise ApiError(f"DeepSeek API returned HTTP {exc.code}: {detail or exc.reason}")
    except urllib.error.URLError as exc:
        raise ApiError(f"Could not reach the DeepSeek API: {exc.reason}")
    except TimeoutError:
        raise ApiError(f"DeepSeek API request timed out after {timeout}s.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(f"DeepSeek API returned non-JSON response: {raw[:400]}")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def _fmt_search_results(query: str, data: dict) -> str:
    results = data.get("results") or []
    if not results:
        return f'No results for "{query}".'
    lines = [f'Search results for "{query}" ({len(results)} hits):', ""]
    for i, r in enumerate(results, 1):
        meta = []
        if r.get("site_name"):
            meta.append(str(r["site_name"]))
        if r.get("date"):
            meta.append(str(r["date"])[:10])
        if r.get("authority") is not None:
            meta.append(f"authority={r['authority']}")
        if r.get("rerank_score") is not None:
            meta.append(f"score={round(float(r['rerank_score']), 2)}")
        head = f"[{i}] {r.get('title') or '(untitled)'}"
        if meta:
            head += f"  ({' · '.join(meta)})"
        lines.append(head)
        lines.append(f"    {r.get('url', '')}")
        snippet = (r.get("snippet") or "").strip()
        if snippet:
            lines.append(f"    {snippet}")
        summary = (r.get("summary") or "").strip()
        if summary and summary != snippet:
            lines.append("    --- summary ---")
            lines.extend("    " + ln for ln in summary.splitlines() if ln.strip())
        content = (r.get("content") or "").strip()
        if content:
            lines.append("    --- content ---")
            lines.extend("    " + ln for ln in content.splitlines() if ln.strip())
        lines.append("")
    lines.append("Tip: call deepseek_fetch on any URL above to read the full page.")
    return "\n".join(lines)


def tool_search(args: dict) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ApiError("`query` is required.")
    if len(query) > 128:
        raise ApiError("`query` must be at most 128 characters.")
    count = args.get("count", 10)
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise ApiError("`count` must be an integer between 1 and 50.")
    if not 1 <= count <= 50:
        raise ApiError("`count` must be between 1 and 50.")
    payload = {"query": query, "count": count}
    ori = args.get("ori_question")
    if ori:
        payload["ori_question"] = str(ori)[:128]
    localization = args.get("localization")
    if localization:
        payload["localization"] = str(localization)
    if args.get("need_content"):
        payload["need_content"] = True
    data = _post("/v1/tools/search", payload, SEARCH_TIMEOUT)
    return _fmt_search_results(query, data)


def tool_fetch(args: dict) -> str:
    url = str(args.get("url") or "").strip()
    if not url:
        raise ApiError("`url` is required.")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ApiError("`url` must start with http:// or https://.")
    max_chars = args.get("max_chars", 20000)
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        raise ApiError("`max_chars` must be a non-negative integer (0 = no limit).")
    data = _post("/v1/tools/fetch", {"url": url}, FETCH_TIMEOUT)
    title = (data.get("title") or "").strip() or url
    content = data.get("content") or ""
    if max_chars and len(content) > max_chars:
        content = (
            content[:max_chars]
            + f"\n\n[truncated: {len(content) - max_chars} more characters — "
            "raise max_chars to read the rest]"
        )
    return f"# {title}\n\nSource: {url}\n\n{content}"


TOOLS = [
    {
        "name": "deepseek_search",
        "description": (
            "Web search via the official DeepSeek search API. Returns ranked results "
            "with title, URL, snippet, summary, site name, date, authority (‑1..4, "
            "≥3 very authoritative) and rerank score (0..3, ≥1.5 relevant). The query "
            "supports `site:example.com`, `-site:example.com` and \"exact phrase\" "
            "operators. Use deepseek_fetch on a result URL to read the full page."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "Search query (1–128 chars).",
                },
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                    "description": "Number of results to return (1–50, default 10).",
                },
                "ori_question": {
                    "type": "string",
                    "maxLength": 128,
                    "description": "Optional: the user's original question, helps reranking.",
                },
                "localization": {
                    "type": "string",
                    "description": "Optional: region hint, e.g. 'CN' / 'US'. Empty = IP-based.",
                },
                "need_content": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also return page content per result (heavier response).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "deepseek_fetch",
        "description": (
            "Fetch any web page as Markdown via the official DeepSeek fetch API. "
            "Returns the page title plus its main content converted to Markdown. "
            "Good for reading a search result, documentation page or article in full."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute http(s) URL to fetch (credentials not allowed).",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 20000,
                    "description": "Truncate the returned content to this many characters (0 = no limit).",
                },
            },
            "required": ["url"],
        },
    },
]


def call_tool(name: str, args: dict) -> dict:
    try:
        if name == "deepseek_search":
            text = tool_search(args)
        elif name == "deepseek_fetch":
            text = tool_fetch(args)
        else:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                "isError": True,
            }
    except ApiError as exc:
        return {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True}
    except Exception as exc:  # never crash the server on a bad call
        log(f"unexpected error in {name}: {exc!r}")
        return {
            "content": [{"type": "text", "text": f"Internal error: {exc}"}],
            "isError": True,
        }
    return {"content": [{"type": "text", "text": text}], "isError": False}


# ---------------------------------------------------------------------------
# JSON-RPC plumbing (newline-delimited over stdio)
# ---------------------------------------------------------------------------
def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": requested or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        log(f"tools/call {name}")
        result = call_tool(name, args)
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if msg_id is None:
        # A notification we don't care about (e.g. notifications/initialized).
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    # UTF-8 everywhere, incl. Windows consoles.
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    log("server started")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log(f"skipping non-JSON input: {line[:120]}")
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
