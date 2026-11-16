from __future__ import annotations
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from config.settings import ARTIFACTS_DIR
from db import Job, SessionLocal

router = APIRouter(prefix="/api/jobs")


def ser(j: Job) -> dict:
    return {c: getattr(j, c) for c in ("id", "source", "company", "title", "location", "url", "is_easy_apply", "status", "score",
            "tailored_summary", "tailored_ok", "error", "notion_page_id")} | {
            "missing_keywords": json.loads(j.missing_keywords or "[]"), "discovered_at": j.discovered_at.isoformat(),
            "applied_at": j.applied_at.isoformat() if j.applied_at else None}


@router.get("")
def list_jobs(status: str | None = None, source: str | None = None, q: str | None = None):
    with SessionLocal() as s:
        qs = s.query(Job).order_by(Job.discovered_at.desc())
        if status: qs = qs.filter(Job.status == status)
        if source: qs = qs.filter(Job.source == source)
        if q: qs = qs.filter((Job.title.ilike(f"%{q}%")) | (Job.company.ilike(f"%{q}%")))
        return [ser(j) for j in qs.limit(500)]


@router.get("/{job_id}")
def detail(job_id: str):
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        if not j: raise HTTPException(404)
        d = ser(j); a = ARTIFACTS_DIR / job_id
        rd = lambda n: (a / n).read_text() if (a / n).exists() else ""
        return d | {"diff": rd("resume.diff"), "base_tex": rd("base_resume.tex"), "tailored_tex": rd("tailored_resume.tex"),
                    "has_pdf": (a / "tailored_resume.pdf").exists(), "has_screenshot": (a / "filled_form.png").exists(), "jd_text": j.jd_text}


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
