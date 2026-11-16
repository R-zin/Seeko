"""Notion MCP client over stdio using the official `mcp` SDK."""
from __future__ import annotations
import json
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class NotionMCP:
    def __init__(self, token: str, command: str = "npx", args: list[str] | None = None):
        self.params = StdioServerParameters(
            command=command, args=args or ["-y", "@notionhq/notion-mcp-server"],
            env={"NOTION_TOKEN": token, "OPENAPI_MCP_HEADERS": json.dumps(
                {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"})})
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

    async def call(self, name: str, args: dict) -> dict:
        res = await self.session.call_tool(name, args)
        text = "".join(c.text for c in res.content if getattr(c, "text", None))
        if res.isError: raise RuntimeError(f"{name}: {text[:300]}")
        try: return json.loads(text)
        except json.JSONDecodeError: return {"raw": text}
