"""Follow-ups: what your job search needs from you now, a daily digest of it, and (optionally) closing the loop on
applications that never got an answer.

* next steps — interviews, assessments, calls: jobs with a Next Step Date overdue (up to two weeks), today, or this week;
* follow up — applications still at Applied / Assessment, sent FOLLOWUP_AFTER_DAYS ago or more, with no next step listed;
* apply by — open jobs you have not applied to whose posting closes within 5 days;
* waiting — forms paused for your approval, and form questions waiting for your answer.

The digest goes out once a day at DIGEST_HOUR (local time) through notify.py. With AUTO_GHOST_DAYS > 0, applications
still at "Applied" that long after you applied are marked "No response" (logged, and synced to Notion like any edit).
Dates you type (next steps) are local calendar days; timestamps the app records are UTC.
"""
from __future__ import annotations
import asyncio
from datetime import date, datetime, timedelta
from sqlalchemy import select
import db
from config.settings import get_settings
from db import Job, get_state, log, set_state, update_application, utcnow

OPEN_APPLICATION = ("applied", "assessment", "interviewing", "offer")
FOLLOW_UP = ("applied", "assessment")
APPLY_BY_DAYS = 5
OVERDUE_DAYS = 14        # a next step overdue by longer than this is no longer listed as one (it shows as a follow-up)


def _sf(session_factory):
    return session_factory or db.SessionLocal


def _item(j: Job, **extra) -> dict:
    return {"id": j.id, "title": j.title, "company": j.company, "status": j.status, "application_status": j.application_status,
            "next_step": j.next_step, "next_step_date": j.next_step_date.isoformat() if j.next_step_date else None,
            "applied_at": j.applied_at.isoformat() if j.applied_at else None,
            "valid_through": j.valid_through.isoformat() if j.valid_through else None, "url": j.url} | extra


def build(session_factory=None, now: datetime | None = None, today: date | None = None) -> dict:
    st = get_settings()
    now, today = now or utcnow(), today or datetime.now().date()
    with _sf(session_factory)() as s:
        steps = s.scalars(select(Job).where(Job.next_step_date.is_not(None), Job.next_step_date <= today + timedelta(days=7),
                                            Job.next_step_date >= today - timedelta(days=OVERDUE_DAYS),
                                            Job.application_status.in_(OPEN_APPLICATION)).order_by(Job.next_step_date)).all()
        stale = s.scalars(select(Job).where(Job.application_status.in_(FOLLOW_UP), Job.applied_at.is_not(None),
                                            Job.applied_at <= now - timedelta(days=st.followup_after_days))
                          .order_by(Job.applied_at)).all()
        closing = s.scalars(select(Job).where(Job.closed_at.is_(None), Job.application_status == "not_applied",
                                              Job.status.not_in(["skipped", "failed", "tracked"]), Job.valid_through.is_not(None),
                                              Job.valid_through >= now, Job.valid_through <= now + timedelta(days=APPLY_BY_DAYS))
                            .order_by(Job.valid_through)).all()
        review = s.scalar(select(db.func.count()).select_from(Job).where(Job.status == "needs_review")) or 0
    from applier.answers import pending_count
    return {
        "today": today.isoformat(),
        "next_steps": [_item(j, when="overdue" if j.next_step_date < today else "today" if j.next_step_date == today else "soon")
                       for j in steps],
        "follow_up": [_item(j, days=(now - j.applied_at).days) for j in stale     # not if a step is listed or still ahead
                      if j.id not in {x.id for x in steps} and not (j.next_step_date and j.next_step_date >= today)],
        "apply_by": [_item(j, days=max(0, (j.valid_through.date() - today).days)) for j in closing],   # calendar days
        "review": review, "questions": pending_count(session_factory),
        "followup_after_days": st.followup_after_days,
    }


def digest_text(a: dict) -> str:
    """The digest as plain text ("" when there is nothing to say)."""
    out = []
    if a["next_steps"]:
        out.append("Next steps:")
        out += [f"• {x['next_step_date']}{' (overdue)' if x['when'] == 'overdue' else ''} {x['next_step'] or 'next step'} — "
                f"{x['title']} at {x['company']}" for x in a["next_steps"][:8]]
    if a["follow_up"]:
        out.append(f"Follow up ({len(a['follow_up'])}, no news for {a['followup_after_days']}+ days):")
        out += [f"• {x['title']} at {x['company']} — applied {x['days']} days ago" for x in a["follow_up"][:8]]
    if a["apply_by"]:
        out.append("Closing soon, not applied yet:")
        out += [f"• {x['title']} at {x['company']} — closes {x['valid_through'][:10]}" for x in a["apply_by"][:8]]
    if a["review"]: out.append(f"{a['review']} filled form(s) waiting for your approval.")
    if a["questions"]: out.append(f"{a['questions']} form question(s) waiting for your answer.")
    return "\n".join(out)


def auto_ghost(days: int, session_factory=None, now: datetime | None = None) -> int:
    """Mark applications with no news `days` after applying as "No response". Returns how many."""
    now = now or utcnow()
    with _sf(session_factory)() as s:
        jobs = s.scalars(select(Job).where(Job.application_status == "applied", Job.applied_at.is_not(None),
                                           Job.applied_at <= now - timedelta(days=days))).all()
        today = datetime.now().date()
        n = 0
        for j in jobs:
            if j.next_step_date and j.next_step_date >= today: continue     # something is still planned
            update_application(j, application_status="ghosted"); n += 1
        s.commit()
    return n


async def tick(session_factory=None, now_local: datetime | None = None) -> dict:
    """Once-a-day work, checked every few minutes: auto "No response", then the digest after DIGEST_HOUR."""
    import notify
    st = get_settings()
    now_local = now_local or datetime.now()
    today = now_local.date().isoformat()
    done = {}
    if st.auto_ghost_days > 0 and get_state("auto_ghost_on", session_factory=session_factory) != today:
        n = auto_ghost(st.auto_ghost_days, session_factory)
        set_state("auto_ghost_on", today, session_factory=session_factory)
        if n: log(f"marked {n} application(s) with no answer after {st.auto_ghost_days} days as “No response”")
        done["ghosted"] = n
    wants = st.notify_webhook_url.strip() and "digest" in st.notify_events and st.digest_hour >= 0
    if wants and now_local.hour >= st.digest_hour and get_state("digest_on", session_factory=session_factory) != today:
        set_state("digest_on", today, session_factory=session_factory)   # once a day, even if delivery fails
        text = digest_text(build(session_factory, today=now_local.date()))
        if text:
            done["digest"] = await notify.send("digest", f"Job search — {now_local:%A %d %B}", text, notify.link("tracker"))
    return done


async def run(poll_s: float = 300) -> None:
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            try: log(f"follow-up check failed: {e}", "warn")
            except Exception: pass
        await asyncio.sleep(poll_s)
