"""Database models and engine.

PostgreSQL is the primary store: set DATABASE_URL (postgresql://user:pass@host/db), or leave it empty and an embedded
PostgreSQL server (pgserver) is started with its data in data/postgres. SQLite URLs still work (tests, legacy installs).
The engine is created lazily so importing this module never starts a database. Schema changes go through Alembic
(backend/migrations); `init_db()` upgrades to head and adopts databases created before migrations existed.
"""
from __future__ import annotations
import threading
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)   # naive UTC, as stored

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
                        create_engine, false, func, inspect, select)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from config.settings import get_settings, DATA_DIR

BACKEND_DIR = Path(__file__).resolve().parent
PG_DATA_DIR = DATA_DIR / "postgres"
LEGACY_SQLITE = DATA_DIR / "seeko.db"
BASELINE_REVISION = "0001"
JSONType = JSON().with_variant(JSONB(), "postgresql")
# Full-text document for job search on PostgreSQL. Must stay identical to the GIN index in migration 0003.
FTS_DOC = "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(company, '') || ' ' || coalesce(jd_text, ''))"
UNMODELED_INDEXES = {"ix_jobs_fts"}   # expression index created by migration 0003 (PostgreSQL only)


class Base(DeclarativeBase): pass


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("dedupe_key"), UniqueConstraint("canonical_url"),
                      Index("ix_jobs_status", "status"), Index("ix_jobs_job_type", "job_type"),
                      Index("ix_jobs_discovered_at", "discovered_at"), Index("ix_jobs_closed_at", "closed_at"))
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String)
    company: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String, default="")
    url: Mapped[str] = mapped_column(String)
    canonical_url: Mapped[str] = mapped_column(String)
    dedupe_key: Mapped[str] = mapped_column(String)
    jd_text: Mapped[str] = mapped_column(Text, default="")        # full job description, plain text
    is_easy_apply: Mapped[bool] = mapped_column(Boolean, default=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String, default="scraped")  # scraped scored tailoring tailored applying needs_review applied failed skipped
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    missing_keywords: Mapped[str] = mapped_column(Text, default="[]")
    tailored_summary: Mapped[str] = mapped_column(Text, default="")
    tailored_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    notion_page_id: Mapped[str] = mapped_column(String, default="")
    notion_dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    # Job search tracking (migration 0002)
    job_type: Mapped[str] = mapped_column(String, default="job", server_default="job")   # job | internship
    employment_type: Mapped[str] = mapped_column(String, default="", server_default="")  # as stated by the posting
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_through: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    jd_source: Mapped[str] = mapped_column(String, default="", server_default="")   # api | json-ld | selector | page
    relevance: Mapped[float | None] = mapped_column(Float, nullable=True)            # 0..1 match to the search
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    times_seen: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    first_run_id: Mapped[str] = mapped_column(String, default="", server_default="")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # gone from its board / expired (0003)


class LogEntry(Base):
    __tablename__ = "logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    level: Mapped[str] = mapped_column(String, default="info")
    job_id: Mapped[str] = mapped_column(String, default="")
    message: Mapped[str] = mapped_column(Text)


class SearchRun(Base):
    """One execution of the LangGraph job search. The id is also the LangGraph thread id (checkpoints)."""
    __tablename__ = "search_runs"
    __table_args__ = (Index("ix_search_runs_started_at", "started_at"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")   # running done failed interrupted
    trigger: Mapped[str] = mapped_column(String, default="manual")   # manual schedule cli api
    spec: Mapped[dict] = mapped_column(JSONType, default=dict)
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class JobSighting(Base):
    """Every time a search run finds a job (new or already known). Unique per (run, job), so re-runs are idempotent."""
    __tablename__ = "job_sightings"
    __table_args__ = (UniqueConstraint("run_id", "job_id"), Index("ix_job_sightings_job_id", "job_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("search_runs.id", ondelete="CASCADE"))
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    is_new: Mapped[bool] = mapped_column(Boolean, default=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SeenUrl(Base):
    """Pages that were read and rejected (expired, not a posting, wrong location...). Not re-read for a while."""
    __tablename__ = "seen_urls"
    canonical_url: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String, default="")
    reason: Mapped[str] = mapped_column(String)
    run_id: Mapped[str] = mapped_column(String, default="")
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ── Engine ──────────────────────────────────────────────────────────────────────────────────────────────────────
_engine: Engine | None = None
_engine_lock = threading.Lock()
_embedded = None


def normalize_url(url: str) -> str:
    """postgres:// and postgresql:// use psycopg 3 (the driver installed with this app)."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def embedded_postgres_url() -> str:
    """Start (or attach to) the embedded PostgreSQL server whose data lives in data/postgres."""
    global _embedded
    try:
        import pgserver
    except ImportError as e:  # pragma: no cover - pgserver is a declared dependency
        raise RuntimeError("DATABASE_URL is not set and the embedded PostgreSQL package (pgserver) is not installed. "
                           "Set DATABASE_URL=postgresql://user:pass@host:5432/seeko or `pip install pgserver`.") from e
    if _embedded is None:
        PG_DATA_DIR.mkdir(parents=True, exist_ok=True)
        _embedded = pgserver.get_server(str(PG_DATA_DIR), cleanup_mode="stop")   # data persists; process stops on exit
    return _embedded.get_uri()


def database_url() -> str:
    return normalize_url(get_settings().database_url.strip() or embedded_postgres_url())


def get_engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            url = database_url()
            if url.startswith("sqlite"):
                db_path = make_url(url).database
                if db_path and db_path != ":memory:": Path(db_path).parent.mkdir(parents=True, exist_ok=True)
                _engine = create_engine(url, connect_args={"check_same_thread": False})
            else:
                _engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
            SessionLocal.configure(bind=_engine)
    return _engine


class _LazySessionmaker(sessionmaker):
    """Binds to the engine on first use, so `from db import SessionLocal` stays import-time cheap."""
    def __call__(self, **local_kw):
        if self.kw.get("bind") is None and "bind" not in local_kw:
            get_engine()
        return super().__call__(**local_kw)


SessionLocal = _LazySessionmaker(expire_on_commit=False)


def alembic_include_object(obj, name, type_, reflected, compare_to) -> bool:
    """What Alembic compares: our tables only (LangGraph's checkpoint tables share the database), and not the
    expression index that only migration 0003 knows about."""
    if type_ == "table" and reflected and compare_to is None:
        return name in Base.metadata.tables
    if type_ == "index" and name in UNMODELED_INDEXES:
        return False
    return True


def alembic_config():
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return cfg


def migrate(engine: Engine | None = None) -> None:
    """Upgrade the schema to head. Databases created by create_all() before migrations existed are stamped first."""
    from alembic import command
    eng = engine or get_engine()
    cfg = alembic_config()
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        insp = inspect(conn)
        if insp.has_table("jobs") and not insp.has_table("alembic_version"):
            cols = {c["name"] for c in insp.get_columns("jobs")}
            command.stamp(cfg, "head" if "job_type" in cols else BASELINE_REVISION)
        command.upgrade(cfg, "head")


def init_db() -> None:
    eng = get_engine()
    migrate(eng)
    if eng.dialect.name == "postgresql":
        n = import_legacy_sqlite()
        if n: log(f"imported {n} job(s) from the previous SQLite database ({LEGACY_SQLITE.name}); the file was left untouched")


def import_legacy_sqlite(path: Path = LEGACY_SQLITE, session_factory=None) -> int:
    """First boot on PostgreSQL: copy jobs and logs from the old SQLite file so already-found jobs stay known.
    Runs only while the target has no jobs; never modifies the SQLite file. Returns the number of jobs copied."""
    sf = session_factory or SessionLocal
    if not path.exists():
        return 0
    with sf() as s:
        if s.scalar(select(func.count()).select_from(Job)):
            return 0
    from sqlalchemy import MetaData, Table
    src = create_engine(f"sqlite:///{path}")
    try:
        md = MetaData()
        if not inspect(src).has_table("jobs"):
            return 0
        jobs_t = Table("jobs", md, autoload_with=src)
        logs_t = Table("logs", md, autoload_with=src) if inspect(src).has_table("logs") else None
        job_cols = {c.key for c in Job.__table__.columns}
        with src.connect() as c:
            jobs = [{k: v for k, v in dict(r._mapping).items() if k in job_cols} for r in c.execute(select(jobs_t))]
            logs = [dict(r._mapping) for r in c.execute(select(logs_t))] if logs_t is not None else []
    finally:
        src.dispose()
    from jobsearch.classify import classify_job_type
    with sf() as s:
        for j in jobs:
            # Internship if either the stored value or the posting itself says so (old files have no job_type column).
            by_posting = classify_job_type(j.get("title") or "", j.get("employment_type") or "", j.get("url") or "")
            j["job_type"] = "internship" if "internship" in (j.get("job_type"), by_posting) else "job"
            j["last_seen_at"] = j.get("last_seen_at") or j.get("discovered_at")
            s.merge(Job(**j))
        for entry in logs:
            entry.pop("id", None)   # let the target assign ids (keeps its sequence valid)
            s.add(LogEntry(**entry))
        s.commit()
    return len(jobs)


def log(message: str, level: str = "info", job_id: str = "") -> None:
    with SessionLocal() as s:
        s.add(LogEntry(message=message, level=level, job_id=job_id)); s.commit()
