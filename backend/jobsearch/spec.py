"""What one search run looks for and how politely it browses. Stored with the run, so every run is reproducible."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, field_validator

JobType = Literal["job", "internship"]
BROWSER_SOURCES = ("linkedin", "indeed", "glassdoor", "internshala")
ALL_SOURCES = ("watchlist",) + BROWSER_SOURCES


class SearchSpec(BaseModel):
    role_titles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_only: bool = False
    job_types: list[JobType] = Field(default_factory=lambda: ["job", "internship"])
    excluded_companies: list[str] = Field(default_factory=list)
    watchlist_urls: list[str] = Field(default_factory=list)   # ATS boards via API, any other careers page via browser
    sources: list[str] = Field(default_factory=lambda: ["watchlist"])
    max_age_days: int = Field(30, ge=0)

    # Politeness / budget
    max_queries: int = Field(6, ge=1, le=50)
    max_pages: int = Field(3, ge=1, le=20)
    max_details: int = Field(25, ge=1, le=500)
    revisit_days: int = Field(14, ge=0)
    delay_min_s: float = Field(3.0, ge=0)
    delay_max_s: float = Field(8.0, ge=0)
    respect_robots: bool = True
    llm_review: bool = True
    headless: bool = True

    @field_validator("role_titles", "keywords", "locations", "excluded_companies", "watchlist_urls", "sources")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        seen, out = set(), []
        for x in v:
            x = (x or "").strip()
            if x and x.lower() not in seen:
                seen.add(x.lower()); out.append(x)
        return out

    @field_validator("job_types")
    @classmethod
    def _types(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v)) or ["job", "internship"]

    @classmethod
    def from_profile(cls, profile=None, settings=None, **overrides) -> "SearchSpec":
        from config.profile import load_profile
        from config.settings import get_settings
        p, s = (profile or load_profile()).search, settings or get_settings()
        base = dict(
            role_titles=p.role_titles, keywords=p.keywords, locations=p.locations, remote_only=p.remote_only,
            job_types=p.job_types, excluded_companies=p.excluded_companies, watchlist_urls=p.watchlist_urls,
            sources=s.enabled_sources, max_age_days=p.max_age_days,
            max_queries=s.search_max_queries, max_pages=s.search_max_pages, max_details=s.search_max_details,
            revisit_days=s.search_revisit_days, delay_min_s=s.search_delay_min_s,
            delay_max_s=max(s.search_delay_max_s, s.search_delay_min_s), respect_robots=s.search_respect_robots,
            llm_review=s.search_llm_review, headless=s.search_headless)
        base.update({k: v for k, v in overrides.items() if v is not None})
        return cls.model_validate(base)
