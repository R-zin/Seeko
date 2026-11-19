"""Notion MCP client over stdio using the official `mcp` SDK.

Talks to @notionhq/notion-mcp-server (major version 2: Notion API 2025-09-03, where a database holds one or more
*data sources* and pages are queried / schema-edited per data source). The server picks the Notion-Version per
operation from its own OpenAPI spec, so none is forced here.
"""
from __future__ import annotations
import json
import os
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

NOTION_MCP_PACKAGE = "@notionhq/notion-mcp-server@2"   # pinned major: tool names and API version are stable within it
# The MCP SDK starts the server with a minimal environment; npx (download) and the server (Notion API calls) still
# need the network settings of this machine, e.g. behind a corporate proxy.
PASSTHROUGH_ENV = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy", "NODE_EXTRA_CA_CERTS",
                   "SSL_CERT_FILE", "NPM_CONFIG_REGISTRY", "npm_config_registry", "NPM_CONFIG_CACHE", "npm_config_cache")


class NotionError(RuntimeError):
    """A Notion API error relayed by the MCP server. `status` is the HTTP status (0 = transport / tool error)."""

    def __init__(self, message: str, status: int = 0, code: str = ""):
        super().__init__(message)
        self.status, self.code = status, code

    @property
    def transient(self) -> bool:
        """Worth retrying later as-is: rate limit, Notion outage, conflict, or the MCP process itself failing."""
        return self.status in (0, 409, 429) or self.status >= 500

    @property
    def gone(self) -> bool:
        """The page / database no longer exists for this integration (deleted, or unshared)."""
        return self.status == 404 or self.code == "object_not_found" or (
            self.status == 400 and "archived" in str(self).lower())


def _error_from(data: dict, tool: str) -> NotionError | None:
    """The server returns HTTP errors as a normal text result: {"object": "error", "status": 404, "code": ...}."""
    if not isinstance(data, dict) or not (data.get("object") == "error" or data.get("status") == "error"):
        return None
    status = data.get("status")
    return NotionError(f"{tool}: {data.get('code') or 'error'}: {str(data.get('message') or data)[:300]}",
                       status if isinstance(status, int) else 0, str(data.get("code") or ""))


class NotionMCP:
    def __init__(self, token: str, command: str = "npx", args: list[str] | None = None):
        env = {k: os.environ[k] for k in PASSTHROUGH_ENV if os.environ.get(k)}
        self.params = StdioServerParameters(command=command, args=args or ["-y", NOTION_MCP_PACKAGE],
                                            env={**env, "NOTION_TOKEN": token})
        self._stack = AsyncExitStack(); self.session: ClientSession | None = None; self.tools: dict = {}

    async def connect(self) -> list[str]:
        r, w = await self._stack.enter_async_context(stdio_client(self.params))
        self.session = await self._stack.enter_async_context(ClientSession(r, w))
        await self.session.initialize()
        self.tools = {t.name: t for t in (await self.session.list_tools()).tools}
        return sorted(self.tools)

    async def close(self): await self._stack.aclose()

    async def __aenter__(self):
        await self.connect(); return self

    async def __aexit__(self, *exc): await self.close()

    def find_tool(self, *needles: str) -> str:
        """Discover a tool by name fragments (tool names differ across server versions)."""
        for name in self.tools:
            n = name.lower().replace("_", "-")
            if all(x in n for x in needles): return name
        raise LookupError(f"no MCP tool matching {needles}; have {sorted(self.tools)}")

    def has_tool(self, *needles: str) -> bool:
        try: self.find_tool(*needles); return True
        except LookupError: return False

    async def call(self, name: str, args: dict) -> dict:
        try:
            res = await self.session.call_tool(name, args)
        except Exception as e:   # the server process died, or the tool raised
            raise NotionError(f"{name}: {type(e).__name__}: {e}") from e
        text = "".join(c.text for c in res.content if getattr(c, "text", None))
        if res.isError: raise NotionError(f"{name}: {text[:300]}")
        try: data = json.loads(text)
        except json.JSONDecodeError: return {"raw": text}
        if err := _error_from(data, name): raise err
        return data
