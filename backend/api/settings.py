from __future__ import annotations
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from config.settings import get_settings, update_settings, DATA_DIR
from config.profile import CandidateProfile, load_profile, save_profile, load_resume, RESUME_PATH
from tailor.latex import compile_tex

router = APIRouter(prefix="/api")


class AppSettings(BaseModel):
    approval_mode: bool | None = None; daily_apply_cap: int | None = None; min_match_score: int | None = None
    enabled_sources: list[str] | None = None; notion_token: str | None = None; notion_database_id: str | None = None


@router.get("/settings")
def get():
    s = get_settings()
    return {"approval_mode": s.approval_mode, "daily_apply_cap": s.daily_apply_cap, "min_match_score": s.min_match_score,
            "enabled_sources": s.enabled_sources, "notion_token": "***" if s.notion_token else "", "notion_database_id": s.notion_database_id}


@router.put("/settings")
def put(b: AppSettings):
    d = b.model_dump()
    if d.get("notion_token") == "***": d["notion_token"] = None
    update_settings(**d); return {"ok": True}


@router.get("/profile")
def get_profile(): return load_profile()


@router.put("/profile")
def put_profile(p: CandidateProfile): save_profile(p); return {"ok": True}


@router.get("/resume")
def get_resume(): return {"tex": load_resume()}


@router.put("/resume")
async def put_resume(file: UploadFile | None = File(None), tex: str | None = None):
    src = (await file.read()).decode() if file else tex
    if not src: raise HTTPException(400, "no resume provided")
    ok, log = await compile_tex(src, DATA_DIR / "base_resume.pdf")
    if not ok: raise HTTPException(422, f"resume does not compile: {log[-600:]}")
    DATA_DIR.mkdir(exist_ok=True); RESUME_PATH.write_text(src); return {"ok": True}
