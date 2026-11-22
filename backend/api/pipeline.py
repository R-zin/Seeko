from __future__ import annotations
import asyncio
from fastapi import APIRouter, HTTPException
from workers import pipeline as P
from scraper.browser import interactive_login
from scraper.boards import LOGIN_URLS
from jobsearch import service as search
from applier.gate import applied_today
from config.settings import get_settings

router = APIRouter(prefix="/api/pipeline")


@router.get("/status")
def status():
    return {"paused": P.state["paused"], "running": P.state["running"], "searching": search.is_running(), "applied_today": applied_today(),
            "ready_to_submit": len(P.ready_ids()),
            "daily_cap": get_settings().daily_apply_cap, "llm_configured": P.llm_configured(), "approval_mode": get_settings().approval_mode}


@router.post("/discover")
async def discover():
    try: run_id = search.start_background(trigger="api")
    except search.RunInProgress: raise HTTPException(409, "a job search is already running")
    return {"started": True, "run_id": run_id}


@router.post("/pause")
def pause(): P.state["paused"] = True; return {"paused": True}


@router.post("/resume")
def resume(): P.state["paused"] = False; return {"paused": False}


@router.post("/jobs/{job_id}/approve")
async def approve(job_id: str):
    if not await P.approve(job_id): raise HTTPException(409, "job is not awaiting approval (browser session may have been lost after a restart)")
    return {"ok": True}


@router.post("/jobs/{job_id}/skip")
async def skip(job_id: str): await P.skip(job_id); return {"ok": True}


@router.post("/approve-ready")
async def approve_ready():
    """Submit every application whose form is completely filled and waiting for your approval."""
    done = await P.approve_ready()
    return {"ok": True, "approved": done}


@router.post("/jobs/{job_id}/refill")
async def refill(job_id: str):
    """Fill a paused job's form again (e.g. after answering its questions)."""
    if not await P.refill(job_id): raise HTTPException(409, "only jobs waiting in review can be filled again")
    return {"ok": True}


@router.post("/jobs/{job_id}/retry")
def retry(job_id: str):
    st = P.retry(job_id)
    if not st: raise HTTPException(409, "only failed or skipped jobs can be retried")
    return {"ok": True, "status": st}


@router.post("/login/{source}")
async def login(source: str):
    if source not in LOGIN_URLS: raise HTTPException(404, "unknown source")
    asyncio.create_task(interactive_login(source)); return {"opened": source, "note": "sign in, then close the browser window"}
