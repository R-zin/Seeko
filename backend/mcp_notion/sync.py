from __future__ import annotations
from datetime import datetime
from .client import NotionMCP

STATUSES = ["Scraped", "Tailored", "Needs Review", "Applied", "Failed"]


def _status_prop(kind: str, value: str): return {kind: {"name": value}} if kind in ("select", "status") else None


def build_props(job: dict, status_type: str = "select") -> dict:
    props = {
        "Role": {"title": [{"text": {"content": job["title"][:200]}}]},
        "Company": {"rich_text": [{"text": {"content": job["company"][:200]}}]},
        "Source": {"select": {"name": job["source"]}},
        "Job URL": {"url": job["url"]},
        "Status": _status_prop(status_type, job["status_label"]),
        "Tailored Summary": {"rich_text": [{"text": {"content": (job.get("summary") or "")[:1900]}}]},
    }
    if job.get("score") is not None: props["Match Score"] = {"number": job["score"]}
    if job.get("applied_at"): props["Applied Date"] = {"date": {"start": job["applied_at"]}}
    return props


async def upsert_job(client: NotionMCP, database_id: str, job: dict, status_type: str = "select") -> str:
    """Create page if job has no page id, else update. Returns page id."""
    props = build_props(job, status_type)
    if job.get("page_id"):
        tool = client.find_tool("patch", "page")
        await client.call(tool, {"page_id": job["page_id"], "properties": props})
        return job["page_id"]
    tool = client.find_tool("post", "page")
    res = await client.call(tool, {"parent": {"database_id": database_id}, "properties": props})
    return res["id"]
