"""Fake Notion MCP server (stdio) for tests, mimicking @notionhq/notion-mcp-server v2 (Notion API 2025-09-03).

Like the real server it returns Notion HTTP errors as a normal text result ({"object": "error", "status": 404, ...}),
not as an MCP error. Start with `--schema legacy` for the original 7-property tracker, `--schema bare` for a fresh
database with only its title property. `fake-*` tools let tests inspect state and play the user editing in Notion.
"""
import json
import sys
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-notion")
LEGACY = {"Role": {"type": "title"}, "Company": {"type": "rich_text"}, "Source": {"type": "select"},
          "Job URL": {"type": "url"}, "Match Score": {"type": "number"}, "Status": {"type": "select"},
          "Tailored Summary": {"type": "rich_text"}}   # Applied Date intentionally missing
BARE = {"Name": {"type": "title"}}
SCHEMA = BARE if "bare" in sys.argv else LEGACY
PROPS: dict = {k: dict(v) for k, v in SCHEMA.items()}
PAGES: dict = {}      # id -> {"properties": {...request form}, "children": [...], "last_edited_time", "in_trash"}
CALLS: list = []


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:00.000Z")   # Notion rounds to the minute


def _err(status: int, code: str, message: str) -> str:
    return json.dumps({"object": "error", "status": status, "code": code, "message": message})


def _response_props(props: dict) -> dict:
    out = {}
    for name, v in props.items():
        t = (PROPS.get(name) or {}).get("type") or next(iter(v))
        val = v.get(t)
        if t in ("title", "rich_text"):
            val = [{**x, "plain_text": x["text"]["content"]} for x in val or []]
        out[name] = {"type": t, t: val}
    return out


def _page(pid: str) -> dict:
    p = PAGES[pid]
    return {"object": "page", "id": pid, "last_edited_time": p["last_edited_time"], "in_trash": p["in_trash"],
            "properties": _response_props(p["properties"])}


def _check_props(props: dict) -> str | None:
    unknown = [k for k in props if k not in PROPS]
    if unknown:
        return _err(400, "validation_error", f"{unknown[0]} is not a property that exists.")
    return None


@mcp.tool(name="API-retrieve-a-database")
def retrieve_database(database_id: str) -> str:
    CALLS.append("retrieve-database")
    if database_id != "db1": return _err(404, "object_not_found", "Could not find database")
    return json.dumps({"object": "database", "id": "db1", "data_sources": [{"id": "ds1", "name": "Jobs"}]})


@mcp.tool(name="API-retrieve-a-data-source")
def retrieve_data_source(data_source_id: str) -> str:
    CALLS.append("retrieve-data-source")
    return json.dumps({"object": "data_source", "id": data_source_id, "properties": PROPS})


@mcp.tool(name="API-update-a-data-source")
def update_data_source(data_source_id: str, properties: dict) -> str:
    CALLS.append("update-data-source")
    for name, spec in properties.items():
        t = next(iter(spec))
        PROPS[name] = {"type": t, **({t: spec[t]} if spec[t] else {})}
    return json.dumps({"object": "data_source", "id": data_source_id, "properties": PROPS})


@mcp.tool(name="API-post-page")
def post_page(parent: dict, properties: dict, children: list | None = None) -> str:
    CALLS.append("post-page")
    if parent.get("data_source_id") != "ds1": return _err(400, "validation_error", f"bad parent {parent}")
    if e := _check_props(properties): return e
    title = next(("".join(x["text"]["content"] for x in v["title"]) for v in properties.values() if "title" in v), "")
    if title == "reject me": return _err(400, "validation_error", "body failed validation: (fake) rejected title")
    pid = f"page-{len(PAGES) + 1}"
    PAGES[pid] = {"properties": dict(properties), "children": list(children or []), "last_edited_time": _now(),
                  "in_trash": False}
    return json.dumps(_page(pid))


@mcp.tool(name="API-patch-page")
def patch_page(page_id: str, properties: dict) -> str:
    CALLS.append("patch-page")
    p = PAGES.get(page_id)
    if p is None: return _err(404, "object_not_found", "Could not find page")
    if p["in_trash"]: return _err(400, "validation_error", "Can't edit block that is archived.")
    if e := _check_props(properties): return e
    p["properties"].update(properties); p["last_edited_time"] = _now()
    return json.dumps(_page(page_id))


@mcp.tool(name="API-patch-block-children")
def patch_block_children(block_id: str, children: list) -> str:
    CALLS.append("patch-block-children")
    if block_id not in PAGES: return _err(404, "object_not_found", "Could not find block")
    if len(children) > 100: return _err(400, "validation_error", "children length should be ≤ 100")
    PAGES[block_id]["children"] += children
    return json.dumps({"object": "list", "results": children})


@mcp.tool(name="API-query-data-source")
def query_data_source(data_source_id: str, filter: dict | None = None, start_cursor: str | None = None,
                      page_size: int = 100) -> str:
    CALLS.append("query-data-source")
    ids = sorted(PAGES)
    if filter and "property" in filter:
        want = filter["rich_text"]["equals"]
        ids = [i for i in ids if "".join(x["text"]["content"] for x in
                                         PAGES[i]["properties"].get(filter["property"], {}).get("rich_text", [])) == want]
    elif filter and filter.get("timestamp") == "last_edited_time":
        since = filter["last_edited_time"]["on_or_after"]
        ids = [i for i in ids if PAGES[i]["last_edited_time"] >= since.replace("+00:00", "")[:16]]
    start = int(start_cursor or 0)
    chunk = ids[start:start + page_size]
    more = start + page_size < len(ids)
    return json.dumps({"object": "list", "results": [_page(i) for i in chunk], "has_more": more,
                       "next_cursor": str(start + page_size) if more else None})


@mcp.tool(name="fake-edit-page")
def fake_edit_page(page_id: str, properties: dict | None = None, in_trash: bool | None = None) -> str:
    """Play the user editing (or deleting) a page in Notion."""
    p = PAGES[page_id]
    if properties: p["properties"].update(properties)
    if in_trash is not None: p["in_trash"] = in_trash
    p["last_edited_time"] = _now()
    return json.dumps(_page(page_id))


@mcp.tool(name="fake-dump")
def fake_dump() -> str:
    return json.dumps({"pages": PAGES, "props": PROPS, "calls": CALLS})


@mcp.tool(name="fake-add-page")
def fake_add_page(properties: dict) -> str:
    """A page created behind Seeko's back (e.g. by a sync whose response was lost)."""
    pid = f"page-{len(PAGES) + 1}"
    PAGES[pid] = {"properties": dict(properties), "children": [], "last_edited_time": _now(), "in_trash": False}
    return json.dumps({"id": pid})


if __name__ == "__main__": mcp.run()
