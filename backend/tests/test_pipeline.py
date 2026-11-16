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


class _Closable:
    def __init__(self): self.closed = False
    async def close(self): self.closed = True
    async def stop(self): self.closed = True
    async def new_page(self):
        class P:
            async def goto(self, *a, **k): pass
        return P()


async def test_apply_job_closes_browser_when_filling_fails(session, monkeypatch):
    mk(session, "x", "tailored")
    ctx, pw = _Closable(), _Closable()
    async def fake_open(name, headless=True): return pw, ctx
    async def boom(*a, **k): raise RuntimeError("llm down")
    monkeypatch.setattr(P, "open_context", fake_open); monkeypatch.setattr(P, "fill_application", boom)
    monkeypatch.setattr(P, "load_profile", lambda: None); monkeypatch.setattr(P, "load_resume", lambda: "")
    with pytest.raises(RuntimeError): await P.apply_job("x", {"url": "u", "title": "t"})
    assert ctx.closed and pw.closed and "x" not in P.pending


def test_worker_idle_without_llm_config(monkeypatch):
    S = lambda **k: type("S", (), {"llm_base_url": "", "llm_api_key": "", **k})()
    monkeypatch.setattr(P, "get_settings", lambda: S())
    assert not P.llm_configured()
    monkeypatch.setattr(P, "get_settings", lambda: S(llm_base_url="http://localhost:11434/v1"))
    assert P.llm_configured()
