from __future__ import annotations
import csv
import io
import json
from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from config.settings import ARTIFACTS_DIR
from db import Job, SessionLocal, utcnow
from jobsearch.store import jobs_query
from tailor.cover_letter import generate_cover_letter

router = APIRouter(prefix="/api/jobs")


_iso = lambda d: d.isoformat() if d else None


def ser(j: Job) -> dict:
    return {c: getattr(j, c) for c in ("id", "source", "company", "title", "location", "url", "is_easy_apply", "status", "score",
            "tailored_summary", "tailored_ok", "error", "notion_page_id", "job_type", "employment_type", "is_remote",
            "jd_source", "relevance", "times_seen")} | {
            "missing_keywords": json.loads(j.missing_keywords or "[]"), "discovered_at": j.discovered_at.isoformat(),
            "applied_at": _iso(j.applied_at), "posted_at": _iso(j.posted_at), "last_seen_at": _iso(j.last_seen_at),
            "closed_at": _iso(j.closed_at)}


State = Literal["open", "closed"]


@router.get("")
def list_jobs(status: str | None = None, source: str | None = None, q: str | None = None, job_type: str | None = None,
              state: State | None = None):
    """`q` searches title, company and the saved job description (full-text on PostgreSQL, ranked)."""
    with SessionLocal() as s:
        stmt = jobs_query(s, status=status, source=source, job_type=job_type, state=state, q=q, limit=500)
        return [ser(j) for j in s.scalars(stmt)]


EXPORT_COLUMNS = ("id", "job_type", "title", "company", "location", "is_remote", "source", "url", "status", "score",
                  "employment_type", "posted_at", "discovered_at", "last_seen_at", "times_seen", "closed_at", "applied_at",
                  "jd_text")


def export_rows(jobs: list[Job]) -> list[dict]:
    return [{c: (_iso(v) if hasattr(v, "isoformat") else v) for c in EXPORT_COLUMNS for v in [getattr(j, c)]} for j in jobs]


@router.get("/export")
def export(format: Literal["csv", "json"] = "csv", status: str | None = None, source: str | None = None,
           q: str | None = None, job_type: str | None = None, state: State | None = None):
    """Every matching job with its full description, as a CSV (spreadsheets) or JSON file."""
    with SessionLocal() as s:
        rows = export_rows(list(s.scalars(jobs_query(s, status=status, source=source, job_type=job_type, state=state,
                                                     q=q, limit=None))))
    name = f"seeko-jobs-{utcnow():%Y%m%d}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    if format == "json":
        return Response(json.dumps(rows, ensure_ascii=False, indent=1), media_type="application/json", headers=headers)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS)
    w.writeheader(); w.writerows(rows)
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)   # BOM: Excel


@router.get("/{job_id}")
def detail(job_id: str):
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        if not j: raise HTTPException(404)
        d = ser(j); a = ARTIFACTS_DIR / job_id
        rd = lambda n: (a / n).read_text() if (a / n).exists() else ""
        return d | {"diff": rd("resume.diff"), "base_tex": rd("base_resume.tex"), "tailored_tex": rd("tailored_resume.tex"),
                    "has_pdf": (a / "tailored_resume.pdf").exists(), "has_screenshot": (a / "filled_form.png").exists(), "cover_letter": rd("cover_letter.txt"), "tailor_report": json.loads(rd("tailor_report.json") or "null"), "jd_text": j.jd_text}


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
