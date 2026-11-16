from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field
from datetime import timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JobPosting(BaseModel):
    id: str = ""
    source: str
    company: str
    title: str
    location: str = ""
    url: str
    jd_text: str = ""
    is_easy_apply: bool = False
    discovered_at: datetime = Field(default_factory=utcnow)


class Score(BaseModel):
    score: int = Field(ge=0, le=100)
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    reasoning: str = ""
