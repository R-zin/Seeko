from __future__ import annotations
from .client import NotionMCP

REQUIRED = {"Role": "title", "Company": "rich_text", "Source": {"select", "rich_text"}, "Job URL": "url",
            "Match Score": "number", "Status": {"select", "status"}, "Tailored Summary": "rich_text", "Applied Date": "date"}


def validate_schema(db: dict) -> dict:
    props = db.get("properties", {})
    missing, wrong = [], []
    for name, want in REQUIRED.items():
        p = props.get(name)
        if not p: missing.append(name); continue
        ok = p["type"] in (want if isinstance(want, set) else {want})
        if not ok: wrong.append({"property": name, "found": p["type"], "expected": sorted(want) if isinstance(want, set) else want})
    return {"ok": not missing and not wrong, "missing": missing, "wrong_type": wrong,
            "status_type": (props.get("Status") or {}).get("type")}


async def discover(client: NotionMCP, database_id: str) -> dict:
    tool = client.find_tool("retrieve", "database")
    db = await client.call(tool, {"database_id": database_id})
    return validate_schema(db)
