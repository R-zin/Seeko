"""The job search as a LangGraph StateGraph.

    START -> plan --Send per source--> collect --> dedupe --Send per source--> fetch --> screen --> [review] --> persist --> finish -> END
                 \\-- nothing to search ----------^        \\-- nothing to read ----------^      \\-- no LLM / nothing to check --^

plan     builds one task per source (ATS APIs, careers pages, each enabled board) and records the run.
collect  finds leads: ATS APIs return full postings; boards and careers pages are browsed (paginated, polite).
dedupe   checks Postgres: already-stored jobs become sightings, recently rejected pages are skipped, the rest are
         queued for reading within the per-source budget (the overflow waits for the next run).
fetch    opens each new posting and extracts it (JSON-LD JobPosting first, then the page).
screen   deterministic checks: title, job vs internship, location / remote, freshness, description present, exclusions.
review   optional LLM check of pages that had no structured data: is it really one posting, does it fit the search.
persist  one transaction: new jobs (with the full description), sightings, rejected pages.
finish   run statistics, run status, log line.

Collect and fetch fan out per source (different sites, different browser profiles) and run concurrently; inside a
source everything is sequential. With a checkpointer every step is saved, so an interrupted run resumes where it stopped.
"""
from __future__ import annotations
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError
import db
from db import utcnow
from scraper.ats import detect_ats, fetch_watchlist
from scraper.boards import BOARDS, Board
from scraper.dedupe import canonical_url, dedupe_key
from . import classify
from .browse import BrowserFactory, Pacer, Robots, Session, persistent_browser
from .classify import _INTERN, title_matches
from .extract import extract_posting, iso
from .leads import board_leads, careers_leads
from .spec import BROWSER_SOURCES, SearchSpec
from .state import SearchInput, SearchOutput, SearchState
from .store import Store, job_id_for

USER_AGENT = "Mozilla/5.0 (compatible; Seeko job search)"


def _default_llm():
    from config.settings import get_settings
    s = get_settings()
    if not (s.llm_base_url.strip() or s.llm_api_key.strip()):
        return None
    from llm.client import LLMClient
    return LLMClient.from_settings(task="scoring")


@dataclass
class Deps:
    """Everything the graph touches outside itself; tests swap these for fakes."""
    store: Store = field(default_factory=Store)
    browser: BrowserFactory = persistent_browser
    http: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=30, follow_redirects=True,
                                                                       headers={"User-Agent": USER_AGENT})
    llm: Callable[[], object | None] = _default_llm
    log: Callable[..., None] = db.log
    now: Callable[[], datetime] = utcnow


# ── planning ────────────────────────────────────────────────────────────────────────────────────────────────────
def board_queries(spec: SearchSpec, board: Board) -> tuple[list[dict], int]:
    """Search queries for one board, interleaved so a small budget still covers every title and job type.
    Returns (queries within budget, number dropped)."""
    locs = ["Remote"] if spec.remote_only else (spec.locations or [""])
    out: list[dict] = []
    for loc in locs:
        for jt in spec.job_types:
            internship = jt == "internship"
            for title in spec.role_titles:
                q = title
                if internship and not board.native_internship_filter and not _INTERN.search(title):
                    q = f"{title} intern"
                item = {"query": q, "location": loc, "internship": internship}
                if item not in out:
                    out.append(item)
    return out[:spec.max_queries], max(0, len(out) - spec.max_queries)


def build_tasks(spec: SearchSpec) -> list[dict]:
    tasks: list[dict] = []
    if "watchlist" in spec.sources and spec.watchlist_urls:
        ats = [u for u in spec.watchlist_urls if detect_ats(u)]
        pages = [u for u in spec.watchlist_urls if not detect_ats(u)]
        if ats:
            tasks.append({"source": "ats", "kind": "api", "targets": ats})
        if pages:
            tasks.append({"source": "careers", "kind": "careers", "targets": pages})
    for name in BROWSER_SOURCES:
        if name in spec.sources and spec.role_titles:
            queries, dropped = board_queries(spec, BOARDS[name])
            tasks.append({"source": name, "kind": "board", "queries": queries, "queries_dropped": dropped})
    return tasks


class PageReview(BaseModel):
    is_job_posting: bool
    title: str = ""
    company: str = ""
    location: str = ""
    is_internship: bool = False
    is_remote: bool = False
    matches_search: bool = True
    reason: str = ""


REVIEW_SYSTEM = (
    "You check web pages collected by a job search. Decide whether the text is ONE specific, currently advertised job "
    "or internship posting (not a list of jobs, a careers landing page, a blog post, a 'position filled' notice or a "
    "generic talent-pool form). Extract title, company and location only from the text; use an empty string when the "
    "text does not say. matches_search is true when the role plausibly fits at least one of the target roles. "
    "Be strict and never guess.")


def build_graph(deps: Deps | None = None) -> StateGraph:
    d = deps or Deps()

    async def plan(state: SearchState, config) -> dict:
        run_id = state.get("run_id") or config["configurable"]["thread_id"]
        spec = SearchSpec.model_validate(state["spec"]) if state.get("spec") else SearchSpec.from_profile()
        tasks = build_tasks(spec)
        spec_json = spec.model_dump(mode="json")
        await asyncio.to_thread(d.store.start_run, run_id, spec_json, state.get("trigger") or "manual")
        if not tasks:
            d.log("job search: nothing to search - add target roles and enable a source, or add career pages", "warn")
        stats = {t["source"]: {"queries_dropped": t["queries_dropped"]} for t in tasks if t.get("queries_dropped")}
        return {"run_id": run_id, "spec": spec_json, "tasks": tasks, "source_stats": stats}

    def route_collect(state: SearchState):
        tasks = state.get("tasks") or []
        return [Send("collect", {"task": t, "spec": state["spec"]}) for t in tasks] or "dedupe"

    # ── collect ─────────────────────────────────────────────────────────────────────────────────────────────
    async def collect(payload: dict) -> dict:
        task, spec = payload["task"], SearchSpec.model_validate(payload["spec"])
        src, st = task["source"], Counter()
        extra: dict = {}
        try:
            if task["kind"] == "api":
                leads = await _collect_api(task, spec, st, extra)
            elif task["kind"] == "careers":
                leads = await _collect_careers(task, spec, st, extra)
            else:
                leads = await _collect_board(task, spec, st, extra)
        except Exception as e:   # one broken source never sinks the run
            leads = []
            st["errors"] += 1; extra["error"] = f"{type(e).__name__}: {e}"[:300]
            d.log(f"job search {src}: collect failed: {e}", "error")
        for lead in leads:
            lead["task"] = src
        return {"leads": leads, "source_stats": {src: {"leads": len(leads), **st, **extra}}}

    async def _collect_api(task, spec: SearchSpec, st: Counter, extra: dict) -> list[dict]:
        errors: list[str] = []
        known = d.store.is_known_url

        def want(title: str, url: str) -> bool:      # Workday: skip description calls for irrelevant/known jobs
            return title_matches(title, spec.role_titles) and not known(url)

        async with d.http() as client:
            found = await fetch_watchlist(task["targets"], client, want=want, errors=errors)
        if errors:
            st["errors"] += len(errors); extra["error_details"] = errors[:5]
            for e in errors: d.log(f"job search: career board failed: {e}", "warn")
        leads = []
        for p in found:
            if not title_matches(p.title, spec.role_titles):
                st["skipped_title"] += 1
                continue
            leads.append({"source": p.source, "url": p.url, "canonical_url": canonical_url(p.url), "title": p.title,
                          "company": p.company, "location": p.location, "complete": True, "jd_text": p.jd_text,
                          "employment_type": p.employment_type, "posted_at": iso(p.posted_at), "valid_through": None,
                          "is_remote": p.is_remote, "jd_source": "api"})
        return leads

    def _keep(lead: dict, spec: SearchSpec, st: Counter, seen: set) -> bool:
        if lead["canonical_url"] in seen:
            return False
        seen.add(lead["canonical_url"])
        if lead["title"] and not title_matches(lead["title"], spec.role_titles):
            st["skipped_title"] += 1
            return False
        loc = lead.get("location") or ""
        if loc and not classify.location_ok(loc, classify.detect_remote(loc, lead["title"]), spec.locations, spec.remote_only):
            st["skipped_location"] += 1          # the results page already says it is elsewhere: don't open it
            return False
        return True

    async def _collect_board(task, spec: SearchSpec, st: Counter, extra: dict) -> list[dict]:
        board = BOARDS[task["source"]]
        leads: list[dict] = []
        seen: set[str] = set()
        cap = spec.max_details * 3
        async with d.browser(board.source, spec.headless) as ctx:
            sess = Session(ctx, Pacer(spec.delay_min_s, spec.delay_max_s))
            for q in task["queries"]:
                for page_no in range(spec.max_pages):
                    url = board.search_url(q["query"], q["location"], page_no, q["internship"])
                    if not url:
                        break
                    read = await sess.read_listing(url)
                    st["pages"] += 1
                    if read.blocked:
                        extra["blocked"] = read.blocked
                        d.log(f"job search {board.source}: stopped, {read.blocked} at {read.url}", "warn")
                        return leads
                    if read.error:
                        st["errors"] += 1
                        break
                    found = board_leads(read.anchors, board)
                    fresh = [lead for lead in found if lead["canonical_url"] not in seen]
                    if not fresh:
                        break                           # past the last page of results
                    leads += [lead for lead in fresh if _keep(lead, spec, st, seen)]
                    if len(leads) >= cap:
                        return leads
        return leads

    async def _collect_careers(task, spec: SearchSpec, st: Counter, extra: dict) -> list[dict]:
        leads: list[dict] = []
        seen: set[str] = set()
        blocked: list[str] = []
        async with d.http() as client, d.browser("search", spec.headless) as ctx:
            robots = Robots(spec.respect_robots, client)
            sess = Session(ctx, Pacer(spec.delay_min_s, spec.delay_max_s))
            for target in task["targets"]:
                if not await robots.allowed(target):
                    st["robots_disallowed"] += 1
                    d.log(f"job search: robots.txt disallows {target}; skipped", "warn")
                    continue
                read = await sess.read_listing(target)
                pages = 1
                while True:
                    st["pages"] += 1
                    if read.blocked:
                        blocked.append(f"{target}: {read.blocked}")
                        d.log(f"job search careers: stopped at {target} ({read.blocked})", "warn")
                        break
                    if read.error:
                        st["errors"] += 1
                        break
                    fresh = [x for x in careers_leads(read.anchors, target) if x["canonical_url"] not in seen]
                    leads += [x for x in fresh if _keep(x, spec, st, seen)]
                    if not fresh or pages >= spec.max_pages:
                        break
                    nxt = await sess.more()
                    if nxt is None:
                        break
                    read, pages = nxt, pages + 1
        if blocked:
            extra["blocked_targets"] = blocked
        return leads

    # ── dedupe against the database ─────────────────────────────────────────────────────────────────────────
    async def dedupe(state: SearchState) -> dict:
        spec = SearchSpec.model_validate(state["spec"])
        unique: dict[str, dict] = {}
        for lead in state.get("leads") or []:
            cur = unique.get(lead["canonical_url"])
            if cur is None or (lead.get("complete") and not cur.get("complete")):
                unique[lead["canonical_url"]] = lead
        leads = list(unique.values())
        keys = [dedupe_key(x["company"], x["title"]) for x in leads if x.get("complete") and x.get("company")]
        known_map = await asyncio.to_thread(d.store.known, [x["canonical_url"] for x in leads], keys)
        skip = await asyncio.to_thread(d.store.recently_rejected,
                                       [x["canonical_url"] for x in leads if not x.get("complete")], spec.revisit_days)
        known, postings, pending, st = [], [], {}, {}
        for x in leads:
            c = st.setdefault(x["task"], Counter())
            jid = known_map.get(x["canonical_url"])
            if not jid and x.get("complete") and x.get("company"):
                jid = known_map.get(dedupe_key(x["company"], x["title"]))
            if jid:
                known.append({"job_id": jid, "source": x["source"], "url": x["url"], "task": x["task"]})
                c["known"] += 1
            elif x.get("complete"):
                postings.append(x)
            elif x["canonical_url"] in skip:
                c["skipped_rejected_before"] += 1
            elif len(pending.setdefault(x["task"], [])) < spec.max_details:
                pending[x["task"]].append(x)
            else:
                c["deferred"] += 1                     # budget used up; found again (and read) next run
        return {"known": known, "pending": pending, "postings": postings,
                "source_stats": {k: dict(v) for k, v in st.items()}}

    def route_fetch(state: SearchState):
        pending = state.get("pending") or {}
        return [Send("fetch", {"task": src, "leads": ls, "spec": state["spec"]}) for src, ls in pending.items() if ls] or "screen"

    # ── fetch postings ──────────────────────────────────────────────────────────────────────────────────────
    async def fetch(payload: dict) -> dict:
        src, leads, spec = payload["task"], payload["leads"], SearchSpec.model_validate(payload["spec"])
        board = BOARDS.get(src)
        st, extra, out = Counter(), {}, []
        try:
            async with d.http() as client, d.browser(board.source if board else "search", spec.headless) as ctx:
                robots = Robots(spec.respect_robots and board is None, client)
                sess = Session(ctx, Pacer(spec.delay_min_s, spec.delay_max_s))
                for lead in leads:
                    if not await robots.allowed(lead["url"]):
                        st["robots_disallowed"] += 1
                        continue
                    read = await sess.read_detail(lead["url"], board.description_selector if board else "")
                    if read.blocked:
                        extra["blocked"] = read.blocked
                        d.log(f"job search {src}: stopped, {read.blocked} at {read.url}", "warn")
                        break
                    if read.error:
                        if read.status in (404, 410):
                            out.append({**lead, "reject": "gone"})
                        else:
                            st["errors"] += 1     # transient: not remembered, retried next run
                        continue
                    p = extract_posting(read.html, lead["url"], read.info, lead)
                    if p is None:
                        out.append({**lead, "reject": "not_a_posting"})
                        continue
                    p.update(source=lead["source"], task=src, url=lead["url"], canonical_url=lead["canonical_url"])
                    out.append(p)
                    st["fetched"] += 1
        except Exception as e:
            st["errors"] += 1; extra["error"] = f"{type(e).__name__}: {e}"[:300]
            d.log(f"job search {src}: fetch failed: {e}", "error")
        return {"postings": out, "source_stats": {src: {**st, **extra}}}

    # ── screen ──────────────────────────────────────────────────────────────────────────────────────────────
    async def screen(state: SearchState) -> dict:
        spec, now = state["spec"], d.now()
        accepted, rejected, st = [], [], {}
        seen_keys: set[str] = set()
        for p in state.get("postings") or []:
            task = p.get("task", p["source"])
            c = st.setdefault(task, Counter())
            reason = p.get("reject")
            if not reason:
                ok, reason, p = classify.screen(p, spec, now)
                if ok:
                    key = dedupe_key(p.get("company", ""), p["title"])
                    if key in seen_keys or p["canonical_url"] in seen_keys:
                        c["duplicate_in_run"] += 1
                        continue
                    seen_keys |= {key, p["canonical_url"]}
                    accepted.append(p)
                    continue
            c[f"rejected_{reason}"] += 1
            rejected.append({"url": p["url"], "source": p["source"], "title": p.get("title", ""), "reason": reason,
                             "task": task, "record": p.get("jd_source") != "api"})
        return {"accepted": accepted, "rejected": rejected, "source_stats": {k: dict(v) for k, v in st.items()}}

    def route_review(state: SearchState):
        spec = state["spec"]
        needs = any(p.get("jd_source") in ("page", "selector") for p in state.get("accepted") or [])
        return "review" if needs and spec.get("llm_review") and d.llm() is not None else "persist"

    # ── review (LLM) ────────────────────────────────────────────────────────────────────────────────────────
    async def review(state: SearchState) -> dict:
        from llm.structured import complete_json
        spec, llm = state["spec"], d.llm()
        sem = asyncio.Semaphore(3)
        roles = ", ".join(spec.get("role_titles") or []) or "any"

        async def one(p: dict) -> tuple[dict, str | None]:
            if p.get("jd_source") not in ("page", "selector"):
                return p, None
            async with sem:
                try:
                    r = await complete_json(llm, PageReview, [
                        {"role": "system", "content": REVIEW_SYSTEM},
                        {"role": "user", "content": f"TARGET ROLES: {roles}\nURL: {p['url']}\nPAGE TITLE: {p.get('title', '')}\n\n"
                                                    f"PAGE TEXT:\n{p.get('jd_text', '')[:7000]}"}])
                except Exception as e:      # LLM down: keep the posting, say it was not reviewed
                    return {**p, "review": f"error: {e}"[:200]}, None
            if not r.is_job_posting:
                return p, "not_a_posting"
            if not r.matches_search:
                return p, "irrelevant_llm"
            q = dict(p, review="ok")
            for k in ("title", "company", "location"):
                if not (q.get(k) or "").strip() and getattr(r, k).strip():
                    q[k] = getattr(r, k).strip()
            q["is_remote"] = bool(q.get("is_remote")) or r.is_remote
            if r.is_internship and q.get("job_type") == "job":
                q["job_type"] = "internship"
                if "internship" not in spec.get("job_types", []):
                    return q, "unwanted_internship"
            return q, None

        results = await asyncio.gather(*(one(p) for p in state.get("accepted") or []))
        accepted, rejected, st = [], list(state.get("rejected") or []), {}
        for p, reason in results:
            if reason:
                st.setdefault(p["task"], Counter())[f"rejected_{reason}"] += 1
                rejected.append({"url": p["url"], "source": p["source"], "title": p.get("title", ""),
                                 "reason": reason, "task": p["task"], "record": True})
            else:
                accepted.append(p)
        return {"accepted": accepted, "rejected": rejected, "source_stats": {k: dict(v) for k, v in st.items()}}

    # ── persist + finish ────────────────────────────────────────────────────────────────────────────────────
    async def persist(state: SearchState) -> dict:
        res = await asyncio.to_thread(d.store.persist_run, state["run_id"], state.get("accepted") or [],
                                      state.get("known") or [], [r for r in state.get("rejected") or [] if r.get("record")])
        return {"new_job_ids": res["new_job_ids"]}

    async def finish(state: SearchState) -> dict:
        new_ids = set(state.get("new_job_ids") or [])
        new_by_type = Counter(p.get("job_type", "job") for p in state.get("accepted") or [] if job_id_for(p["url"]) in new_ids)
        sources = state.get("source_stats") or {}
        stats = {
            "leads": len({x["canonical_url"] for x in state.get("leads") or []}),
            "known": len(state.get("known") or []),
            "read": sum(int(s.get("fetched", 0)) for s in sources.values()),
            "new": len(new_ids),
            "new_jobs": new_by_type.get("job", 0),
            "new_internships": new_by_type.get("internship", 0),
            "rejected": dict(Counter(r["reason"] for r in state.get("rejected") or [])),
            "deferred": sum(int(s.get("deferred", 0)) for s in sources.values()),
            "blocked": {k: v["blocked"] for k, v in sources.items() if v.get("blocked")},
            "errors": sum(int(s.get("errors", 0)) for s in sources.values()),
            "sources": sources,
        }
        await asyncio.to_thread(d.store.finish_run, state["run_id"], "done", stats)
        blocked = "; ".join(f"{k} blocked ({v})" for k, v in stats["blocked"].items())
        d.log(f"job search {state['run_id']}: {stats['leads']} found, {stats['known']} already known, "
              f"{stats['new']} new ({stats['new_jobs']} jobs, {stats['new_internships']} internships), "
              f"{sum(stats['rejected'].values())} rejected" + (f", {stats['deferred']} left for next run" if stats["deferred"] else "")
              + (f"; {blocked}" if blocked else ""), "warn" if blocked else "info")
        return {"stats": stats, "new_job_ids": sorted(new_ids)}

    g = StateGraph(SearchState, input_schema=SearchInput, output_schema=SearchOutput)
    g.add_node("plan", plan)
    g.add_node("collect", collect)
    g.add_node("dedupe", dedupe)
    g.add_node("fetch", fetch)
    g.add_node("screen", screen)
    g.add_node("review", review)
    g.add_node("persist", persist, retry_policy=RetryPolicy(max_attempts=3, retry_on=OperationalError))
    g.add_node("finish", finish)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", route_collect, ["collect", "dedupe"])
    g.add_edge("collect", "dedupe")
    g.add_conditional_edges("dedupe", route_fetch, ["fetch", "screen"])
    g.add_edge("fetch", "screen")
    g.add_conditional_edges("screen", route_review, ["review", "persist"])
    g.add_edge("review", "persist")
    g.add_edge("persist", "finish")
    g.add_edge("finish", END)
    return g
