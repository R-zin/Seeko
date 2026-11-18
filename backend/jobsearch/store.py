"""Persistence for the job search (PostgreSQL in production, SQLite in tests).

Every write is idempotent, so a graph node can be retried or a crashed run resumed without double counting:
jobs insert with ON CONFLICT DO NOTHING on (canonical URL | company+title), sightings are unique per (run, job), and
`times_seen` only moves when a new sighting row is actually inserted.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timedelta
from typing import Iterable
from sqlalchemy import select, update
from sqlalchemy.orm import Session
import db
from db import Job, JobSighting, SearchRun, SeenUrl, utcnow
from scraper.dedupe import canonical_url, dedupe_key
from .extract import parse_date

_CHUNK = 500


def _insert(session: Session, model):
    name = session.get_bind().dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover
        raise RuntimeError(f"unsupported database dialect: {name}")
    return insert(model)


def _chunks(xs: list, n: int = _CHUNK) -> Iterable[list]:
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def job_id_for(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]


class Store:
    def __init__(self, session_factory=None):
        self._sf = session_factory or db.SessionLocal

    # ── runs ────────────────────────────────────────────────────────────────────────────────────────────────
    def start_run(self, run_id: str, spec: dict, trigger: str = "manual") -> None:
        with self._sf() as s:
            stmt = _insert(s, SearchRun).values(id=run_id, started_at=utcnow(), status="running", trigger=trigger,
                                                spec=spec, stats={}, error="")
            s.execute(stmt.on_conflict_do_update(index_elements=["id"], set_={"status": "running", "error": ""}))
            s.commit()

    def finish_run(self, run_id: str, status: str, stats: dict | None = None, error: str = "") -> None:
        with self._sf() as s:
            run = s.get(SearchRun, run_id)
            if run is None:
                run = SearchRun(id=run_id, spec={}, stats={}); s.add(run)
            run.status, run.finished_at, run.error = status, utcnow(), error[:2000]
            if stats is not None:
                run.stats = stats
            s.commit()

    def set_status(self, run_id: str, status: str) -> None:
        with self._sf() as s:
            s.execute(update(SearchRun).where(SearchRun.id == run_id).values(status=status, error=""))
            s.commit()

    def mark_interrupted(self) -> int:
        """Runs left 'running' by a previous process cannot be running now; they can be resumed from their checkpoint."""
        with self._sf() as s:
            n = s.execute(update(SearchRun).where(SearchRun.status == "running")
                          .values(status="interrupted", error="process stopped before the run finished")).rowcount
            s.commit()
            return n or 0

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self._sf() as s:
            return [run_dict(r) for r in s.scalars(select(SearchRun).order_by(SearchRun.started_at.desc()).limit(limit))]

    def get_run(self, run_id: str) -> dict | None:
        with self._sf() as s:
            r = s.get(SearchRun, run_id)
            if r is None:
                return None
            rows = s.execute(select(JobSighting.is_new, Job.id, Job.title, Job.company, Job.job_type, Job.location,
                                    Job.source, Job.url).join(Job, Job.id == JobSighting.job_id)
                             .where(JobSighting.run_id == run_id).order_by(JobSighting.is_new.desc(), Job.title)).all()
            jobs = [{"is_new": n, "id": i, "title": t, "company": c, "job_type": jt, "location": loc, "source": src, "url": u}
                    for n, i, t, c, jt, loc, src, u in rows]
            return run_dict(r) | {"jobs": jobs}

    # ── what do we know already ─────────────────────────────────────────────────────────────────────────────
    def known(self, canonical_urls: list[str], dedupe_keys: list[str] = ()) -> dict[str, str]:
        """Map of canonical URL / dedupe key -> job id for jobs already stored."""
        out: dict[str, str] = {}
        with self._sf() as s:
            for chunk in _chunks(list(set(canonical_urls))):
                for cu, jid in s.execute(select(Job.canonical_url, Job.id).where(Job.canonical_url.in_(chunk))):
                    out[cu] = jid
            for chunk in _chunks([k for k in set(dedupe_keys) if k and k != "|"]):
                for dk, jid in s.execute(select(Job.dedupe_key, Job.id).where(Job.dedupe_key.in_(chunk))):
                    out[dk] = jid
        return out

    def recently_rejected(self, canonical_urls: list[str], days: int) -> set[str]:
        if days <= 0:
            return set()
        since = utcnow() - timedelta(days=days)
        out: set[str] = set()
        with self._sf() as s:
            for chunk in _chunks(list(set(canonical_urls))):
                out |= set(s.scalars(select(SeenUrl.canonical_url)
                                     .where(SeenUrl.canonical_url.in_(chunk), SeenUrl.seen_at >= since)))
        return out

    def is_known_url(self, url: str) -> bool:
        return bool(self.known([canonical_url(url)]))

    # ── writes for one run (one transaction) ───────────────────────────────────────────────────────────────
    def persist_run(self, run_id: str, accepted: list[dict], known: list[dict], rejected: list[dict]) -> dict:
        """Insert new postings, record sightings of known ones, remember rejected pages. Returns ids and counts."""
        now = utcnow()
        new_ids: list[str] = []
        sightings: list[tuple[str, str, str, bool]] = [(k["job_id"], k["source"], k["url"], False) for k in known]
        with self._sf() as s, s.begin():
            for p in accepted:
                cu, dk = canonical_url(p["url"]), dedupe_key(p.get("company", ""), p["title"])
                row = dict(id=job_id_for(p["url"]), source=p["source"], company=p.get("company", "")[:300],
                           title=p["title"][:300], location=(p.get("location") or "")[:300], url=p["url"],
                           canonical_url=cu, dedupe_key=dk, jd_text=p.get("jd_text", ""),
                           is_easy_apply=bool(p.get("is_easy_apply")), discovered_at=now, status="scraped",
                           missing_keywords="[]", tailored_summary="", tailored_ok=False, notion_page_id="",
                           notion_dirty=True, error="", job_type=p.get("job_type", "job"),
                           employment_type=(p.get("employment_type") or "")[:100], is_remote=bool(p.get("is_remote")),
                           posted_at=parse_date(p.get("posted_at")), valid_through=parse_date(p.get("valid_through")),
                           jd_source=p.get("jd_source", ""), relevance=p.get("relevance"), last_seen_at=now,
                           times_seen=1, first_run_id=run_id)
                inserted = s.execute(_insert(s, Job).values(**row).on_conflict_do_nothing().returning(Job.id)).scalar_one_or_none()
                if inserted:
                    new_ids.append(inserted); sightings.append((inserted, p["source"], p["url"], True))
                    continue
                # Same job already stored (another source, an earlier run, or this run before a retry).
                existing = s.execute(select(Job.id, Job.first_run_id)
                                     .where((Job.canonical_url == cu) | (Job.dedupe_key == dk)).limit(1)).first()
                if existing:
                    if existing.first_run_id == run_id:
                        new_ids.append(existing.id)
                    sightings.append((existing.id, p["source"], p["url"], existing.first_run_id == run_id))
            bumped = 0
            for job_id, source, url, is_new in dict(((j, (j, src, u, n)) for j, src, u, n in sightings)).values():
                sid = s.execute(_insert(s, JobSighting).values(run_id=run_id, job_id=job_id, source=source, url=url,
                                                               is_new=is_new, seen_at=now)
                                .on_conflict_do_nothing().returning(JobSighting.id)).scalar_one_or_none()
                if sid and not is_new:
                    s.execute(update(Job).where(Job.id == job_id).values(last_seen_at=now, times_seen=Job.times_seen + 1))
                    bumped += 1
            for r in rejected:
                stmt = _insert(s, SeenUrl).values(canonical_url=canonical_url(r["url"]), source=r["source"],
                                                  title=(r.get("title") or "")[:300], reason=r["reason"], run_id=run_id, seen_at=now)
                s.execute(stmt.on_conflict_do_update(index_elements=["canonical_url"],
                                                     set_={"reason": r["reason"], "run_id": run_id, "seen_at": now,
                                                           "title": (r.get("title") or "")[:300]}))
        return {"new_job_ids": list(dict.fromkeys(new_ids)), "known_bumped": bumped}


def run_dict(r: SearchRun) -> dict:
    return {"id": r.id, "status": r.status, "trigger": r.trigger, "started_at": _iso(r.started_at),
            "finished_at": _iso(r.finished_at), "spec": r.spec or {}, "stats": r.stats or {}, "error": r.error}


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None
