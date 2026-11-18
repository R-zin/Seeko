"""Persistence on SQLite and on a real PostgreSQL (TEST_DATABASE_URL or embedded pgserver)."""
from datetime import timedelta
import json
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, select, text, update
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
        mc = MigrationContext.configure(c, opts={"compare_type": True, "include_object": db.alembic_include_object})
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
        from alembic.script import ScriptDirectory
        head = ScriptDirectory.from_config(db.alembic_config()).get_current_head()
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == head


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


def test_closed_postings_and_reopening(migrated):
    _, SL = migrated
    st = Store(SL)
    st.start_run("r1", {}, "test")
    st.persist_run("r1", [posting(1), posting(2), posting(3, valid_through="2020-01-01")], [], [])
    with SL() as s:
        ids = {j.title: j.id for j in s.scalars(select(Job))}
    with SL() as s:                                       # job 3 was past its expiry date when it was saved
        assert {j.title for j in s.scalars(select(Job).where(Job.closed_at.is_not(None)))} == {"Engineer 3"}
    # r2: the full Greenhouse board for "acme" only lists job 1 -> job 2 has closed
    st.start_run("r2", {}, "test")
    res = st.persist_run("r2", [], [], [], [{"source": "greenhouse", "company": "Acme", "urls": ["https://boards.greenhouse.io/acme/jobs/1"]}])
    with SL() as s:
        closed = {j.title for j in s.scalars(select(Job).where(Job.closed_at.is_not(None)))}
    assert closed == {"Engineer 2", "Engineer 3"} and res["closed"] == 1
    # r3: job 2 is seen again -> reopened
    st.start_run("r3", {}, "test")
    res = st.persist_run("r3", [], [{"job_id": ids["Engineer 2"], "source": "greenhouse",
                                     "url": "https://boards.greenhouse.io/acme/jobs/2"}], [])
    assert res["reopened"] == 1
    with SL() as s:
        assert s.get(Job, ids["Engineer 2"]).closed_at is None


def test_jobs_query_filters_and_searches_descriptions(migrated):
    from jobsearch.store import jobs_query
    eng, SL = migrated
    st = Store(SL)
    st.start_run("r1", {}, "test")
    st.persist_run("r1", [posting(1, title="Backend Engineer", jd_text=JD + " We run Kubernetes clusters."),
                          posting(2, title="Data Engineering Intern", job_type="internship", jd_text=JD + " Airflow and dbt."),
                          posting(3, title="Frontend Engineer", jd_text=JD + " React; no Kubernetes needed.")], [], [])
    with SL() as s:
        titles = lambda **kw: [j.title for j in s.scalars(jobs_query(s, **kw))]
        assert set(titles(q="kubernetes")) == {"Backend Engineer", "Frontend Engineer"}     # found in the description
        assert titles(q="airflow", job_type="internship") == ["Data Engineering Intern"]
        assert titles(q="Front") == ["Frontend Engineer"]                                  # partial title match
        assert titles(q="100%_") == []                                                      # LIKE wildcards are literal
        s.execute(update(Job).where(Job.title == "Frontend Engineer").values(closed_at=utcnow())); s.commit()
        assert set(titles(state="open")) == {"Backend Engineer", "Data Engineering Intern"}
        assert titles(state="closed") == ["Frontend Engineer"]
        if eng.dialect.name == "postgresql":
            assert titles(q='kubernetes -react') == ["Backend Engineer"]                     # websearch syntax
            s.execute(text("SET enable_seqscan = off"))
            plan = "\n".join(r[0] for r in s.execute(text(
                f"EXPLAIN SELECT id FROM jobs WHERE {db.FTS_DOC} @@ websearch_to_tsquery('english', 'kubernetes')")))
            assert "ix_jobs_fts" in plan                                                   # the GIN index is used


def test_progress_and_recent_run_stats(migrated):
    _, SL = migrated
    st = Store(SL)
    st.start_run("r1", {}, "test")
    st.set_progress("r1", {"step": "reading postings", "leads": 4}, {"linkedin": {"loads": 7}})
    assert st.get_run("r1")["stats"]["progress"]["step"] == "reading postings"
    rows = st.recent_run_stats(utcnow() - timedelta(hours=1))
    assert rows[0][1]["sources"]["linkedin"]["loads"] == 7
    st.finish_run("r1", "done", {"new": 1})
    st.set_progress("r1", {"step": "late"}, {})                                             # ignored once finished
    assert st.get_run("r1")["stats"] == {"new": 1}


def test_migrations_round_trip_keeps_data(migrated):
    """Every downgrade works, and stepping back one revision and forward again keeps the saved jobs."""
    from alembic import command
    from sqlalchemy import inspect
    eng, SL = migrated
    st = Store(SL)
    st.start_run("r1", {}, "test")
    st.persist_run("r1", [posting(1, title="Kept Across Migrations")], [], [])
    cfg = db.alembic_config()

    def run(fn, rev):
        with eng.begin() as c:
            cfg.attributes["connection"] = c
            fn(cfg, rev)

    run(command.downgrade, "0002")                       # drop closed_at + the full-text index
    assert "closed_at" not in {c["name"] for c in inspect(eng).get_columns("jobs")}
    run(command.upgrade, "head")
    with SL() as s:
        assert s.scalar(select(Job.title)) == "Kept Across Migrations"
    run(command.downgrade, "base")                       # all the way down
    assert not ({"jobs", "logs", "search_runs", "job_sightings", "seen_urls"} & set(inspect(eng).get_table_names()))
    db.migrate(eng)                                      # and back up from nothing
    with eng.connect() as c:
        mc = MigrationContext.configure(c, opts={"compare_type": True, "include_object": db.alembic_include_object})
        assert compare_metadata(mc, db.Base.metadata) == []
