"""Persistence for the job search (PostgreSQL in production, SQLite in tests).

Every write is idempotent, so a graph node can be retried or a crashed run resumed without double counting:
jobs insert with ON CONFLICT DO NOTHING on (canonical URL | company+title), sightings are unique per (run, job), and
`times_seen` only moves when a new sighting row is actually inserted.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timedelta
from typing import Iterable
from sqlalchemy import Select, func, literal_column, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import db
from db import FTS_DOC, Job, JobSighting, SearchRun, SeenUrl, utcnow
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
            s.execute(stmt.on_conflict_do_update(index_elements=["id"], set_={"status": "running", "error": "",
                                                                                "updated_at": utcnow()}))
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

    def set_progress(self, run_id: str, progress: dict, sources: dict) -> None:
        """Live progress of a running search (replaced by the final statistics when it finishes)."""
        with self._sf() as s:
            s.execute(update(SearchRun).where(SearchRun.id == run_id, SearchRun.status == "running")
                      .values(stats={"progress": progress, "sources": sources}))
            s.commit()

    def recent_run_stats(self, since: datetime) -> list[tuple[datetime, dict]]:
        with self._sf() as s:
            return [(r.started_at, r.stats or {}) for r in s.scalars(select(SearchRun).where(SearchRun.started_at >= since))]

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

    def add_job(self, p: dict, origin: str = "user", applied: bool = False) -> tuple[str, bool]:
        """Store one job added by hand (jobsearch/intake.py). Returns (job id, created). A job already stored (same
        link, or same company + title) is returned as it is; with `applied` it is marked applied if it was not yet."""
        return _add_job(self, p, origin, applied)

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
    def persist_run(self, run_id: str, accepted: list[dict], known: list[dict], rejected: list[dict],
                    listings: list[dict] = ()) -> dict:
        """Insert new postings, record sightings of known ones (reopening them if they had closed), remember rejected
        pages, and close postings that left their company board or passed their expiry date. Returns ids and counts."""
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
            bumped = reopened = 0
            for job_id, source, url, is_new in dict(((j, (j, src, u, n)) for j, src, u, n in sightings)).values():
                sid = s.execute(_insert(s, JobSighting).values(run_id=run_id, job_id=job_id, source=source, url=url,
                                                               is_new=is_new, seen_at=now)
                                .on_conflict_do_nothing().returning(JobSighting.id)).scalar_one_or_none()
                if sid and not is_new:
                    s.execute(update(Job).where(Job.id == job_id).values(last_seen_at=now, times_seen=Job.times_seen + 1))
                    bumped += 1
                    reopened += s.execute(update(Job).where(Job.id == job_id, Job.closed_at.is_not(None))
                                          .values(closed_at=None, notion_dirty=True)).rowcount or 0
            closed = 0
            for board in listings:     # the API returned the company's whole board: what is missing has closed
                closed += s.execute(update(Job).where(
                    Job.source == board["source"], Job.company == board["company"], Job.closed_at.is_(None),
                    Job.canonical_url.not_in(board["urls"])).values(closed_at=now, notion_dirty=True)).rowcount or 0
            closed += s.execute(update(Job).where(Job.closed_at.is_(None), Job.valid_through.is_not(None),
                                                  Job.valid_through < now).values(closed_at=now, notion_dirty=True)).rowcount or 0
            for r in rejected:
                stmt = _insert(s, SeenUrl).values(canonical_url=canonical_url(r["url"]), source=r["source"],
                                                  title=(r.get("title") or "")[:300], reason=r["reason"], run_id=run_id, seen_at=now)
                s.execute(stmt.on_conflict_do_update(index_elements=["canonical_url"],
                                                     set_={"reason": r["reason"], "run_id": run_id, "seen_at": now,
                                                           "title": (r.get("title") or "")[:300]}))
        if new_ids or closed or reopened:
            db.jobs_changed()   # Core statements skip the ORM events: wake the Notion syncer ourselves
        return {"new_job_ids": list(dict.fromkeys(new_ids)), "known_bumped": bumped, "closed": closed, "reopened": reopened}


def _add_job(store: "Store", p: dict, origin: str, applied: bool) -> tuple[str, bool]:
    cu = p.get("canonical_url") or canonical_url(p["url"])
    dk = dedupe_key(p.get("company", ""), p["title"])
    now = utcnow()
    with store._sf() as s:
        existing = s.scalar(select(Job).where((Job.canonical_url == cu) | (Job.dedupe_key == dk)).limit(1))
        if existing is not None:
            if applied and existing.application_status == "not_applied":
                db.update_application(existing, application_status="applied")
                if existing.status in ("scraped", "scored"): existing.status = "tracked"
                s.commit()
            return existing.id, False
        job = Job(id=job_id_for(cu), source=p["source"], company=(p.get("company") or "")[:300], title=p["title"][:300],
                  location=(p.get("location") or "")[:300], url=p.get("url") or "", canonical_url=cu, dedupe_key=dk,
                  jd_text=p.get("jd_text") or "", discovered_at=now, last_seen_at=now, origin=origin,
                  status="tracked" if applied else "scraped", job_type=p.get("job_type", "job"),
                  employment_type=(p.get("employment_type") or "")[:100], is_remote=bool(p.get("is_remote")),
                  posted_at=parse_date(p.get("posted_at")), valid_through=parse_date(p.get("valid_through")),
                  jd_source=p.get("jd_source", ""), relevance=p.get("relevance"), times_seen=1)
        if applied: db.update_application(job, application_status="applied")
        s.add(job)
        try:
            s.commit()
        except IntegrityError:   # added at the same moment by a search: return that one
            s.rollback()
            return s.scalar(select(Job.id).where((Job.canonical_url == cu) | (Job.dedupe_key == dk)).limit(1)), False
        return job.id, True


def jobs_query(session: Session, *, status: str | None = None, source: str | None = None, job_type: str | None = None,
               state: str | None = None, q: str | None = None, since: datetime | None = None, limit: int | None = 500,
               application_status: str | None = None) -> Select:
    """The one query behind the jobs list, export and CLI. `q` is full-text over title, company and the saved
    description (PostgreSQL: websearch syntax - "exact phrase", -exclude, OR - ranked; SQLite: substring)."""
    stmt = select(Job)
    if status: stmt = stmt.where(Job.status == status)
    if source: stmt = stmt.where(Job.source == source)
    if application_status: stmt = stmt.where(Job.application_status == application_status)
    if job_type: stmt = stmt.where(Job.job_type == job_type)
    if state == "open": stmt = stmt.where(Job.closed_at.is_(None))
    elif state == "closed": stmt = stmt.where(Job.closed_at.is_not(None))
    if since: stmt = stmt.where(Job.discovered_at >= since)
    order = [Job.discovered_at.desc()]
    if q and q.strip():
        q = q.strip()[:200]
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        partial = [Job.title.ilike(like, escape="\\"), Job.company.ilike(like, escape="\\")]
        if session.get_bind().dialect.name == "postgresql":
            doc, tsq = literal_column(FTS_DOC), func.websearch_to_tsquery(literal_column("'english'"), q)
            stmt = stmt.where(or_(doc.op("@@")(tsq), *partial))
            order = [func.ts_rank(doc, tsq).desc()] + order
        else:
            stmt = stmt.where(or_(*partial, Job.jd_text.ilike(like, escape="\\")))
    stmt = stmt.order_by(*order)
    return stmt.limit(limit) if limit else stmt


def run_dict(r: SearchRun) -> dict:
    return {"id": r.id, "status": r.status, "trigger": r.trigger, "started_at": _iso(r.started_at),
            "finished_at": _iso(r.finished_at), "spec": r.spec or {}, "stats": r.stats or {}, "error": r.error}


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None
