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
    monkeypatch.setattr(P, "llm_configured", lambda: True)   # else nothing is picked and the loop spins forever
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


def test_worker_leaves_jobs_the_user_already_handled(session):
    mk(session, "mine", "tailored", application_status="interviewing")   # applied by hand, tracked in Notion
    mk(session, "todo", "scored")
    with session() as s:
        assert [j.id for j in s.query(Job).filter(*P.WORKABLE)] == ["todo"]


def test_submitting_marks_the_application_applied(session):
    mk(session, "a", "applying"); mk(session, "b", "applying", application_status="offer")
    P._set("a", status="applied"); P._set("b", status="applied")
    with session() as s:
        assert s.get(Job, "a").application_status == "applied" and s.get(Job, "b").application_status == "offer"


def test_queue_order_deadline_mine_stage_score(session):
    from datetime import timedelta
    from db import utcnow
    now = utcnow()
    mk(session, "old_scraped", "scraped", relevance=0.9)
    mk(session, "scored_low", "scored", score=61.0)
    mk(session, "scored_high", "scored", score=90.0)
    mk(session, "tailored", "tailored", score=70.0)
    mk(session, "mine", "scraped", origin="user")
    mk(session, "due_soon", "scraped", valid_through=now + timedelta(days=2))
    mk(session, "waiting", "tailored", retry_at=now + timedelta(minutes=30))       # backing off after a timeout
    order = []
    with session() as s:
        while (jid := P.next_job_id(s, now)):
            order.append(jid)
            s.get(Job, jid).status = "applied"; s.commit()
    assert order == ["due_soon", "mine", "tailored", "scored_high", "scored_low", "old_scraped"]
    with session() as s:
        assert P.next_job_id(s, now + timedelta(hours=1)) == "waiting"


async def test_transient_failures_retry_with_backoff_then_fail(session, monkeypatch):
    import httpx, openai
    monkeypatch.setattr(P, "load_profile", lambda: None); monkeypatch.setattr(P, "load_resume", lambda: "")
    calls = []

    async def flaky(*a, **k):
        calls.append(1); raise httpx.ConnectTimeout("connect timed out")
    monkeypatch.setattr(P, "score_job", flaky)
    mk(session, "t", "scraped")
    for n in (1, 2, 3):
        await P.process_job("t")
        with session() as s:
            j = s.get(Job, "t")
            assert (j.status, j.attempts) == ("scraped", n) and j.retry_at is not None and f"retry {n}/3" in j.error
    await P.process_job("t")
    with session() as s:
        assert s.get(Job, "t").status == "failed"
    assert P.retry("t") == "scraped"
    with session() as s:
        assert (s.get(Job, "t").attempts, s.get(Job, "t").retry_at) == (0, None)

    async def broken(*a, **k): raise ValueError("model returned nonsense")
    monkeypatch.setattr(P, "score_job", broken)
    mk(session, "v", "scraped")
    await P.process_job("v")
    with session() as s:
        assert s.get(Job, "v").status == "failed"                   # not transient: no retry
    req = httpx.Request("POST", "http://llm.test")
    assert P.is_transient(openai.RateLimitError("slow down", response=httpx.Response(429, request=req), body=None))
    assert not P.is_transient(openai.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None))


async def test_failure_while_tailoring_or_applying_resumes_from_the_last_finished_step(session, monkeypatch):
    import httpx
    mk(session, "x", "tailoring"); mk(session, "y", "applying")
    P._failed("x", "x", httpx.ReadTimeout("read timed out")); P._failed("y", "y", TimeoutError())
    with session() as s:
        assert s.get(Job, "x").status == "scored" and s.get(Job, "y").status == "tailored"


async def test_approve_all_ready_submits_only_complete_forms(session, monkeypatch):
    submitted = []
    async def fake_submit(page): submitted.append(page)
    async def no_learn(job_id, page): return 0
    monkeypatch.setattr(P, "submit", fake_submit); monkeypatch.setattr(P, "learn_from_page", no_learn)
    mk(session, "ok", "needs_review"); mk(session, "q", "needs_review", waiting_on=["notice period"])
    mk(session, "blocked", "needs_review", error="needs human: blockers=['captcha']")
    try:
        for jid in ("ok", "q", "blocked"):
            P.pending[jid] = (_Closable(), _Closable(), f"page-{jid}")
        assert P.ready_ids() == ["ok"]
        assert await P.approve_ready() == ["ok"] and submitted == ["page-ok"]
        with session() as s:
            assert s.get(Job, "ok").status == "applied" and s.get(Job, "ok").application_status == "applied"
            assert s.get(Job, "q").status == "needs_review"
    finally:
        P.pending.clear()
