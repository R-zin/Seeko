"""One Notion database through the MCP server: resolve its data source, read / extend its schema, create, update
and query pages. Tool names are discovered at runtime; both the data-source tools (server v2, API 2025-09-03) and the
older database tools (server v1, API 2022-06-28) are understood."""
from __future__ import annotations
from .client import NotionError, NotionMCP
from .schema import BLOCK_BATCH, description_blocks


class NotionDB:
    def __init__(self, client: NotionMCP, database_id: str):
        self.client, self.database_id = client, database_id.strip()
        self.data_source_id: str | None = None
        self.properties: dict = {}

    def _tool(self, *needles: str) -> str:
        return self.client.find_tool(*needles)

    async def open(self) -> dict:
        """Load the database's properties (from its first data source on the current API)."""
        db = await self.client.call(self._tool("retrieve", "database"), {"database_id": self.database_id})
        if db.get("properties"):                      # old API: the database itself carries the schema
            self.data_source_id, self.properties = None, db["properties"]
            return self.properties
        sources = db.get("data_sources") or []
        if not sources:
            raise NotionError("this database has no data source (is it a linked view? use the original database)", 400)
        self.data_source_id = sources[0]["id"]
        src = await self.client.call(self._tool("retrieve", "data-source"), {"data_source_id": self.data_source_id})
        self.properties = src.get("properties") or {}
        return self.properties

    @property
    def parent(self) -> dict:
        if self.data_source_id:
            return {"type": "data_source_id", "data_source_id": self.data_source_id}
        return {"database_id": self.database_id}

    async def add_properties(self, props: dict) -> None:
        if not props: return
        if self.data_source_id:
            await self.client.call(self._tool("update", "data-source"), {"data_source_id": self.data_source_id, "properties": props})
        else:
            await self.client.call(self._tool("update", "database"), {"database_id": self.database_id, "properties": props})
        await self.open()

    async def query(self, filter: dict | None = None, start_cursor: str | None = None, page_size: int = 100) -> dict:
        args: dict = {"page_size": page_size}
        if filter: args["filter"] = filter
        if start_cursor: args["start_cursor"] = start_cursor
        if self.data_source_id:
            return await self.client.call(self._tool("query", "data-source"), {"data_source_id": self.data_source_id, **args})
        return await self.client.call(self._tool("query", "database"), {"database_id": self.database_id, **args})

    async def find_page(self, property_name: str, value: str) -> str | None:
        """Id of the (first, non-trashed) page whose rich-text property equals `value`."""
        res = await self.query({"property": property_name, "rich_text": {"equals": value}}, page_size=5)
        for page in res.get("results") or []:
            if not (page.get("in_trash") or page.get("archived")):
                return page["id"]
        return None

    async def create_page(self, properties: dict, description: str = "") -> str:
        blocks = description_blocks(description)
        args = {"parent": self.parent, "properties": properties}
        if blocks: args["children"] = blocks[:BLOCK_BATCH]
        page = await self.client.call(self._tool("post", "page"), args)
        if not page.get("id"):
            raise NotionError(f"creating a page returned no id: {str(page)[:200]}")
        await self._append(page["id"], blocks[BLOCK_BATCH:])
        return page["id"]

    async def update_page(self, page_id: str, properties: dict) -> None:
        await self.client.call(self._tool("patch", "page"), {"page_id": page_id, "properties": properties})

    async def append_description(self, page_id: str, description: str) -> None:
        await self._append(page_id, description_blocks(description))

    async def _append(self, block_id: str, blocks: list[dict]) -> None:
        for i in range(0, len(blocks), BLOCK_BATCH):
            await self.client.call(self._tool("patch", "block", "children"), {"block_id": block_id, "children": blocks[i:i + BLOCK_BATCH]})
