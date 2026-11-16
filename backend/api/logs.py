from fastapi import APIRouter
from db import LogEntry, SessionLocal

router = APIRouter(prefix="/api/logs")


@router.get("")
def logs(level: str | None = None, limit: int = 200):
    with SessionLocal() as s:
        q = s.query(LogEntry).order_by(LogEntry.id.desc())
        if level: q = q.filter(LogEntry.level == level)
        return [{"id": l.id, "ts": l.ts.isoformat(), "level": l.level, "job_id": l.job_id, "message": l.message} for l in q.limit(limit)]
