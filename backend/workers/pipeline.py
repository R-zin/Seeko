"""Orchestrates discovery -> score -> tailor -> apply. DB-backed state; resumes after restart."""
from __future__ import annotations
import asyncio, json, time
from datetime import datetime
from pathlib import Path
from config.settings import get_settings, ARTIFACTS_DIR
from config.profile import load_profile, load_resume
from db import utcnow, Job, SessionLocal, log
from scraper.scorer import score_job
from tailor.tailor import tailor_resume
from applier.engine import fill_application, submit
from applier.gate import decide, pending
from scraper.browser import open_context

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
    with SessionLocal() as s: j = s.get(Job, job_id); jd = dict(title=j.title, company=j.company, jd_text=j.jd_text, url=j.url, status=j.status)
    try:
        if jd["status"] == "scraped":
            sc = await score_job(jd["jd_text"], jd["title"], jd["company"], resume)
            thr = prof.search.min_match_score or st.min_match_score
            _set(job_id, score=sc.score, missing_keywords=json.dumps(sc.missing_keywords), status="scored" if sc.score >= thr else "skipped")
            if sc.score < thr: log(f"skipped {jd['title']} ({sc.score}<{thr})", job_id=job_id); return
            jd["status"] = "scored"
        if jd["status"] in ("scored", "tailoring"):
            _set(job_id, status="tailoring")
            t = await tailor_resume(job_id, jd["jd_text"], jd["title"], jd["company"])
            _set(job_id, status="tailored", tailored_summary=t.summary, tailored_ok=not t.fell_back)
            jd["status"] = "tailored"
        if jd["status"] == "tailored":
            await apply_job(job_id, jd)
    except Exception as e:
        _set(job_id, status="failed", error=str(e)[:1000]); log(f"{jd['title']}: {e}", "error", job_id)


async def apply_job(job_id: str, jd: dict) -> None:
    _set(job_id, status="applying")
    pw, ctx = await open_context("apply", headless=get_settings().approval_mode is False)
    keep_open = False
    try:
        page = await ctx.new_page(); await page.goto(jd["url"], wait_until="domcontentloaded")
        res = await fill_application(page, load_profile(), load_resume(), ARTIFACTS_DIR / job_id / "tailored_resume.pdf", jd,
                                     screenshot_to=ARTIFACTS_DIR / job_id / "filled_form.png")
        action = decide(res)
        if action == "submit":
            await submit(page); _set(job_id, status="applied", applied_at=utcnow())
            return
        keep_open = True      # page stays open in `pending` until the user approves or skips
    finally:
        if not keep_open:     # success, or any exception: never leak a browser
            try: await ctx.close()
            finally: await pw.stop()
    pending[job_id] = (pw, ctx, page)
    note = "" if res.ready else f"needs human: blockers={res.blockers} fields={res.needs_human}"
    _set(job_id, status="needs_review", error=note); log(f"awaiting review {jd['title']} {note}", "warn" if note else "info", job_id)


async def approve(job_id: str) -> bool:
    entry = pending.pop(job_id, None)
    if not entry: return False
    pw, ctx, page = entry
    try:
        await submit(page); _set(job_id, status="applied", applied_at=utcnow(), error="")
    except Exception as e: _set(job_id, status="failed", error=str(e)[:500])
    finally: await ctx.close(); await pw.stop()
    return True


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
        j.error = ""; s.commit(); return j.status


async def shutdown() -> None:
    state["running"] = False
    for jid in list(pending):
        _, ctx, _ = pending.pop(jid)
        try: await ctx.close()
        except Exception: pass


# What the worker picks up: not closed (gone from its board / expired), and not already handled by the user - a job
# marked applied / interviewing / rejected... in the Tracker or in Notion is never tailored or applied to again.
WORKABLE = (Job.status.in_(["scraped", "scored", "tailoring", "tailored"]), Job.closed_at.is_(None),
            Job.application_status == "not_applied")


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
                        j = s.query(Job).filter(*WORKABLE).order_by(Job.discovered_at).first()
                        jid = j.id if j else None
                if jid: await process_job(jid); continue
        except asyncio.CancelledError: raise
        except Exception as e:
            log(f"worker loop error: {e}", "error")
        await asyncio.sleep(5)
