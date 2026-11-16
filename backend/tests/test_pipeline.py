import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import db
from db import Job
from workers import pipeline as P


@pytest.fixture
def session(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path/'t.db'}"); db.Base.metadata.create_all(eng)
    SL = sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(P, "SessionLocal", SL); monkeypatch.setattr(P, "ARTIFACTS_DIR", tmp_path / "art")
    monkeypatch.setattr(P, "log", lambda *a, **k: None)
    return SL


def mk(SL, id, status, **kw):
    with SL() as s:
        s.add(Job(id=id, source="x", company="c", title=id, url=f"u{id}", canonical_url=f"u{id}", dedupe_key=id, status=status, **kw)); s.commit()


def status(SL, id):
    with SL() as s: return s.get(Job, id).status


def test_recover_orphans_requeues_jobs_without_live_session(session):
    mk(session, "a", "needs_review"); mk(session, "b", "applying"); mk(session, "c", "applied"); mk(session, "d", "needs_review")
    P.pending["d"] = ("pw", "ctx", "page")          # live session survives
    try: assert P.recover_orphans() == 2
    finally: P.pending.clear()
    assert [status(session, i) for i in "abcd"] == ["tailored", "tailored", "applied", "needs_review"]


def test_retry_resumes_from_furthest_stage(session, tmp_path):
    mk(session, "f1", "failed"); mk(session, "f2", "failed", score=80); mk(session, "f3", "skipped", score=80)
    (tmp_path / "art" / "f3").mkdir(parents=True); (tmp_path / "art" / "f3" / "tailored_resume.pdf").write_bytes(b"x")
    assert P.retry("f1") == "scraped" and P.retry("f2") == "scored" and P.retry("f3") == "tailored"
    mk(session, "ok", "applied"); assert P.retry("ok") is None and P.retry("missing") is None


async def test_loop_survives_a_crashing_job(session, monkeypatch):
    import asyncio
    mk(session, "boom", "scraped"); calls = []
    async def bad(jid): calls.append(jid); P.state["running"] = False; raise RuntimeError("kaboom")
    monkeypatch.setattr(P, "process_job", bad)
    async def nosleep(_): pass
    monkeypatch.setattr(P.asyncio, "sleep", nosleep)
    P.state["paused"] = False
    await asyncio.wait_for(P.loop(), 5)            # must return normally instead of raising
    assert calls == ["boom"]
