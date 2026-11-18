from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel, Field
from config.settings import get_settings, update_settings
from config.profile import CandidateProfile, load_profile, save_profile

router = APIRouter(prefix="/api")


SEARCH_KEYS = ("search_max_queries", "search_max_pages", "search_max_details", "search_delay_min_s", "search_delay_max_s",
               "search_revisit_days", "search_respect_robots", "search_llm_review", "indeed_host")


class AppSettings(BaseModel):
    approval_mode: bool | None = None; daily_apply_cap: int | None = None; discovery_interval_min: int | None = None; min_match_score: int | None = None
    enabled_sources: list[str] | None = None; notion_token: str | None = None; notion_database_id: str | None = None
    search_max_queries: int | None = Field(None, ge=1, le=50); search_max_pages: int | None = Field(None, ge=1, le=20)
    search_max_details: int | None = Field(None, ge=1, le=500); search_delay_min_s: float | None = Field(None, ge=0, le=120)
    search_delay_max_s: float | None = Field(None, ge=0, le=300); search_revisit_days: int | None = Field(None, ge=0, le=365)
    search_respect_robots: bool | None = None; search_llm_review: bool | None = None; indeed_host: str | None = None


@router.get("/settings")
def get():
    s = get_settings()
    return {"approval_mode": s.approval_mode, "daily_apply_cap": s.daily_apply_cap, "discovery_interval_min": s.discovery_interval_min, "min_match_score": s.min_match_score,
            "enabled_sources": s.enabled_sources, "notion_token": "***" if s.notion_token else "", "notion_database_id": s.notion_database_id} | {
            k: getattr(s, k) for k in SEARCH_KEYS}


@router.put("/settings")
def put(b: AppSettings):
    d = b.model_dump()
    if d.get("notion_token") == "***": d["notion_token"] = None
    update_settings(**d); return {"ok": True}


@router.get("/profile")
def get_profile(): return load_profile()


@router.put("/profile")
def put_profile(p: CandidateProfile): save_profile(p); return {"ok": True}
