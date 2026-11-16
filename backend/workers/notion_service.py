"""Lazy singleton Notion MCP connection + best-effort sync. Failures never block the pipeline."""
from __future__ import annotations
import asyncio
from datetime import datetime
from config.settings import get_settings
from db import Job, SessionLocal, log
from mcp_notion.client import NotionMCP
from mcp_notion.schema import discover
from mcp_notion.sync import upsert_job

LABEL = {"scraped": "Scraped", "scored": "Scraped", "tailoring": "Scraped", "tailored": "Tailored",
         "applying": "Tailored", "needs_review": "Needs Review", "applied": "Applied", "failed": "Failed"}
_client: NotionMCP | None = None
_status_type = "select"; _lock = asyncio.Lock()


async def connect() -> dict:
    global _client, _status_type
    st = get_settings()
    if not (st.notion_token and st.notion_database_id): return {"ok": False, "error": "NOTION_TOKEN / NOTION_DATABASE_ID not set"}
    async with _lock:
        if _client: await _client.close()
        _client = NotionMCP(st.notion_token); tools = await _client.connect()
        schema = await discover(_client, st.notion_database_id); _status_type = schema.get("status_type") or "select"
        return {**schema, "tools": tools}


async def sync_job(job_id: str) -> None:
    st = get_settings()
    if not (st.notion_token and st.notion_database_id): return
    try:
        if _client is None: await connect()
        with SessionLocal() as s:
            j = s.get(Job, job_id)
            if j is None or j.status == "skipped": return
            data = dict(title=j.title, company=j.company, source=j.source, url=j.url, score=j.score, summary=j.tailored_summary,
                        status_label=LABEL.get(j.status, "Scraped"), page_id=j.notion_page_id,
                        applied_at=j.applied_at.date().isoformat() if j.applied_at else None)
        async with _lock: pid = await upsert_job(_client, st.notion_database_id, data, _status_type)
        with SessionLocal() as s:
            j = s.get(Job, job_id); j.notion_page_id = pid; j.notion_dirty = False; s.commit()
    except Exception as e:
        log(f"notion sync failed: {e}", "warn", job_id)   # stays notion_dirty=True; retried next cycle


async def retry_dirty() -> None:
    with SessionLocal() as s: ids = [j.id for j in s.query(Job).filter(Job.notion_dirty.is_(True)).all()]
    for i in ids: await sync_job(i)
