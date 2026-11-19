from __future__ import annotations
import csv
import io
import json
from datetime import date, datetime, time
from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from config.settings import ARTIFACTS_DIR
from db import APPLICATION_STATUSES, Job, SessionLocal, update_application, utcnow
from jobsearch.store import jobs_query
from tailor.cover_letter import generate_cover_letter
from workers.notion_service import NotionSync
from applier.answers import questions_for

router = APIRouter(prefix="/api/jobs")


_iso = lambda d: d.isoformat() if d else None


def ser(j: Job) -> dict:
    return {c: getattr(j, c) for c in ("id", "source", "company", "title", "location", "url", "is_easy_apply", "status", "score",
            "tailored_summary", "tailored_ok", "error", "notion_page_id", "job_type", "employment_type", "is_remote",
            "jd_source", "relevance", "times_seen", "application_status", "next_step", "notes", "notion_error", "origin",
            "attempts")} | {"retry_at": _iso(j.retry_at)} | {
            "missing_keywords": json.loads(j.missing_keywords or "[]"), "discovered_at": j.discovered_at.isoformat(),
            "applied_at": _iso(j.applied_at), "posted_at": _iso(j.posted_at), "last_seen_at": _iso(j.last_seen_at),
            "closed_at": _iso(j.closed_at), "valid_through": _iso(j.valid_through), "next_step_date": _iso(j.next_step_date),
            "notion_state": notion_state(j)}


def notion_state(j: Job) -> str:
    """synced | pending | error | deleted | off (Notion not connected, or never pushed, e.g. a skipped job)"""
    if not NotionSync.configured(): return "synced" if j.notion_page_id and not j.notion_dirty else "off"
    if (j.notion_base or {}).get("gone"): return "deleted"
    if j.notion_error: return "error"
    if j.notion_dirty and (j.notion_page_id or j.status != "skipped"): return "pending"
    return "synced" if j.notion_page_id else "off"


State = Literal["open", "closed"]


@router.get("")
def list_jobs(status: str | None = None, source: str | None = None, q: str | None = None, job_type: str | None = None,
              state: State | None = None, application_status: str | None = None):
    """`q` searches title, company and the saved job description (full-text on PostgreSQL, ranked)."""
    with SessionLocal() as s:
        stmt = jobs_query(s, status=status, source=source, job_type=job_type, state=state, q=q, limit=500,
                          application_status=application_status)
        return [ser(j) for j in s.scalars(stmt)]


EXPORT_COLUMNS = ("id", "job_type", "title", "company", "location", "is_remote", "source", "url", "status", "score",
                  "employment_type", "posted_at", "discovered_at", "last_seen_at", "times_seen", "closed_at", "applied_at",
                  "application_status", "next_step", "next_step_date", "notes", "jd_text")


def export_rows(jobs: list[Job]) -> list[dict]:
    return [{c: (_iso(v) if hasattr(v, "isoformat") else v) for c in EXPORT_COLUMNS for v in [getattr(j, c)]} for j in jobs]


@router.get("/export")
def export(format: Literal["csv", "json"] = "csv", status: str | None = None, source: str | None = None,
           q: str | None = None, job_type: str | None = None, state: State | None = None,
           application_status: str | None = None):
    """Every matching job with its full description, as a CSV (spreadsheets) or JSON file."""
    with SessionLocal() as s:
        rows = export_rows(list(s.scalars(jobs_query(s, status=status, source=source, job_type=job_type, state=state,
                                                     q=q, limit=None, application_status=application_status))))
    name = f"seeko-jobs-{utcnow():%Y%m%d}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    if format == "json":
        return Response(json.dumps(rows, ensure_ascii=False, indent=1), media_type="application/json", headers=headers)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS)
    w.writeheader(); w.writerows(rows)
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)   # BOM: Excel


class AddJob(BaseModel):
    """A job you found: its link, or what you pasted, or a bookmarklet capture (page text + JSON-LD from your browser)."""
    url: str = Field("", max_length=2000)
    title: str = Field("", max_length=300)
    company: str = Field("", max_length=300)
    location: str = Field("", max_length=300)
    description: str = Field("", max_length=100_000)
    capture: dict | None = None
    mode: Literal["apply", "applied"] = "apply"
    preview: bool = False


@router.get("/bookmarklet")
def get_bookmarklet(origin: str):
    """Link to drag to the bookmarks bar: on any job page, it sends that page to Seeko's Add job form."""
    from jobsearch.intake import IntakeError, bookmarklet
    try: return {"href": bookmarklet(origin)}
    except IntakeError as e: raise HTTPException(422, str(e))


@router.post("/add")
async def add_job(b: AddJob):
    """Preview (`preview: true`) or store one job. Mode "apply": Seeko scores, tailors and fills it; "applied": tracked only."""
    from jobsearch.intake import IntakeError, add_job as intake
    try:
        res = await intake(url=b.url, title=b.title, company=b.company, location=b.location, description=b.description,
                           capture=b.capture, mode=b.mode, preview=b.preview)
    except IntakeError as e:
        raise HTTPException(422, str(e))
    if res["preview"]: return res
    with SessionLocal() as s:
        return {"job": ser(s.get(Job, res["job_id"])), "created": res["created"], "preview": False}


class Tracking(BaseModel):
    """Your own tracking of a job. Only the fields sent are changed; the change is pushed to Notion right away."""
    application_status: str | None = Field(None, max_length=60)
    applied_at: date | None = None
    next_step: str | None = Field(None, max_length=300)
    next_step_date: date | None = None
    notes: str | None = Field(None, max_length=20000)


@router.get("/application-statuses")
def application_statuses():
    return [{"key": k, "label": v} for k, v in APPLICATION_STATUSES.items()]


@router.patch("/{job_id}")
def update_tracking(job_id: str, b: Tracking):
    fields = b.model_dump(exclude_unset=True)
    if "application_status" in fields and not (fields["application_status"] or "").strip():
        raise HTTPException(422, "application_status cannot be empty")
    if "applied_at" in fields and fields["applied_at"] is not None:
        fields["applied_at"] = datetime.combine(fields["applied_at"], time(12))   # a day, stored as naive UTC noon
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        if not j: raise HTTPException(404)
        update_application(j, **fields)
        s.commit()
        return ser(j)


@router.get("/{job_id}")
def detail(job_id: str):
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        if not j: raise HTTPException(404)
        d = ser(j); a = ARTIFACTS_DIR / job_id
        rd = lambda n: (a / n).read_text() if (a / n).exists() else ""
        return d | {"diff": rd("resume.diff"), "base_tex": rd("base_resume.tex"), "tailored_tex": rd("tailored_resume.tex"),
                    "has_pdf": (a / "tailored_resume.pdf").exists(), "has_screenshot": (a / "filled_form.png").exists(), "cover_letter": rd("cover_letter.txt"), "tailor_report": json.loads(rd("tailor_report.json") or "null"), "jd_text": j.jd_text,
                    "questions": questions_for(j.waiting_on) if j.status == "needs_review" else []}


@router.get("/{job_id}/pdf")
def pdf(job_id: str):
    p = ARTIFACTS_DIR / job_id / "tailored_resume.pdf"
    if not p.exists(): raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf")


@router.get("/{job_id}/screenshot")
def shot(job_id: str):
    p = ARTIFACTS_DIR / job_id / "filled_form.png"
    if not p.exists(): raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


@router.post("/{job_id}/cover-letter")
async def cover_letter(job_id: str):
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        if not j: raise HTTPException(404)
        title, company, jd = j.title, j.company, j.jd_text
    return {"letter": await generate_cover_letter(job_id, title, company, jd)}
