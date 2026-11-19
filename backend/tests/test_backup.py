"""Backup server mirror: full check, incremental copy, deletes, sequences, restore - on SQLite and PostgreSQL."""
import uuid
from contextlib import contextmanager
import pytest
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
import db
from backup import BackupService, Mirror, counts
from db import Job, JobSighting, LogEntry, SearchRun, SeenUrl, utcnow
from jobsearch.store import Store
from tests.test_jobsearch_store import posting


@contextmanager
def pg_schema(server_url: str):
    """A migrated engine on a fresh PostgreSQL schema, plus a URL that reaches the same schema."""
    schema = "b_" + uuid.uuid4().hex[:10]
    admin = create_engine(server_url)
    with admin.begin() as c: c.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = create_engine(server_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        db.migrate(eng)
        url = make_url(server_url).update_query_dict({"options": f"-csearch_path={schema}"})
        yield eng, url.render_as_string(hide_password=False)
    finally:
        eng.dispose()
        with admin.begin() as c: c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def pair(request, tmp_path):
    if request.param == "sqlite":
        engines = [create_engine(f"sqlite:///{tmp_path / n}") for n in ("primary.db", "backup.db")]
        for e in engines: db.migrate(e)
        yield engines
        for e in engines: e.dispose()
    else:
        url = request.getfixturevalue("pg_server_url")
        with pg_schema(url) as (src, _), pg_schema(url) as (dst, _):
            yield [src, dst]


def seed(SL) -> list[str]:
    st = Store(SL)
    st.start_run("r1", {"role_titles": ["Engineer"]}, "test")
    ids = st.persist_run("r1", [posting(1), posting(2, job_type="internship")], [],
                         [{"url": "https://x.test/old", "source": "careers", "title": "Old", "reason": "expired"}])["new_job_ids"]
    st.finish_run("r1", "done", {"new": 2})
    with SL() as s:
        s.add_all([LogEntry(message="one"), LogEntry(message="two")]); s.commit()
    return ids


def rows(eng, model, *cols):
    with eng.connect() as c:
        return sorted(c.execute(select(*[model.__table__.c[x] for x in cols])).all())


def test_full_then_incremental_mirror(pair):
    src, dst = pair
    SL = sessionmaker(src, expire_on_commit=False)
    ids = seed(SL)
    stats = Mirror(src, dst).full()
    assert stats["jobs"]["copied"] == 2 and stats["logs"]["copied"] == 2 and stats["job_sightings"]["copied"] == 2
    assert counts(dst) == counts(src)
    assert rows(dst, Job, "id", "title", "jd_text", "updated_at") == rows(src, Job, "id", "title", "jd_text", "updated_at")
    assert rows(dst, SearchRun, "id", "status", "stats") == rows(src, SearchRun, "id", "status", "stats")
    assert Mirror(src, dst).full()["jobs"]["copied"] == 0          # nothing changed: nothing copied

    since = utcnow()
    with SL() as s:                                                 # ORM change, Core change, and a new row
        s.get(Job, ids[0]).application_status = "interviewing"; s.commit()
        s.execute(update(Job).where(Job.id == ids[1]).values(times_seen=Job.times_seen + 5)); s.commit()
        s.add(LogEntry(message="three")); s.commit()
    Store(SL).mark_interrupted()                                     # (no running runs: no-op)
    inc = Mirror(src, dst).incremental(since)
    assert inc["jobs"]["copied"] == 2 and inc["logs"]["copied"] == 1
    assert rows(dst, Job, "id", "application_status", "times_seen") == rows(src, Job, "id", "application_status", "times_seen")

    with SL() as s:                                                  # a row removed from the primary
        s.execute(delete(LogEntry).where(LogEntry.message == "one")); s.commit()
    assert Mirror(src, dst).full()["logs"]["deleted"] == 1
    assert rows(dst, LogEntry, "id", "message") == rows(src, LogEntry, "id", "message")

    # The backup can take over: its sequences are past the copied ids
    with sessionmaker(dst)() as s:
        s.add(LogEntry(message="written on the backup")); s.commit()
        assert s.scalar(select(LogEntry.id).where(LogEntry.message == "written on the backup")) > max(r[0] for r in rows(src, LogEntry, "id"))


def test_backup_refuses_the_primary_and_non_postgres_urls(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    with pytest.raises(RuntimeError, match="points at the primary"):
        BackupService(primary=eng, url=str(eng.url)).target()
    with pytest.raises(RuntimeError, match="postgresql"):
        BackupService(primary=eng, url=f"sqlite:///{tmp_path / 'other.db'}").target()
    svc = BackupService(primary=eng, url="")
    assert not svc.configured() and svc.status()["configured"] is False


def test_service_sync_and_restore(pg_server_url):
    with pg_schema(pg_server_url) as (primary, _), pg_schema(pg_server_url) as (_, backup_url):
        SL = sessionmaker(primary, expire_on_commit=False)
        seed(SL)
        svc = BackupService(primary=primary, url=backup_url)
        svc.sync()
        st = svc.status()
        assert st["last_full_at"] and st["rows"]["jobs"] == 2 and st["last_error"] == "" and st["configured"]
        with SL() as s:
            s.add(LogEntry(message="later")); s.commit()
        svc.sync()                                                   # incremental this time
        assert svc.status()["rows"]["logs"] == 3 and svc.status()["last_copied"] >= 1

        with pytest.raises(RuntimeError, match="already has jobs"):
            svc.restore()
        with SL() as s:                                              # primary lost its data
            for m in (JobSighting, LogEntry, SeenUrl, Job, SearchRun): s.execute(delete(m))
            s.commit()
        svc.restore()
        assert counts(primary) == {"search_runs": 1, "jobs": 2, "job_sightings": 2, "seen_urls": 1, "logs": 3,
                                   "answer_bank": 0, "app_state": 0}
        with SL() as s:                                              # and keeps working (sequences moved on)
            s.add(LogEntry(message="after restore")); s.commit()
