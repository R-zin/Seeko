"""Orchestrates discovery -> score -> tailor -> apply. DB-backed state; resumes after restart."""
from __future__ import annotations
import asyncio, json, time
from datetime import datetime, timedelta
from pathlib import Path
import httpx
import openai
from playwright.async_api import TimeoutError as PWTimeout
from sqlalchemy import case, or_, select
from config.settings import get_settings, ARTIFACTS_DIR
from config.profile import load_profile, load_resume
from db import utcnow, Job, SessionLocal, log
from scraper.scorer import score_job
from tailor.tailor import tailor_resume
from applier import answers
from applier.engine import fill_application, submit
from applier.extract import extract_fields
from applier.gate import decide, pending
from scraper.browser import open_context
import notify

state = {"paused": False, "running": False, "task": None, "last_discovery": 0.0}


def _set(job_id: str, **kw) -> None:
    """Update a job. Notion picks the change up by itself (db.py marks it dirty and wakes the syncer on commit)."""
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        for k, v in kw.items(): setattr(j, k, v)
        if kw.get("status") == "applied" and j.application_status == "not_applied":
            j.application_status = "applied"
        s.commit()


async def discover(trigger: str = "manual") -> int:
    """Run the LangGraph job search (jobsearch/graph.py); new jobs land as status 'scraped' for this worker."""
    from jobsearch.service import RunCancelled, RunInProgress, run_search
    try:
        res = await run_search(trigger=trigger)
    except RunInProgress:
        log("discovery skipped: a job search is already running", "warn"); return 0
    except RunCancelled:
        return 0
    return len(res["new_job_ids"])


async def process_job(job_id: str) -> None:
    st = get_settings(); prof = load_profile(); resume = load_resume()
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        jd = dict(title=j.title, company=j.company, jd_text=j.jd_text, url=j.url, status=j.status, origin=j.origin)
    try:
        if jd["status"] == "scraped":
            sc = await score_job(jd["jd_text"], jd["title"], jd["company"], resume)
            thr = prof.search.min_match_score or st.min_match_score
            keep = sc.score >= thr or jd["origin"] == "user"     # a job you added yourself is never skipped for its score
            _set(job_id, score=sc.score, missing_keywords=json.dumps(sc.missing_keywords), status="scored" if keep else "skipped")
            if not keep: log(f"skipped {jd['title']} ({sc.score}<{thr})", job_id=job_id); return
            jd["status"] = "scored"
        if jd["status"] in ("scored", "tailoring"):
            _set(job_id, status="tailoring")
            t = await tailor_resume(job_id, jd["jd_text"], jd["title"], jd["company"])
            _set(job_id, status="tailored", tailored_summary=t.summary, tailored_ok=not t.fell_back)
            jd["status"] = "tailored"
        if jd["status"] == "tailored":
            await apply_job(job_id, jd)
    except Exception as e:
        _failed(job_id, jd["title"], e)


def _bank() -> tuple[dict, list]:
    """Your saved answers (and the same as facts for the model). A problem reading them never blocks applying."""
    try:
        bank = answers.load_bank(SessionLocal)
        return bank, answers.facts(bank, SessionLocal)
    except Exception as e:
        log(f"answer bank unavailable: {e}", "warn"); return {}, []


async def apply_job(job_id: str, jd: dict) -> None:
    _set(job_id, status="applying")
    pw, ctx = await open_context("apply", headless=get_settings().approval_mode is False)
    keep_open = False
    try:
        page = await ctx.new_page(); await page.goto(jd["url"], wait_until="domcontentloaded")
        bank, facts = _bank()
        res = await fill_application(page, load_profile(), load_resume(), ARTIFACTS_DIR / job_id / "tailored_resume.pdf", jd,
                                     screenshot_to=ARTIFACTS_DIR / job_id / "filled_form.png", bank=bank, bank_facts=facts)
        action = decide(res)
        if action == "submit":
            await submit(page); _set(job_id, status="applied", applied_at=utcnow(), waiting_on=None)
            notify.emit("applied", f"Applied: {jd['title']} at {jd['company']}", "Submitted on its own (full-auto).",
                        notify.link("pipeline"), {"job_id": job_id})
            return
        keep_open = True      # page stays open in `pending` until the user approves or skips
    finally:
        if not keep_open:     # success, or any exception: never leak a browser
            try: await ctx.close()
            finally: await pw.stop()
    pending[job_id] = (pw, ctx, page)
    keys = answers.record_unanswered(res.questions, SessionLocal) if res.questions else []
    note = "" if res.ready else f"needs human: blockers={res.blockers} fields={res.needs_human}"
    _set(job_id, status="needs_review", error=note, waiting_on=keys or None)
    used = f" ({res.from_bank} from your saved answers)" if res.from_bank else ""
    log(f"awaiting review {jd['title']}{used} {note}", "warn" if note else "info", job_id)
    why = (f"Needs your answer to: {'; '.join(res.needs_human[:4])}" if res.needs_human else
           f"Stopped at: {', '.join(res.blockers)}" if res.blockers else "The form is filled — approve to submit.")
    notify.emit("needs_review", f"Review: {jd['title']} at {jd['company']}", why, notify.link("pipeline"), {"job_id": job_id})


async def learn_from_page(job_id: str, page) -> int:
    """Save what you typed into the paused form for the questions it was waiting on. Never blocks a submit."""
    try:
        with SessionLocal() as s:
            keys = set((s.get(Job, job_id).waiting_on or []))
        if not keys: return 0
        n = answers.learn(await extract_fields(page), keys, SessionLocal)
        if n: log(f"learned {n} answer(s) from the form you completed; future forms reuse them", "info", job_id)
        return n
    except Exception as e:
        log(f"could not read your answers from the form: {e}", "warn", job_id); return 0


async def approve(job_id: str) -> bool:
    entry = pending.pop(job_id, None)
    if not entry: return False
    pw, ctx, page = entry
    try:
        await learn_from_page(job_id, page)
        await submit(page); _set(job_id, status="applied", applied_at=utcnow(), error="", waiting_on=None)
    except Exception as e: _set(job_id, status="failed", error=str(e)[:500])
    finally: await ctx.close(); await pw.stop()
    return True


def ready_ids() -> list[str]:
    """Jobs whose form is filled completely and open in the browser, waiting only for your approval."""
    if not pending: return []
    with SessionLocal() as s:   # waiting_on is JSON: None is stored as JSON null, so test it here rather than in SQL
        rows = s.execute(select(Job.id, Job.waiting_on).where(Job.id.in_(list(pending)), Job.status == "needs_review",
                                                              Job.error == "")).all()
    return [jid for jid, waiting in rows if not waiting]


async def approve_ready() -> list[str]:
    """Submit every completely filled application waiting for approval (one at a time)."""
    return [jid for jid in ready_ids() if await approve(jid)]


async def refill(job_id: str) -> bool:
    """Fill a paused job's form again from scratch (e.g. after you answered its questions): the worker re-applies."""
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        if j is None or j.status != "needs_review": return False
    if entry := pending.pop(job_id, None):
        try: await entry[1].close(); await entry[0].stop()
        except Exception: pass
    _set(job_id, status="tailored", error="", waiting_on=None)
    return True


async def refill_ready() -> list[str]:
    """Re-queue every paused job whose open questions now all have answers."""
    done = [jid for jid in answers.ready_to_refill(SessionLocal) if await refill(jid)]
    if done: log(f"{len(done)} job(s) re-queued to be filled with your new answers", "info")
    return done


async def skip(job_id: str) -> None:
    if entry := pending.pop(job_id, None):
        await entry[1].close(); await entry[0].stop()
    _set(job_id, status="skipped")


def recover_orphans() -> int:
    """After a restart no browser pages exist: jobs stuck in applying/needs_review go back to 'tailored' to be re-filled."""
    n = 0
    with SessionLocal() as s:
        for j in s.query(Job).filter(Job.status.in_(["applying", "needs_review"])):
            if j.id not in pending:
                j.status, n = "tailored", n + 1
                j.error = "re-queued after restart (previous browser session was lost)"
        s.commit()
    if n: log(f"recovered {n} job(s) orphaned by restart", "warn")
    return n


def retry(job_id: str) -> str | None:
    """Re-queue a failed/skipped job from the furthest stage it already completed."""
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        if j is None or j.status not in ("failed", "skipped"): return None
        if (ARTIFACTS_DIR / job_id / "tailored_resume.pdf").exists(): j.status = "tailored"
        elif j.score is not None: j.status = "scored"
        else: j.status = "scraped"
        j.error, j.attempts, j.retry_at = "", 0, None; s.commit(); return j.status


async def shutdown() -> None:
    state["running"] = False
    for jid in list(pending):
        _, ctx, _ = pending.pop(jid)
        try: await ctx.close()
        except Exception: pass


# ── failures ────────────────────────────────────────────────────────────────────────────────────────────────────
_TRANSIENT = (asyncio.TimeoutError, TimeoutError, ConnectionError, httpx.TransportError, openai.APIConnectionError,
              openai.RateLimitError, openai.InternalServerError, PWTimeout)
_TRANSIENT_TEXT = ("timed out", "timeout", "rate limit", "too many requests", "temporarily unavailable",
                   "connection reset", "connection refused", "bad gateway", "service unavailable")
RESUME_STAGE = {"tailoring": "scored", "applying": "tailored"}    # where a job goes back to when a step failed


def is_transient(e: BaseException) -> bool:
    """Worth trying again later as-is: timeouts, rate limits, 5xx, a dropped connection. Not: bad data, bad config."""
    if isinstance(e, _TRANSIENT): return True
    if isinstance(e, openai.APIStatusError): return e.status_code in (408, 409, 429) or e.status_code >= 500
    return any(t in str(e).lower() for t in _TRANSIENT_TEXT)


def retry_delay(attempt: int) -> timedelta:
    return timedelta(minutes=min(5 * 2 ** (attempt - 1), 120))


def _failed(job_id: str, title: str, e: Exception) -> None:
    """A step failed: transient problems retry from the stage the job reached (with backoff, up to AUTO_RETRY times);
    anything else marks it failed for you to look at."""
    limit = get_settings().auto_retry
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        n = (j.attempts or 0) + 1
        if is_transient(e) and n <= limit:
            j.status = RESUME_STAGE.get(j.status, j.status)
            j.attempts, j.retry_at = n, utcnow() + retry_delay(n)
            j.error = f"retry {n}/{limit} at {j.retry_at:%H:%M} UTC after: {str(e)[:500]}"
            s.commit()
            log(f"{title}: {e} — will retry ({n}/{limit})", "warn", job_id)
            return
        j.status, j.error, j.retry_at = "failed", str(e)[:1000], None
        s.commit()
    log(f"{title}: {e}", "error", job_id)
    notify.emit("failed", f"Failed: {title}", str(e)[:500], notify.link("pipeline"), {"job_id": job_id})


# What the worker picks up: not closed (gone from its board / expired), and not already handled by the user - a job
# marked applied / interviewing / rejected... in the Tracker or in Notion is never tailored or applied to again.
WORKABLE = (Job.status.in_(["scraped", "scored", "tailoring", "tailored"]), Job.closed_at.is_(None),
            Job.application_status == "not_applied")


def next_job_id(s, now: datetime | None = None) -> str | None:
    """The job to work on next: an apply-by date within a week first, then jobs you added yourself, then the ones
    furthest along (finish what was started), best score / relevance first, oldest first. Jobs waiting out a retry
    delay are skipped."""
    now = now or utcnow()
    deadline = case((Job.valid_through <= now + timedelta(days=7), 0), else_=1)
    mine = case((Job.origin == "user", 0), else_=1)
    stage = case((Job.status == "tailored", 0), (Job.status == "tailoring", 1), (Job.status == "scored", 2), else_=3)
    return s.scalar(select(Job.id).where(*WORKABLE, or_(Job.retry_at.is_(None), Job.retry_at <= now))
                    .order_by(deadline, Job.valid_through.asc().nulls_last(), mine, stage, Job.score.desc().nulls_last(),
                              Job.relevance.desc().nulls_last(), Job.discovered_at).limit(1))


def llm_configured() -> bool:
    """Without a base URL or key, every call would hit api.openai.com with a dummy key and fail - so don't try."""
    st = get_settings()
    return bool(st.llm_base_url.strip() or st.llm_api_key.strip())


def discovery_due(now: float | None = None) -> bool:
    n = get_settings().discovery_interval_min
    return n > 0 and (now or time.time()) - state["last_discovery"] >= n * 60


async def loop() -> None:
    """Background worker: process one job at a time while not paused. Never dies on a single bad job."""
    state["running"] = True
    while state["running"]:
        try:
            if not state["paused"]:
                if discovery_due():
                    state["last_discovery"] = time.time(); await discover("schedule")
                jid = None
                if llm_configured():
                    with SessionLocal() as s:
                        jid = next_job_id(s)
                if jid: await process_job(jid); continue
        except asyncio.CancelledError: raise
        except Exception as e:
            log(f"worker loop error: {e}", "error")
        await asyncio.sleep(5)
