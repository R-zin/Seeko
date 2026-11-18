"""The LangGraph job search end to end: real headless Chromium browsing a fixture careers site and a fake board
(served by Playwright route interception - no network), real database writes, checkpoints and resume."""
import json
from collections import Counter
from contextlib import asynccontextmanager
from datetime import timedelta
import httpx
import pytest
from playwright.async_api import async_playwright
from sqlalchemy import select
from db import Job, SeenUrl, utcnow
from jobsearch import service
from jobsearch.graph import Deps, PageReview, build_graph
from jobsearch.spec import SearchSpec
from jobsearch.store import Store

SITE = "https://careers.acme.test"
JD = "<p>" + "You will design and run backend services in Python and PostgreSQL. " * 8 + "</p><ul><li>APIs</li><li>SQL</li></ul>"


def ld(title, etype="FULL_TIME", valid=None, remote=False, locality="Kochi"):
    d = {"@context": "https://schema.org", "@type": "JobPosting", "title": title, "description": JD,
         "hiringOrganization": {"@type": "Organization", "name": "Acme"}, "employmentType": etype,
         "datePosted": (utcnow() - timedelta(days=3)).date().isoformat(),
         "jobLocation": {"@type": "Place", "address": {"addressLocality": locality, "addressCountry": "India"}}}
    if valid: d["validThrough"] = valid
    if remote: d["jobLocationType"] = "TELECOMMUTE"
    return f'<html><head><script type="application/ld+json">{json.dumps(d)}</script></head><body><h1>{title}</h1></body></html>'


def listing(links, next_href=None):
    items = "".join(f'<li><a href="{h}">{t}</a></li>' for h, t in links)
    nxt = f'<a href="{next_href}">Next</a>' if next_href else ""
    return (f'<html><body><nav><a href="{SITE}/careers/teams">Our teams</a><a href="/privacy">Privacy policy</a></nav>'
            f'<ul>{items}</ul>{nxt}</body></html>')


PAGES = {
    f"{SITE}/careers": listing([(f"{SITE}/careers/backend-engineer-101", "Backend Engineer"),
                                (f"{SITE}/careers/backend-intern-102", "Backend Engineer Intern"),
                                (f"{SITE}/careers/backend-lead-103", "Backend Engineering Lead"),
                                (f"{SITE}/careers/office-manager-104", "Office Manager"),
                                (f"{SITE}/careers/private/backend-engineer-105", "Backend Engineer (Private)")],
                               next_href="/careers?page=2"),
    f"{SITE}/careers?page=2": listing([(f"{SITE}/careers/platform-engineer-106", "Backend Platform Engineer"),
                                       (f"{SITE}/careers/backend-gone-107", "Backend Engineer, Payments")]),
    f"{SITE}/careers/backend-engineer-101": ld("Backend Engineer"),
    f"{SITE}/careers/backend-intern-102": ld("Backend Engineer Intern", etype="INTERN", remote=True, locality="Remote"),
    f"{SITE}/careers/backend-lead-103": ld("Backend Engineering Lead", valid="2020-01-31"),
    f"{SITE}/careers/office-manager-104": ld("Office Manager"),
    f"{SITE}/careers/private/backend-engineer-105": ld("Backend Engineer (Private)"),
    f"{SITE}/careers/platform-engineer-106": (
        '<html><head><meta property="og:site_name" content="Acme"></head><body><h1>Backend Platform Engineer</h1>'
        f'<div class="job-description">{JD}</div></body></html>'),
    "https://www.linkedin.com/jobs/search/": "<html><head><title>Security Verification</title></head>"
                                             "<body>Please verify you are human</body></html>",
}
ROBOTS = "User-agent: *\nDisallow: /careers/private/\n"


class Site:
    """Serves PAGES to a real Chromium and counts every request."""
    def __init__(self):
        self.hits: Counter = Counter()

    async def _route(self, route):
        url = route.request.url
        key = url if url in PAGES else url.split("?")[0] if url.split("?")[0].endswith("/jobs/search/") else url
        self.hits[url] += 1
        if key in PAGES:
            await route.fulfill(status=200, content_type="text/html", body=PAGES[key])
        else:
            await route.fulfill(status=404, content_type="text/html", body="<html><body>Not found</body></html>")

    @asynccontextmanager
    async def browser(self, profile, headless):
        pw = await async_playwright().start()
        b = await pw.chromium.launch(headless=True)
        ctx = await b.new_context()
        await ctx.route("**/*", self._route)
        try:
            yield ctx
        finally:
            await ctx.close(); await b.close(); await pw.stop()

    def http(self):
        def h(req):
            if req.url.path == "/robots.txt":
                return httpx.Response(200, text=ROBOTS)
            return httpx.Response(404)
        return httpx.AsyncClient(transport=httpx.MockTransport(h))

    def detail_hits(self):
        return {u: n for u, n in self.hits.items() if "/careers/" in u and "page=" not in u}


def make_deps(SL, site, llm=None, logs=None):
    logs = logs if logs is not None else []
    return Deps(store=Store(SL), browser=site.browser, http=site.http, llm=lambda: llm,
                log=lambda m, level="info", job_id="": logs.append((level, m)))


SPEC = SearchSpec(role_titles=["Backend Engineer"], locations=["Kochi", "Remote"], watchlist_urls=[f"{SITE}/careers"],
                  sources=["watchlist"], delay_min_s=0, delay_max_s=0, max_pages=3, max_details=10, llm_review=False)


async def test_careers_site_end_to_end_and_second_run_reads_nothing_twice(migrated):
    _, SL = migrated
    site = Site()
    res = await service.run_search(SPEC, deps=make_deps(SL, site))
    st = res["stats"]
    with SL() as s:
        jobs = {j.title: j for j in s.scalars(select(Job))}
        seen = dict(s.execute(select(SeenUrl.canonical_url, SeenUrl.reason)).all())
    assert set(jobs) == {"Backend Engineer", "Backend Engineer Intern", "Backend Platform Engineer"}
    intern = jobs["Backend Engineer Intern"]
    assert intern.job_type == "internship" and intern.is_remote and intern.jd_source == "json-ld"
    assert "• APIs" in intern.jd_text and len(intern.jd_text) > 400          # full description saved, structured
    assert jobs["Backend Engineer"].job_type == "job" and jobs["Backend Engineer"].company == "Acme"
    page = jobs["Backend Platform Engineer"]
    assert page.jd_source in ("page", "selector") and page.company == "Acme" and page.status == "scraped"
    assert seen == {"https://careers.acme.test/careers/backend-lead-103": "expired",
                    "https://careers.acme.test/careers/backend-gone-107": "gone"}
    assert st["new"] == 3 and st["new_internships"] == 1 and st["new_jobs"] == 2
    assert st["sources"]["careers"]["skipped_title"] == 1                 # Office Manager never opened
    assert st["sources"]["careers"]["robots_disallowed"] == 1             # /careers/private/ never opened
    hits = site.detail_hits()
    assert f"{SITE}/careers/office-manager-104" not in hits and f"{SITE}/careers/private/backend-engineer-105" not in hits
    assert site.hits[f"{SITE}/careers?page=2"] == 1                       # followed the "Next" link

    site2 = Site()
    res2 = await service.run_search(SPEC, deps=make_deps(SL, site2))
    assert res2["new_job_ids"] == [] and res2["stats"]["known"] == 3
    assert res2["stats"]["sources"]["careers"]["skipped_rejected_before"] == 2
    assert site2.detail_hits() == {}                                      # nothing already known is re-read
    with SL() as s:
        assert sorted(s.scalars(select(Job.times_seen))) == [2, 2, 2]
    assert [r["status"] for r in Store(SL).list_runs()] == ["done", "done"]


async def test_blocked_board_stops_that_source_only(migrated):
    _, SL = migrated
    site, logs = Site(), []
    spec = SPEC.model_copy(update={"sources": ["watchlist", "linkedin"]})
    res = await service.run_search(spec, deps=make_deps(SL, site, logs=logs))
    assert res["stats"]["blocked"] == {"linkedin": "bot check"}
    assert res["stats"]["new"] == 3                                        # the careers site still delivered
    linkedin_hits = [u for u in site.hits if "linkedin.com" in u]
    assert len(linkedin_hits) == 1                                         # stopped at the first block
    assert any("linkedin" in m and "bot check" in m for _, m in logs)


async def test_llm_review_checks_unstructured_pages(migrated, monkeypatch):
    _, SL = migrated
    seen_prompts = []

    async def fake(llm, schema, messages, **kw):
        seen_prompts.append(messages[1]["content"])
        return PageReview(is_job_posting=False, reason="careers landing page")
    monkeypatch.setattr("llm.structured.complete_json", fake)
    spec = SPEC.model_copy(update={"llm_review": True})
    res = await service.run_search(spec, deps=make_deps(SL, Site(), llm=object()))
    assert len(seen_prompts) == 1 and "Backend Platform Engineer" in seen_prompts[0]   # JSON-LD pages are not re-checked
    assert res["stats"]["rejected"]["not_a_posting"] == 1 and res["stats"]["new"] == 2
    with SL() as s:
        assert s.get(SeenUrl, f"{SITE}/careers/platform-engineer-106").reason == "not_a_posting"


async def test_failed_run_resumes_from_checkpoint_without_browsing_again(migrated, monkeypatch, pg_server_url):
    eng, SL = migrated
    if eng.dialect.name == "postgresql":     # real AsyncPostgresSaver in the test's own schema
        from sqlalchemy.engine import make_url
        conn = (make_url(pg_server_url).set(drivername="postgresql")
                .update_query_dict({"options": f"-csearch_path={eng.info_schema}"}).render_as_string(hide_password=False))
        monkeypatch.setattr(service, "checkpoint_conn_string", lambda: conn)
        monkeypatch.setattr(service, "_pg_setup_done", False)
    site = Site()
    deps = make_deps(SL, site)
    real = deps.store.persist_run
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database went away")
        return real(*a, **k)
    deps.store.persist_run = flaky
    with pytest.raises(RuntimeError):
        await service.run_search(SPEC, run_id="resume-me", deps=deps)
    assert Store(SL).get_run("resume-me")["status"] == "failed"
    hits_before = dict(site.hits)

    res = await service.resume_run("resume-me", deps=deps)
    assert res["stats"]["new"] == 3 and Store(SL).get_run("resume-me")["status"] == "done"
    assert dict(site.hits) == hits_before                                 # collect/fetch were not repeated
    assert (await service.resume_run("resume-me", deps=deps))["stats"]["new"] == 3   # finished: no-op


async def test_run_lock_rejects_concurrent_runs(migrated):
    _, SL = migrated
    await service._lock.acquire()
    try:
        with pytest.raises(service.RunInProgress):
            await service.run_search(SPEC, deps=make_deps(SL, Site()))
    finally:
        service._lock.release()


async def test_nothing_to_search_finishes_cleanly(migrated):
    _, SL = migrated
    logs = []
    res = await service.run_search(SearchSpec(sources=["linkedin"]), deps=make_deps(SL, Site(), logs=logs))
    assert res["stats"]["new"] == 0 and any("nothing to search" in m for _, m in logs)


def test_graph_shape():
    g = build_graph(Deps(store=Store())).compile()
    assert set(g.get_graph().nodes) >= {"plan", "collect", "dedupe", "fetch", "screen", "review", "persist", "finish"}
