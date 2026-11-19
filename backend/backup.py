"""Backup PostgreSQL server: a continuously updated copy of every Seeko table on a second database.

Set BACKUP_DATABASE_URL (postgresql://… on another machine or container, e.g. `docker compose up -d db-backup`, or
"embedded" for a second local server in data/postgres-backup). The backup gets the same Alembic schema; the app then
copies rows to it:

  * full check at start and every 6 h: compares (primary key, version) on both sides, copies what differs or is
    missing and deletes what the primary no longer has;
  * incremental every BACKUP_INTERVAL_S: rows whose version column moved since the last copy (with a few minutes of
    overlap, so a transaction that committed late is still caught; upserts make re-copies harmless).

The version column is `updated_at` for jobs and search runs (bumped by every ORM and Core update, db.py), `seen_at`
for rejected pages (bumped on re-read), and the insert time for sightings and logs, which never change.

Afterwards the backup's id sequences are moved past the copied ids, so it can take over as the primary at any time:
point DATABASE_URL at it. `python -m jobsearch restore-backup` copies it back into an (empty) primary instead.
LangGraph checkpoints are not copied: losing them only means an interrupted search cannot be resumed.
"""
from __future__ import annotations
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlalchemy import Engine, create_engine, delete, func, select, text
from sqlalchemy.engine import make_url
import db
from config.settings import DATA_DIR, get_settings
from db import AppState, Job, JobSighting, LogEntry, SavedAnswer, SearchRun, SeenUrl, utcnow

BACKUP_PG_DIR = DATA_DIR / "postgres-backup"
OVERLAP = timedelta(minutes=5)
FULL_EVERY_S = 6 * 3600
CHUNK = 500


@dataclass(frozen=True)
class Table:
    model: type
    pk: str
    version: str


# Parents before children (job_sightings references search_runs and jobs); deletes run in reverse.
TABLES = (Table(SearchRun, "id", "updated_at"), Table(Job, "id", "updated_at"), Table(JobSighting, "id", "seen_at"),
          Table(SeenUrl, "canonical_url", "seen_at"), Table(LogEntry, "id", "ts"), Table(SavedAnswer, "key", "updated_at"),
          Table(AppState, "key", "updated_at"))
SERIAL = (("job_sightings", "id"), ("logs", "id"))


def _insert(engine_or_conn, table):
    name = engine_or_conn.dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover
        raise RuntimeError(f"unsupported database dialect: {name}")
    return insert(table)


def _chunks(xs: list, n: int = CHUNK):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


class Mirror:
    """Copies Seeko's tables from `src` to `dst` (both already migrated to the same revision)."""

    def __init__(self, src: Engine, dst: Engine):
        self.src, self.dst = src, dst

    def _upsert(self, conn, t: Table, rows: list[dict]) -> int:
        if not rows: return 0
        tbl = t.model.__table__
        ins = _insert(conn, tbl)
        cols = [c.name for c in tbl.columns if c.name != t.pk]
        conn.execute(ins.on_conflict_do_update(index_elements=[t.pk], set_={c: ins.excluded[c] for c in cols}), rows)
        return len(rows)

    def _fetch(self, conn, t: Table, keys: list) -> list[dict]:
        tbl = t.model.__table__
        out = []
        for chunk in _chunks(keys):
            out += [dict(r) for r in conn.execute(select(tbl).where(tbl.c[t.pk].in_(chunk))).mappings()]
        return out

    def full(self) -> dict:
        """Make dst equal to src: copy changed / missing rows, delete rows src no longer has."""
        stats: dict[str, dict] = {}
        with self.src.connect() as sc, self.dst.begin() as dc:
            extra: dict[str, list] = {}
            for t in TABLES:
                tbl = t.model.__table__
                q = select(tbl.c[t.pk], tbl.c[t.version])
                have = dict(sc.execute(q).all())
                mine = dict(dc.execute(q).all())
                todo = [k for k, v in have.items() if k not in mine or mine[k] != v]
                copied = sum(self._upsert(dc, t, self._fetch(sc, t, keys)) for keys in _chunks(todo))
                extra[t.model.__tablename__] = [k for k in mine if k not in have]
                stats[t.model.__tablename__] = {"copied": copied}
            for t in reversed(TABLES):
                tbl, gone = t.model.__table__, extra[t.model.__tablename__]
                for chunk in _chunks(gone):
                    dc.execute(delete(tbl).where(tbl.c[t.pk].in_(chunk)))
                stats[t.model.__tablename__]["deleted"] = len(gone)
            fix_sequences(dc)
        return stats

    def incremental(self, since: datetime) -> dict:
        """Copy rows whose version column is at or after `since`."""
        stats: dict[str, dict] = {}
        with self.src.connect() as sc, self.dst.begin() as dc:
            for t in TABLES:
                tbl = t.model.__table__
                rows = [dict(r) for r in sc.execute(select(tbl).where(tbl.c[t.version] >= since)).mappings()]
                stats[t.model.__tablename__] = {"copied": sum(self._upsert(dc, t, c) for c in _chunks(rows))}
            fix_sequences(dc)
        return stats


def fix_sequences(conn) -> None:
    """After copying explicit ids, move PostgreSQL sequences past them so this database can accept new rows."""
    if conn.dialect.name != "postgresql": return
    for table, col in SERIAL:
        conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
                          f"COALESCE((SELECT MAX({col}) FROM {table}), 0) + 1, false)"))


def counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as c:
        return {t.model.__tablename__: c.scalar(select(func.count()).select_from(t.model.__table__)) or 0 for t in TABLES}


def redact(url: str) -> str:
    try: return make_url(url).render_as_string(hide_password=True)
    except Exception: return "(unparseable URL)"


class BackupService:
    def __init__(self, primary: Engine | None = None, url: str | None = None):
        self._primary, self._url = primary, url
        self._engine: Engine | None = None
        self._engine_url = ""
        self._embedded = None
        self._since: datetime | None = None
        self._full_at = 0.0
        self._lock = threading.Lock()
        self.state: dict = {"last_sync_at": None, "last_full_at": None, "last_error": "", "last_copied": 0,
                            "rows": {}, "running": False}

    def url(self) -> str:
        return (self._url if self._url is not None else get_settings().backup_database_url).strip()

    def configured(self) -> bool:
        return bool(self.url())

    def primary(self) -> Engine:
        return self._primary or db.get_engine()

    def target(self) -> Engine:
        """The backup engine, created (and migrated) on first use."""
        raw = self.url()
        if self._engine is not None and self._engine_url == raw:
            return self._engine
        if raw.lower() == "embedded":
            import pgserver
            BACKUP_PG_DIR.mkdir(parents=True, exist_ok=True)
            self._embedded = pgserver.get_server(str(BACKUP_PG_DIR), cleanup_mode="stop")
            url = db.normalize_url(self._embedded.get_uri())
        else:
            url = db.normalize_url(raw)
        if make_url(url) == self.primary().url:
            raise RuntimeError("BACKUP_DATABASE_URL points at the primary database; use a different server or database")
        if not url.startswith("postgresql"):
            raise RuntimeError("BACKUP_DATABASE_URL must be a postgresql:// URL (or 'embedded')")
        eng = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)
        db.migrate(eng)
        if self._engine is not None: self._engine.dispose()
        self._engine, self._engine_url, self._since = eng, raw, None
        return eng

    def sync(self, full: bool = False) -> dict:
        """One copy round (blocking; the loop runs it in a thread). Full at first, every 6 h, or when asked."""
        with self._lock:
            try:
                dst = self.target()
                started = utcnow()
                mirror = Mirror(self.primary(), dst)
                if full or self._since is None or time.monotonic() - self._full_at >= FULL_EVERY_S:
                    stats = mirror.full()
                    self._full_at = time.monotonic(); self.state["last_full_at"] = started.isoformat()
                else:
                    stats = mirror.incremental(self._since - OVERLAP)
                self._since = started
                self.state.update(last_sync_at=started.isoformat(), last_error="", rows=counts(dst),
                                  last_copied=sum(s["copied"] for s in stats.values()))
                return stats
            except Exception as e:
                self.state["last_error"] = f"{type(e).__name__}: {e}"[:500]
                raise

    def restore(self, force: bool = False) -> dict:
        """Copy the backup into the primary. Refuses a primary that has jobs unless `force` (which makes the primary
        an exact copy of the backup, deleting rows the backup does not have)."""
        with self._lock:
            src, dst = self.target(), self.primary()
            db.migrate(dst)
            if counts(dst)["jobs"] and not force:
                raise RuntimeError("the primary database already has jobs; pass --force to overwrite it with the backup")
            stats = Mirror(src, dst).full()
            self._since = None   # next sync: full check
            return stats

    def status(self) -> dict:
        return {"configured": self.configured(), "target": redact(self.url()) if self.url().lower() != "embedded" else
                f"embedded ({BACKUP_PG_DIR})", "interval_s": get_settings().backup_interval_s, **self.state}

    async def run(self) -> None:
        import asyncio
        self.state["running"] = True
        failures = 0
        try:
            while True:
                if self.configured():
                    try:
                        await asyncio.to_thread(self.sync)
                        failures = 0
                    except Exception as e:
                        failures += 1
                        if failures == 1: db.log(f"backup database sync failed: {e}", "warn")
                await asyncio.sleep(max(5, get_settings().backup_interval_s) * min(2 ** min(failures, 5), 20))
        finally:
            self.state["running"] = False


service = BackupService()
