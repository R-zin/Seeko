from __future__ import annotations
import json
from pydantic import BaseModel, Field
from .settings import DATA_DIR

PROFILE_PATH = DATA_DIR / "candidate_profile.json"
RESUME_PATH = DATA_DIR / "base_resume.tex"


class Personal(BaseModel):
    name: str = ""; email: str = ""; phone: str = ""; location: str = ""
    linkedin: str = ""; github: str = ""; portfolio: str = ""


class AppMeta(BaseModel):
    work_authorization: str = ""; needs_sponsorship: bool = False
    salary_expectation: str = ""; notice_period: str = ""; years_experience: float = 0


class Search(BaseModel):
    role_titles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_only: bool = False
    excluded_companies: list[str] = Field(default_factory=list)
    min_match_score: int = 60
    watchlist_urls: list[str] = Field(default_factory=list)   # ATS boards (API) or any careers page (browsed)
    job_types: list[str] = Field(default_factory=lambda: ["job", "internship"])
    max_age_days: int = 30      # skip postings older than this when the page states a date; 0 = no limit


class CandidateProfile(BaseModel):
    personal: Personal = Field(default_factory=Personal)
    application: AppMeta = Field(default_factory=AppMeta)
    search: Search = Field(default_factory=Search)


def load_profile() -> CandidateProfile:
    if PROFILE_PATH.exists():
        return CandidateProfile.model_validate(json.loads(PROFILE_PATH.read_text()))
    return CandidateProfile()


def save_profile(p: CandidateProfile) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(p.model_dump_json(indent=2))


def load_resume() -> str:
    return RESUME_PATH.read_text() if RESUME_PATH.exists() else ""
