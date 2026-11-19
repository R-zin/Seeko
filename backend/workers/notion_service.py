"""Live Notion sync: every change to a job reaches its Notion page, and tracking edits made in Notion come back.

Push: a change to any column Notion shows sets `jobs.notion_dirty` (ORM event in db.py; Core writers set it
themselves) and wakes this syncer after the commit. It pushes dirty jobs one at a time (Notion allows ~3 requests/s),
skipping the request when the page would not change (hash of the encoded properties). New pages get the full job
description as their body. A lost page id never creates a duplicate: the page is looked up by its Seeko ID first.

Pull: the two-way properties (Application Status, Applied Date, Next Step, Next Step Date, Notes) are read back from
pages edited since the last pull and merged three-way against the last synced value (`notion_base`): a Notion edit
is adopted when Seeko has not changed that field since; when both changed, Seeko's value wins and is pushed.

Failures never block the pipeline. Transient errors (rate limit, outage, MCP process) back off globally; an error
specific to one job backs off that job only. Everything is visible in GET /api/notion/status.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import time
from datetime import date, datetime, timedelta
from sqlalchemy import case, func, or_, select, update
import db
from config.settings import get_settings
from db import Job, application_key, log, update_application, utcnow
from mcp_notion.client import NotionError, NotionMCP
from mcp_notion.schema import (APPLICATION_COLORS, STATUS_LABELS, TWO_WAY, SchemaReport, check, decode,
                               encode_properties, setup_patch)
from mcp_notion.sync import NotionDB

PACE_S = 0.35            # between Notion requests (~3/s)
MAX_JOB_BACKOFF = timedelta(hours=6)


def _day(v) -> str | None:
    if v is None: return None
    return (v.date() if isinstance(v, datetime) else v).isoformat()


def _app_label(key: str) -> str:
    return db.APPLICATION_STATUSES.get(key, key) if key else "Not applied"


def job_values(j: Job) -> dict:
    """What the Notion page shows, keyed by mcp_notion.schema.Prop.field."""
    return {"title": j.title, "company": j.company, "status": STATUS_LABELS.get(j.status, j.status.replace("_", " ").title()),
            "application_status": _app_label(j.application_status), "applied_on": _day(j.applied_at),
            "next_step": j.next_step or "", "next_step_date": _day(j.next_step_date), "notes": j.notes or "",
            "job_type": "Internship" if j.job_type == "internship" else "Job", "location": j.location or "",
            "remote": bool(j.is_remote), "source": j.source, "url": j.url or None, "score": j.score,
            "employment_type": j.employment_type or "", "discovered_on": _day(j.discovered_at),
            "posted_on": _day(j.posted_at), "apply_by": _day(j.valid_through), "closed_on": _day(j.closed_at),
            "summary": j.tailored_summary or "", "seeko_id": j.id}


def two_way_values(values: dict) -> dict:
    return {p.field: (values.get(p.field) or "") for p in TWO_WAY}


def body_key(jd_text: str) -> str:
    return hashlib.sha1(jd_text.strip().encode()).hexdigest()[:12] if (jd_text or "").strip() else ""


def existing_page_props(props: dict, values: dict, base: dict) -> dict:
    """What to send to a page that already exists. A two-way property goes out only when Seeko changed it since the
    last sync, so an edit made in Notion (not pulled yet) is never overwritten by a push for some other change. A field
    never synced (page from an older version) is only sent if Seeko has a value, so it cannot blank one set in Notion."""
    out = dict(props)
    for p in TWO_WAY:
        if p.name not in out: continue
        local = values.get(p.field) or ""
        if (local == base[p.field]) if p.field in base else not local:
            out.pop(p.name)
    return out


def page_hash(properties: dict) -> str:
    return hashlib.sha256(json.dumps(properties, sort_keys=True, default=str).encode()).hexdigest()[:32]


def merge(base: str | None, local: str, remote: str) -> tuple[str, str]:
    """Three-way merge of one two-way field -> (action, value). Actions: keep (nothing to do), adopt (take Notion's
    value), converge (both already equal; just record it), conflict (both changed: Seeko's value stays)."""
    if base is None: return "keep", local                # never synced: the next push establishes the base
    if remote == base: return "keep", local              # Notion unchanged (a Seeko change, if any, is pushed)
    if local == base: return "adopt", remote
    if local == remote: return "converge", remote
    return "conflict", local


def _from_notion(field: str, value: str):
    """A two-way value from Notion as the job column it updates."""
    if field == "application_status": return "application_status", application_key(value)
    if field == "applied_on": return "applied_at", datetime.fromisoformat(value) if value else None
    if field == "next_step_date": return "next_step_date", date.fromisoformat(value) if value else None
    return field, value


class NotionSync:
    def __init__(self, session_factory=None, client_factory=None, pace_s: float = PACE_S, debounce_s: float = 1.0):
        self._sf = session_factory
        self._client_factory = client_factory or (lambda token: NotionMCP(token))
        self.pace_s, self.debounce_s = pace_s, debounce_s
        self.client: NotionMCP | None = None
        self.db: NotionDB | None = None
        self.report: SchemaReport | None = None
        self._key: tuple[str, str] | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._pull_since: datetime | None = None
        self._pulled_at = 0.0
        self._backoff_s = 0.0
        self.state: dict = {"connected": False, "last_push_at": None, "last_pull_at": None, "last_error": "",
                            "pushed": 0, "pulled": 0, "backoff_until": None, "running": False}

    # ── plumbing ──────────────────────────────────────────────────────────────────────────────────────────────────
    def sessions(self):
        return (self._sf or db.SessionLocal)()

    @property
    def lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:   # one MCP session: serialize every call
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    @staticmethod
    def configured() -> bool:
        st = get_settings()
        return bool(st.notion_token.strip() and st.notion_database_id.strip())

    def kick(self) -> None:
        """Wake the syncer now (thread-safe; called after every commit that changed a job)."""
        if self._loop is not None and self._wake is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._wake.set)

    async def close(self) -> None:
        if self.client is not None:
            try: await self.client.close()
            except Exception: pass
        self.client = self.db = self.report = None
        self._key = None; self.state["connected"] = False

    async def _connect_locked(self) -> dict:
        await self.close()
        st = get_settings()
        self.client = self._client_factory(st.notion_token.strip())
        tools = await self.client.connect()
        self.db = NotionDB(self.client, st.notion_database_id)
        self.report = check(await self.db.open())
        self._key = (st.notion_token.strip(), st.notion_database_id.strip())
        self._pull_since = None
        self.state.update(connected=True, last_error="")
        return {**self.report.as_dict(), "tools": tools, "data_source_id": self.db.data_source_id}

    async def connect(self) -> dict:
        if not self.configured():
            return {"ok": False, "error": "NOTION_TOKEN / NOTION_DATABASE_ID not set"}
        async with self.lock:
            try:
                return await self._connect_locked()
            except Exception as e:
                await self.close()
                self.state["last_error"] = str(e)[:500]
                raise

    async def ensure(self) -> None:
        st = get_settings()
        if self.db is None or self._key != (st.notion_token.strip(), st.notion_database_id.strip()):
            await self.connect()

    # ── schema ────────────────────────────────────────────────────────────────────────────────────────────────────
    async def setup(self) -> dict:
        """Add every missing tracker property to the database. Existing properties are never changed."""
        await self.ensure()
        async with self.lock:
            patch = setup_patch(self.report)
            await self.db.add_properties(patch)
            self.report = check(self.db.properties)
        self.resync()   # pages need the new properties filled in
        return {**self.report.as_dict(), "added": sorted(patch)}

    def resync(self) -> int:
        """Re-push every job (e.g. after adding properties or switching databases); pages deleted in Notion are
        recreated."""
        with self.sessions() as s:
            n = s.execute(update(Job).values(notion_dirty=True, notion_hash="", notion_attempts=0, notion_retry_at=None,
                                             notion_error="")).rowcount or 0
            gone = s.execute(select(Job.id, Job.notion_base).where(Job.notion_base.is_not(None))).all()
            for jid, base in gone:
                if (base or {}).get("gone"):
                    s.execute(update(Job).where(Job.id == jid).values(notion_page_id="", notion_base={}))
            s.commit()
        self.kick()
        return n

    # ── push ──────────────────────────────────────────────────────────────────────────────────────────────────────
    async def push(self, job_id: str) -> str:
        """Bring one job's page up to date. Returns created | updated | unchanged | skipped | gone."""
        await self.ensure()
        with self.sessions() as s:
            j = s.get(Job, job_id)
            if j is None: return "skipped"
            values, page_id, base = job_values(j), j.notion_page_id, dict(j.notion_base or {})
            old_hash, stamp, jd, status = j.notion_hash, j.updated_at, j.jd_text or "", j.status
        if base.get("gone") or (status == "skipped" and not page_id):   # deleted in Notion / never worth a page
            self._mark(job_id, stamp, page_id=page_id, base=base, hash_=old_hash)
            return "gone" if base.get("gone") else "skipped"
        props = encode_properties(values, self.report)
        h, body = page_hash(props), body_key(jd)
        needs_body = bool(body) and "body" not in base
        if page_id and h == old_hash and not needs_body:
            self._mark(job_id, stamp, page_id=page_id, base=base, hash_=h)
            return "unchanged"
        async with self.lock:
            result = "updated"
            if not page_id and "Seeko ID" in self.report.mapping:   # created before, but the id was never saved?
                page_id = await self.db.find_page("Seeko ID", job_id) or ""
                needs_body = needs_body and not page_id   # a page found this way was created with its body
            if page_id:
                try:
                    await self.db.update_page(page_id, existing_page_props(props, values, base))
                except NotionError as e:
                    if not e.gone: raise
                    log(f"Notion page for '{values['title']}' was deleted in Notion; it will not be recreated "
                        f"(Setup → Notion → Resync all recreates deleted pages)", "warn", job_id)
                    self._mark(job_id, stamp, page_id=page_id, base={**base, "gone": True}, hash_=h)
                    return "gone"
                if needs_body:
                    await self.db.append_description(page_id, jd)
            else:
                page_id = await self.db.create_page(props, jd)
                result = "created"
        new_base = {**two_way_values(values), "body": body if (body and (needs_body or result == "created")) else base.get("body", "")}
        if not new_base["body"]: new_base.pop("body")
        self._mark(job_id, stamp, page_id=page_id, base=new_base, hash_=h)
        self.state["pushed"] += 1; self.state["last_push_at"] = utcnow().isoformat()
        return result

    def _mark(self, job_id: str, stamp: datetime | None, *, page_id: str, base: dict, hash_: str) -> None:
        """Record a successful sync. The dirty flag is only cleared if the job did not change while we pushed."""
        unchanged = Job.updated_at.is_(None) if stamp is None else Job.updated_at == stamp
        with self.sessions() as s:
            s.execute(update(Job).where(Job.id == job_id).values(
                notion_page_id=page_id, notion_hash=hash_, notion_base=base, notion_synced_at=utcnow(),
                notion_error="", notion_attempts=0, notion_retry_at=None,
                notion_dirty=case((unchanged, False), else_=Job.notion_dirty)))
            s.commit()

    def _job_failed(self, job_id: str, err: Exception) -> None:
        with self.sessions() as s:
            j = s.get(Job, job_id)
            if j is None: return
            j.notion_attempts = (j.notion_attempts or 0) + 1
            j.notion_retry_at = utcnow() + min(timedelta(minutes=2 ** min(j.notion_attempts, 9)), MAX_JOB_BACKOFF)
            j.notion_error = str(err)[:1000]
            first = j.notion_attempts == 1
            s.commit()
        if first: log(f"Notion sync failed: {err}", "warn", job_id)

    def pending_ids(self, limit: int = 100) -> list[str]:
        with self.sessions() as s:
            return list(s.scalars(select(Job.id).where(Job.notion_dirty.is_(True), or_(
                Job.notion_retry_at.is_(None), Job.notion_retry_at <= utcnow())).order_by(Job.updated_at).limit(limit)))

    async def drain(self, limit: int = 100) -> int:
        """Push dirty jobs until none are left (or `limit` per call). Transient errors stop the drain and propagate."""
        n = 0
        ids = self.pending_ids(limit)
        if ids: await self.ensure()   # connection / auth problems are global, not the fault of each job
        for job_id in ids:
            try:
                if await self.push(job_id) in ("created", "updated"):
                    n += 1
                    await asyncio.sleep(self.pace_s)
            except NotionError as e:
                if e.transient or e.status in (401, 403): raise
                self._job_failed(job_id, e)
            except LookupError:   # the MCP server lacks a tool we need: nothing will work until that is fixed
                raise
            except Exception as e:  # bad data for this job: don't let it block the others
                self._job_failed(job_id, e)
        return n

    # ── pull ──────────────────────────────────────────────────────────────────────────────────────────────────────
    async def pull(self) -> int:
        """Adopt tracking edits made in Notion since the last pull. Returns the number of jobs changed."""
        await self.ensure()
        props = [p for p in TWO_WAY if p.name in self.report.mapping]
        if not props: return 0
        started = utcnow()
        flt = None
        if self._pull_since:   # Notion rounds last_edited_time to the minute: overlap generously
            flt = {"timestamp": "last_edited_time",
                   "last_edited_time": {"on_or_after": (self._pull_since - timedelta(minutes=2)).isoformat() + "Z"}}
        changed, cursor = 0, None
        while True:
            async with self.lock:
                res = await self.db.query(flt, cursor)
            for page in res.get("results") or []:
                changed += self._merge_page(page, props)
            if not res.get("has_more") or not res.get("next_cursor"): break
            cursor = res["next_cursor"]
            await asyncio.sleep(self.pace_s)
        self._pull_since = started
        self.state["last_pull_at"] = started.isoformat(); self.state["pulled"] += changed
        return changed

    def _merge_page(self, page: dict, props) -> int:
        if page.get("in_trash") or page.get("archived"): return 0
        pp = page.get("properties") or {}
        seeko_id = decode(pp.get("Seeko ID"))
        with self.sessions() as s:
            j = s.scalar(select(Job).where(Job.notion_page_id == page["id"]).limit(1))
            if j is None and seeko_id:
                j = s.get(Job, seeko_id)
            if j is None: return 0          # a row added by hand in Notion: not Seeko's
            base = dict(j.notion_base or {})
            local = two_way_values(job_values(j))
            edits, notes = {}, []
            for p in props:
                remote = decode(pp.get(p.name))
                action, value = merge(base.get(p.field), local[p.field], remote)
                if action == "adopt":
                    col, v = _from_notion(p.field, value); edits[col] = v; base[p.field] = value
                elif action == "converge":
                    base[p.field] = value
                elif action == "conflict":
                    notes.append(f"{p.name} (kept Seeko's “{local[p.field]}” over Notion's “{remote}”)")
            if edits:
                update_application(j, **edits)
            if edits or base != (j.notion_base or {}):
                j.notion_base = base
            if not j.notion_page_id:
                j.notion_page_id = page["id"]
            s.commit()
        if edits: log(f"from Notion: {', '.join(sorted(edits))} updated for {j.title}", "info", j.id)
        if notes: log(f"Notion edit conflict: {'; '.join(notes)}", "warn", j.id)
        return 1 if edits else 0

    # ── loop ──────────────────────────────────────────────────────────────────────────────────────────────────────
    def _transient(self, err: Exception) -> None:
        self._backoff_s = min(max(self._backoff_s * 2, 15.0), 600.0)
        self.state["last_error"] = str(err)[:500]
        self.state["backoff_until"] = (utcnow() + timedelta(seconds=self._backoff_s)).isoformat()
        if isinstance(err, NotionError) and err.status == 0:
            self.db = None   # the MCP process may be gone: reconnect next time

    async def tick(self) -> None:
        """One round: push everything pending, then pull if it is due."""
        await self.drain(limit=500)
        interval = get_settings().notion_pull_interval_s
        if interval > 0 and time.monotonic() - self._pulled_at >= interval:
            self._pulled_at = time.monotonic()
            await self.pull()
            await self.drain(limit=500)   # pushes caused by adopted edits (e.g. Applied Date filled in)
        self._backoff_s = 0.0; self.state["backoff_until"] = None; self.state["last_error"] = ""

    async def run(self, poll_s: float = 30.0) -> None:
        self._loop, self._wake = asyncio.get_running_loop(), asyncio.Event()
        db.on_jobs_changed(self.kick)
        self.state["running"] = True
        try:
            while True:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=max(poll_s, self._backoff_s))
                    await asyncio.sleep(self.debounce_s)   # batch a burst of commits into one round
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                if not self.configured():
                    continue
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    first = not self.state["last_error"]
                    self._transient(e)
                    if first: log(f"Notion sync paused: {e}", "warn")
        finally:
            self.state["running"] = False
            await self.close()

    def status(self) -> dict:
        with self.sessions() as s:
            total = s.scalar(select(func.count()).select_from(Job).where(Job.notion_page_id != "")) or 0
            pending = s.scalar(select(func.count()).select_from(Job).where(
                Job.notion_dirty.is_(True), or_(Job.status != "skipped", Job.notion_page_id != ""))) or 0
            errors = s.scalar(select(func.count()).select_from(Job).where(Job.notion_error != "")) or 0
        return {"configured": self.configured(), **self.state, "synced": total, "pending": pending, "errors": errors,
                "schema": self.report.as_dict() if self.report else None,
                "application_statuses": list(APPLICATION_COLORS)}


service = NotionSync()


async def connect() -> dict:
    return await service.connect()
