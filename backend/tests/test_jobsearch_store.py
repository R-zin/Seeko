"""Persistence on SQLite and on a real PostgreSQL (TEST_DATABASE_URL or embedded pgserver)."""
from datetime import timedelta
import json
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
import db
from db import Job, JobSighting, SeenUrl, utcnow
from jobsearch.store import Store

JD = "Full description. " * 30


def posting(n: int, **kw) -> dict:
    return {"source": "greenhouse", "task": "ats", "url": f"https://boards.greenhouse.io/acme/jobs/{n}?gh_src=x",
            "title": f"Engineer {n}", "company": "Acme", "location": "Kochi", "jd_text": JD, "job_type": "job",
            "jd_source": "api", "posted_at": "2026-11-01T00:00:00", "relevance": 0.9} | kw


def test_migrations_match_models(migrated):
    eng, _ = migrated
    with eng.connect() as c:
        mc = MigrationContext.configure(c, opts={"compare_type": True, "include_object":
                                                 lambda o, n, t, r, ct: not (t == "table" and r and ct is None and n not in db.Base.metadata.tables)})
        assert compare_metadata(mc, db.Base.metadata) == []
    db.migrate(eng)   # idempotent


def test_persist_new_known_and_idempotent(migrated):
    _, SL = migrated
    st = Store(SL)
    st.start_run("r1", {"role_titles": ["Engineer"]}, "test")
    res = st.persist_run("r1", [posting(1), posting(2, job_type="internship", title="Engineering Intern")], [],
                         [{"url": "https://x.test/old", "source": "careers", "title": "Old", "reason": "expired"}])
    assert len(res["new_job_ids"]) == 2
    again = st.persist_run("r1", [posting(1), posting(2, job_type="internship", title="Engineering Intern")], [], [])
    assert sorted(again["new_job_ids"]) == sorted(res["new_job_ids"])          # retried node: same answer, no dupes
    with SL() as s:
        jobs = {j.title: j for j in s.scalars(select(Job))}
        assert set(jobs) == {"Engineer 1", "Engineering Intern"}
        j = jobs["Engineering Intern"]
        assert j.job_type == "internship" and j.jd_text == JD and j.times_seen == 1 and j.first_run_id == "r1"
        assert j.canonical_url == "https://boards.greenhouse.io/acme/jobs/2" and j.posted_at.day == 1
        assert s.scalar(select(SeenUrl.reason)) == "expired"
        assert len(s.scalars(select(JobSighting)).all()) == 2

    # Next run sees job 1 again (as a known lead) and job 2 via another source (same company + title)
    st.start_run("r2", {}, "test")
    known = st.known(["https://boards.greenhouse.io/acme/jobs/1"])
    assert list(known) == ["https://boards.greenhouse.io/acme/jobs/1"]
    res2 = st.persist_run("r2", [posting(99, url="https://www.linkedin.com/jobs/view/123/", source="linkedin",
                                         title="Engineering Intern", job_type="internship")],
                          [{"job_id": known["https://boards.greenhouse.io/acme/jobs/1"], "source": "greenhouse",
                            "url": "https://boards.greenhouse.io/acme/jobs/1"}], [])
    assert res2["new_job_ids"] == [] and res2["known_bumped"] == 2
    st.persist_run("r2", [], [{"job_id": known["https://boards.greenhouse.io/acme/jobs/1"], "source": "greenhouse",
                               "url": "https://boards.greenhouse.io/acme/jobs/1"}], [])     # retry: no second bump
    with SL() as s:
        assert sorted(s.scalars(select(Job.times_seen))) == [2, 2] and s.scalar(select(Job).where(Job.title == "Engineer 1")).last_seen_at
    run = st.get_run("r2")
    assert {(x["title"], x["is_new"]) for x in run["jobs"]} == {("Engineer 1", False), ("Engineering Intern", False)}


def test_recently_rejected_window_and_interrupted_runs(migrated):
    _, SL = migrated
    st = Store(SL)
    st.start_run("r1", {}, "test")
    st.persist_run("r1", [], [], [{"url": "https://x.test/a", "source": "careers", "reason": "not_a_posting"}])
    assert st.recently_rejected(["https://x.test/a", "https://x.test/b"], 14) == {"https://x.test/a"}
    with SL() as s:
        s.get(SeenUrl, "https://x.test/a").seen_at = utcnow() - timedelta(days=30); s.commit()
    assert st.recently_rejected(["https://x.test/a"], 14) == set()          # re-read after the window
    assert st.mark_interrupted() == 1 and st.list_runs()[0]["status"] == "interrupted"
    st.finish_run("r1", "done", {"new": 0})
    assert st.get_run("r1")["stats"] == {"new": 0}


def test_legacy_sqlite_is_adopted_and_backfilled(tmp_path):
    """A database made by the old create_all(): stamped at the baseline, upgraded, internships classified."""
    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    from alembic import command
    cfg = db.alembic_config()
    with eng.begin() as c:
        cfg.attributes["connection"] = c
        command.upgrade(cfg, "0001")
        c.execute(text("DROP TABLE alembic_version"))
        for i, title in enumerate(["Data Science Intern", "International Sales Lead"]):
            c.execute(text("INSERT INTO jobs VALUES (:id,'x','Acme',:t,'','u'||:id,'u'||:id,:id,'jd',0,'2026-01-01 00:00:00',"
                           "'scraped',NULL,'[]','',0,'',1,NULL,'')"), {"id": str(i), "t": title})
    db.migrate(eng)
    with eng.connect() as c:
        rows = dict(c.execute(text("SELECT title, job_type FROM jobs")).all())
        assert rows == {"Data Science Intern": "internship", "International Sales Lead": "job"}
        assert c.execute(text("SELECT last_seen_at FROM jobs")).scalar() is not None
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"


@pytest.mark.postgres
def test_import_legacy_sqlite_into_postgres(tmp_path, pg_engine):
    src = create_engine(f"sqlite:///{tmp_path / 'seeko.db'}")     # the pre-migration schema, as old installs have it
    from alembic import command
    cfg = db.alembic_config()
    with src.begin() as c:
        cfg.attributes["connection"] = c
        command.upgrade(cfg, "0001")
        for jid, title, status in (("a1", "Engineer", "applied"), ("a2", "Data Intern", "scraped")):
            c.execute(text("INSERT INTO jobs VALUES (:id,'lever','Acme',:t,'','https://jobs.lever.co/acme/'||:id,"
                           "'https://jobs.lever.co/acme/'||:id,:id,:jd,0,'2026-01-01 00:00:00',:st,NULL,'[]','',0,'',1,NULL,'')"),
                      {"id": jid, "t": title, "jd": JD, "st": status})
        c.execute(text("INSERT INTO logs (ts, level, job_id, message) VALUES ('2026-01-01 00:00:00','info','','old log line')"))
    src.dispose()
    db.migrate(pg_engine)
    SL = sessionmaker(pg_engine, expire_on_commit=False)
    assert db.import_legacy_sqlite(tmp_path / "seeko.db", SL) == 2
    assert db.import_legacy_sqlite(tmp_path / "seeko.db", SL) == 0           # only into an empty database
    with SL() as s:
        j = s.get(Job, "a1")
        assert j.status == "applied" and j.jd_text == JD and j.job_type == "job"
        assert s.get(Job, "a2").job_type == "internship"
        s.add(db.LogEntry(message="new")); s.commit()                          # log id sequence still valid
        assert s.scalar(text("SELECT count(*) FROM logs")) == 2


@pytest.mark.postgres
def test_postgres_uses_jsonb(pg_engine):
    db.migrate(pg_engine)
    with pg_engine.connect() as c:
        types = dict(c.execute(text("SELECT column_name, data_type FROM information_schema.columns "
                                    "WHERE table_name='search_runs' AND table_schema=current_schema()")).all())
    assert types["spec"] == "jsonb" and types["stats"] == "jsonb"


def test_settings_file_cannot_pin_the_database(tmp_path, monkeypatch):
    from config import settings as cfg
    (tmp_path / "s.json").write_text(json.dumps({"database_url": "sqlite:////old/seeko.db", "llm_model": "m"}))
    monkeypatch.setattr(cfg, "OVERRIDES", tmp_path / "s.json")
    loaded = cfg._load()
    assert loaded.llm_model == "m" and loaded.database_url != "sqlite:////old/seeko.db"


def test_normalize_url():
    assert db.normalize_url("postgres://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert db.normalize_url("postgresql://u@/d?host=/tmp") == "postgresql+psycopg://u@/d?host=/tmp"
    assert db.normalize_url("sqlite:///x.db") == "sqlite:///x.db"
