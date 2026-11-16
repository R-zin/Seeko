"""Orchestrates discovery -> score -> tailor -> apply. DB-backed state; resumes after restart."""
from __future__ import annotations
import asyncio, json
from datetime import datetime
from pathlib import Path
from config.settings import get_settings, ARTIFACTS_DIR
from config.profile import load_profile, load_resume
from db import Job, SessionLocal, log
from scraper.dedupe import save_if_new
from scraper.ats import fetch_watchlist
from scraper.boards import BOARDS
from scraper.scorer import score_job
from tailor.tailor import tailor_resume
from applier.engine import fill_application, submit
from applier.gate import decide, pending
from scraper.browser import open_context
from . import notion_service as notion

state = {"paused": False, "running": False, "task": None}


def _set(job_id: str, **kw) -> None:
    with SessionLocal() as s:
        j = s.get(Job, job_id)
        for k, v in kw.items(): setattr(j, k, v)
        if "status" in kw: j.notion_dirty = True
        s.commit()


async def discover() -> int:
    st, prof = get_settings(), load_profile()
    found = await fetch_watchlist(prof.search.watchlist_urls) if "watchlist" in st.enabled_sources or prof.search.watchlist_urls else []
    for name, cls in BOARDS.items():
        if name in st.enabled_sources:
            try: found += await cls().search(prof.search.role_titles, prof.search.locations, 15)
            except Exception as e: log(f"{name} scrape failed: {e}", "error")
    excl = {c.lower() for c in prof.search.excluded_companies}
    new = 0
    for p in found:
        if p.company.lower() in excl: continue
        if (job := save_if_new(p)): new += 1; asyncio.create_task(notion.sync_job(job.id))
    log(f"discovery: {len(found)} found, {new} new"); return new


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
            await notion.sync_job(job_id); jd["status"] = "tailored"
        if jd["status"] == "tailored":
            await apply_job(job_id, jd)
    except Exception as e:
        _set(job_id, status="failed", error=str(e)[:1000]); log(f"{jd['title']}: {e}", "error", job_id)
    await notion.sync_job(job_id)


async def apply_job(job_id: str, jd: dict) -> None:
    _set(job_id, status="applying")
    pw, ctx = await open_context("apply", headless=get_settings().approval_mode is False)
    page = await ctx.new_page(); await page.goto(jd["url"], wait_until="domcontentloaded")
    res = await fill_application(page, load_profile(), load_resume(), ARTIFACTS_DIR / job_id / "tailored_resume.pdf", jd,
                                 screenshot_to=ARTIFACTS_DIR / job_id / "filled_form.png")
    action = decide(res)
    if action == "submit":
        await submit(page); _set(job_id, status="applied", applied_at=datetime.utcnow()); await ctx.close(); await pw.stop()
    else:
        pending[job_id] = (pw, ctx, page)
        note = ("" if res.ready else f"needs human: blockers={res.blockers} fields={res.needs_human}")
        _set(job_id, status="needs_review", error=note); log(f"awaiting review {jd['title']} {note}", "warn" if note else "info", job_id)


async def approve(job_id: str) -> bool:
    entry = pending.pop(job_id, None)
    if not entry: return False
    pw, ctx, page = entry
    try:
        await submit(page); _set(job_id, status="applied", applied_at=datetime.utcnow(), error="")
    except Exception as e: _set(job_id, status="failed", error=str(e)[:500])
    finally: await ctx.close(); await pw.stop()
    await notion.sync_job(job_id); return True


async def skip(job_id: str) -> None:
    if entry := pending.pop(job_id, None):
        await entry[1].close(); await entry[0].stop()
    _set(job_id, status="skipped"); await notion.sync_job(job_id)


def recover_orphans() -> int:
    """After a restart no browser pages exist: jobs stuck in applying/needs_review go back to 'tailored' to be re-filled."""
    n = 0
    with SessionLocal() as s:
        for j in s.query(Job).filter(Job.status.in_(["applying", "needs_review"])):
            if j.id not in pending:
                j.status, j.notion_dirty, n = "tailored", True, n + 1
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
        j.error, j.notion_dirty = "", True; s.commit(); return j.status


async def shutdown() -> None:
    state["running"] = False
    for jid in list(pending):
        _, ctx, _ = pending.pop(jid)
        try: await ctx.close()
        except Exception: pass


async def loop() -> None:
    """Background worker: process one job at a time while not paused. Never dies on a single bad job."""
    state["running"] = True
    while state["running"]:
        try:
            if not state["paused"]:
                with SessionLocal() as s:
                    j = s.query(Job).filter(Job.status.in_(["scraped", "scored", "tailoring", "tailored"])).order_by(Job.discovered_at).first()
                    jid = j.id if j else None
                if jid: await process_job(jid); continue
                await notion.retry_dirty()
        except asyncio.CancelledError: raise
        except Exception as e:
            log(f"worker loop error: {e}", "error")
        await asyncio.sleep(5)
