"""Safety gate: approval mode vs. capped full-auto, and the registry of pages awaiting approval."""
from __future__ import annotations
from datetime import datetime, time
from sqlalchemy import select, func
from config.settings import get_settings
from db import utcnow, Job, SessionLocal
from .engine import FillResult

pending: dict[str, object] = {}   # job_id -> (pw, ctx, page) kept open while awaiting approval


def applied_today() -> int:
    start = datetime.combine(utcnow().date(), time.min)
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(Job).where(Job.status == "applied", Job.applied_at >= start)) or 0


def decide(res: FillResult) -> str:
    """-> 'submit' | 'review' | 'paused'. Approval mode always reviews; auto only submits when clean and under cap."""
    st = get_settings()
    if res.blockers or res.needs_human: return "paused" if not st.approval_mode else "review"
    if st.approval_mode: return "review"
    if applied_today() >= st.daily_apply_cap: return "review"
    return "submit"
