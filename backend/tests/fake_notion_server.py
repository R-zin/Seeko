"""Fake Notion MCP server (stdio) for tests, mimicking tool names of @notionhq/notion-mcp-server."""
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-notion")
DB = {"properties": {"Role": {"type": "title"}, "Company": {"type": "rich_text"}, "Source": {"type": "select"},
      "Job URL": {"type": "url"}, "Match Score": {"type": "number"}, "Status": {"type": "select"},
      "Tailored Summary": {"type": "rich_text"}}}  # Applied Date intentionally missing
PAGES: dict = {}

@mcp.tool(name="API-retrieve-a-database")
def retrieve(database_id: str) -> str: return json.dumps(DB)

@mcp.tool(name="API-post-page")
def post_page(parent: dict, properties: dict) -> str:
    pid = f"page{len(PAGES)+1}"; PAGES[pid] = properties; return json.dumps({"id": pid})

@mcp.tool(name="API-patch-page")
def patch_page(page_id: str, properties: dict) -> str:
    PAGES[page_id].update(properties); return json.dumps({"id": page_id, "properties": PAGES[page_id]})

if __name__ == "__main__": mcp.run()
