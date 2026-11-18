from __future__ import annotations
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from jobsearch import service
from jobsearch.spec import ALL_SOURCES, SearchSpec
from jobsearch.store import Store

router = APIRouter(prefix="/api/search")


class RunRequest(BaseModel):
    """Optional overrides of the saved profile/settings for this run only."""
    role_titles: list[str] | None = None
    locations: list[str] | None = None
    job_types: list[Literal["job", "internship"]] | None = None
    sources: list[str] | None = None
    max_details: int | None = Field(None, ge=1, le=500)


@router.post("/runs", status_code=202)
async def start(body: RunRequest | None = None):
    o = (body or RunRequest()).model_dump(exclude_none=True)
    if bad := [s for s in o.get("sources", []) if s not in ALL_SOURCES]:
        raise HTTPException(422, f"unknown source(s): {', '.join(bad)}")
    try:
        run_id = service.start_background(SearchSpec.from_profile(**o), trigger="api")
    except service.RunInProgress:
        raise HTTPException(409, "a job search is already running")
    return {"run_id": run_id}


@router.get("/runs")
def runs(limit: int = 20):
    return {"running": service.is_running(), "current": service.current_run_id(),
            "runs": Store().list_runs(min(max(limit, 1), 100))}


@router.post("/cancel")
def cancel():
    run_id = service.cancel()
    if run_id is None: raise HTTPException(409, "no job search is running")
    return {"run_id": run_id, "stopping": True}


@router.get("/runs/{run_id}")
def run(run_id: str):
    r = Store().get_run(run_id)
    if r is None: raise HTTPException(404)
    return r


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume(run_id: str):
    r = Store().get_run(run_id)
    if r is None: raise HTTPException(404)
    if r["status"] not in ("interrupted", "failed"):
        raise HTTPException(409, f"run is {r['status']}; only interrupted or failed runs can be resumed")
    if service.is_running(): raise HTTPException(409, "a job search is already running")
    from db import log

    async def go():
        try: await service.resume_run(run_id)
        except LookupError: log(f"job search {run_id}: no checkpoint to resume from (it was saved in memory only)", "warn")
        except (service.RunCancelled, Exception): pass   # recorded on the run
    service.spawn(go())
    return {"run_id": run_id, "resumed": True}
