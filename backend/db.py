from __future__ import annotations
from datetime import datetime
from sqlalchemy import create_engine, String, Integer, Text, Boolean, DateTime, Float, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from config.settings import get_settings, DATA_DIR

DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(get_settings().database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase): pass


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("dedupe_key"), UniqueConstraint("canonical_url"))
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String)
    company: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String, default="")
    url: Mapped[str] = mapped_column(String)
    canonical_url: Mapped[str] = mapped_column(String)
    dedupe_key: Mapped[str] = mapped_column(String)
    jd_text: Mapped[str] = mapped_column(Text, default="")
    is_easy_apply: Mapped[bool] = mapped_column(Boolean, default=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String, default="scraped")  # scraped scored tailoring tailored applying needs_review applied failed skipped
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    missing_keywords: Mapped[str] = mapped_column(Text, default="[]")
    tailored_summary: Mapped[str] = mapped_column(Text, default="")
    tailored_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    notion_page_id: Mapped[str] = mapped_column(String, default="")
    notion_dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")


class LogEntry(Base):
    __tablename__ = "logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    level: Mapped[str] = mapped_column(String, default="info")
    job_id: Mapped[str] = mapped_column(String, default="")
    message: Mapped[str] = mapped_column(Text)


def init_db() -> None:
    Base.metadata.create_all(engine)


def log(message: str, level: str = "info", job_id: str = "") -> None:
    with SessionLocal() as s:
        s.add(LogEntry(message=message, level=level, job_id=job_id)); s.commit()
