"""A tiny MCP server that only answers requests carrying an API key.

Every POST /mcp must carry the key, either as `X-API-Key: <key>` or
`Authorization: Bearer <key>`, or it gets a 401 before any MCP method runs.
Behind the check there is one harmless tool, `ping`.

Every request is logged with whether a key arrived and where it came from, so
you can see what a client (or a tunnel) actually sends.
"""

import json
import logging
import os
import re
import sys
from datetime import datetime

import uvicorn
from mcp.server.fastmcp import FastMCP

API_KEY = os.environ.get("MCP_API_KEY", "")
PORT = int(os.environ.get("MCP_PORT", "8765"))

if not API_KEY:
    sys.exit("Set MCP_API_KEY to the key clients must send.")

mcp = FastMCP("mcp-api-key-server", stateless_http=True, json_response=True, log_level="WARNING")
logging.getLogger("mcp").setLevel(logging.WARNING)


@mcp.tool()
def ping() -> str:
    """Reply with a greeting, to prove the call got through the key check."""
    return "pong: your key got through"


def presented_key(headers: dict[str, str]) -> tuple[str | None, str]:
    """The key a request presents: X-API-Key first, then an Authorization: Bearer token."""
    if headers.get("x-api-key"):
        return headers["x-api-key"], "X-API-Key"
    match = re.match(r"^bearer\s+(\S+)\s*$", headers.get("authorization", ""), re.I)
    if match:
        return match.group(1), "Authorization: Bearer"
    return None, "none"


def log(method: str, path: str, source: str, ok: bool) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    verdict = "ALLOWED" if ok else "REJECTED"
    print(f"[{stamp}] {method} {path}  key via: {source:<22} -> {verdict}", flush=True)


class KeyGate:
    """ASGI wrapper that refuses /mcp requests without the right key."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            return await self.app(scope, receive, send)

        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        key, source = presented_key(headers)
        ok = key == API_KEY
        log(scope["method"], scope["path"], source, ok)
        if ok:
            return await self.app(scope, receive, send)

        body = json.dumps({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32001, "message": "Missing API key" if key is None else "Invalid API key"},
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})


if __name__ == "__main__":
    print(f"MCP server listening on http://localhost:{PORT}/mcp", flush=True)
    uvicorn.run(KeyGate(mcp.streamable_http_app()), host="127.0.0.1", port=PORT, log_level="warning")
