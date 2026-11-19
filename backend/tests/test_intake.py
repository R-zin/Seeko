"""Adding one job by link (real headless Chromium on the fixture careers site), bookmarklet capture, or paste."""
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from db import Job
from jobsearch import intake
from jobsearch.graph import Deps
from jobsearch.intake import IntakeError
from jobsearch.store import Store
from tests.test_jobsearch_graph import JD, SITE, Site, ld

LD = json.dumps({"@context": "https://schema.org", "@type": "JobPosting", "title": "Data Analyst Intern",
                 "description": "<p>" + "Analyse product data with SQL and Python. " * 12 + "</p>",
                 "hiringOrganization": {"name": "Globex"}, "employmentType": "INTERN", "validThrough": "2099-01-31",
                 "jobLocation": {"address": {"addressLocality": "Bengaluru", "addressCountry": "India"}}})


def deps(SL, site=None):
    return Deps(store=Store(SL), **({"browser": site.browser, "http": site.http} if site else {}))


def test_source_and_canonical_link():
    assert intake.source_for("https://www.linkedin.com/jobs/view/3912345678/?trk=abc")[0] == "linkedin"
    assert intake.source_for("https://boards.greenhouse.io/acme/jobs/123?gh_src=x")[0] == "greenhouse"
    assert intake.source_for("https://jobs.lever.co/acme/1f2e")[0] == "lever"
    assert intake.source_for("https://careers.acme.test/jobs/9")[:1] == ("careers",)


def test_capture_prefers_json_ld_then_page_text():
    p = intake.from_capture({"url": "https://www.linkedin.com/jobs/view/1/", "title": "x | LinkedIn", "jsonld": [LD],
                             "text": "nav nav nav"})
    assert (p["title"], p["company"], p["jd_source"]) == ("Data Analyst Intern", "Globex", "json-ld")
    p = intake.from_capture({"url": "https://careers.acme.test/j/2", "title": "Backend Engineer - Acme | Careers",
                             "h1": "Backend Engineer", "text": "You will build APIs. " * 30})
    assert p["title"] == "Backend Engineer" and p["company"] == "Acme" and p["jd_text"].startswith("You will build")
    with pytest.raises(IntakeError):
        intake.from_capture({"url": "https://x.test/a", "text": ""})
    with pytest.raises(IntakeError, match="web link"):
        intake.from_capture({"url": "javascript:alert(1)", "text": "x" * 300})


async def test_add_from_capture_dedupes_and_marks_applied(migrated):
    _, SL = migrated
    cap = {"url": "https://www.linkedin.com/jobs/view/42/?refId=9", "title": "x", "jsonld": [LD], "text": ""}
    prev = await intake.add_job(capture=cap, preview=True, deps=deps(SL))
    assert prev["preview"] and prev["job"]["job_type"] == "internship" and prev["job"]["source"] == "linkedin"
    with SL() as s: assert s.scalar(select(Job.id)) is None                      # a preview stores nothing
    res = await intake.add_job(capture=cap, deps=deps(SL))
    assert res["created"]
    with SL() as s:
        j = s.get(Job, res["job_id"])
        assert (j.origin, j.status, j.application_status, j.company) == ("user", "scraped", "not_applied", "Globex")
        assert j.valid_through.year == 2099 and j.notion_dirty
    again = await intake.add_job(capture={**cap, "url": "https://www.linkedin.com/jobs/view/42/"}, mode="applied", deps=deps(SL))
    assert again == {"job_id": res["job_id"], "created": False, "preview": False}
    with SL() as s:
        j = s.get(Job, res["job_id"])
        assert (j.status, j.application_status) == ("tracked", "applied") and j.applied_at is not None


async def test_paste_without_link_can_only_be_tracked(migrated):
    _, SL = migrated
    with pytest.raises(IntakeError, match="link"):
        await intake.add_job(title="Platform Engineer", company="Initech", description="Run Kubernetes.", deps=deps(SL))
    res = await intake.add_job(title="Platform Engineer", company="Initech", description="Run Kubernetes.",
                               mode="applied", deps=deps(SL))
    with SL() as s:
        j = s.get(Job, res["job_id"])
        assert j.canonical_url == "manual:initech|platform engineer" and j.status == "tracked" and j.source == "manual"


async def test_link_is_read_carefully_with_a_real_browser(migrated):
    _, SL = migrated
    site = Site()
    res = await intake.add_job(url=f"{SITE}/careers/backend-engineer-101", deps=deps(SL, site))
    with SL() as s:
        j = s.get(Job, res["job_id"])
        assert (j.title, j.company, j.source, j.jd_source) == ("Backend Engineer", "Acme", "careers", "json-ld")
        assert "PostgreSQL" in j.jd_text
    assert sum(site.hits.values()) == 1                                         # one page load
    with pytest.raises(IntakeError, match="robots.txt"):                         # careers pages: robots.txt is obeyed
        await intake.add_job(url=f"{SITE}/careers/private/backend-engineer-105", deps=deps(SL, site))
    with pytest.raises(IntakeError, match="bookmarklet"):                       # a bot check stops it, never bypassed
        await intake.add_job(url="https://www.linkedin.com/jobs/search/?keywords=x", deps=deps(SL, site))


async def test_user_added_jobs_are_never_skipped_for_their_score(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import db
    from schemas import Score
    from workers import pipeline as P
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}"); db.migrate(eng)
    SL = sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(P, "SessionLocal", SL); monkeypatch.setattr(P, "log", lambda *a, **k: None)
    monkeypatch.setattr(P, "load_resume", lambda: ""); monkeypatch.setattr(P, "ARTIFACTS_DIR", tmp_path)

    async def low(*a, **k): return Score(score=12)
    class T: summary, fell_back = "tailored", False
    async def tailor(*a, **k): return T()
    async def apply(job_id, jd): pass
    monkeypatch.setattr(P, "score_job", low); monkeypatch.setattr(P, "tailor_resume", tailor); monkeypatch.setattr(P, "apply_job", apply)
    with SL() as s:
        for jid, origin in (("mine", "user"), ("found", "search")):
            s.add(Job(id=jid, source="careers", company="A", title=jid, url=f"https://x/{jid}", canonical_url=f"https://x/{jid}",
                      dedupe_key=jid, origin=origin))
        s.commit()
    await P.process_job("mine"); await P.process_job("found")
    with SL() as s:
        assert s.get(Job, "mine").status == "tailored" and s.get(Job, "found").status == "skipped"


def test_add_endpoint():
    from main import app
    with TestClient(app) as c:
        r = c.post("/api/jobs/add", json={"title": "Site Reliability Engineer", "company": "Umbrella",
                                          "description": "On-call for Kubernetes.", "mode": "applied"})
        assert r.status_code == 200 and r.json()["created"] and r.json()["job"]["status"] == "tracked"
        assert r.json()["job"]["application_status"] == "applied"
        bad = c.post("/api/jobs/add", json={"title": "SRE", "company": "Umbrella", "description": "x"})
        assert bad.status_code == 422 and "link" in bad.json()["detail"]
        assert c.post("/api/jobs/add", json={}).status_code == 422


async def test_bookmarklet_reads_the_page_you_are_on():
    """Run the real bookmarklet in Chromium on a job page; what it sends must parse into the posting."""
    from urllib.parse import unquote
    from playwright.async_api import async_playwright
    href = intake.bookmarklet("http://127.0.0.1:5173")
    assert href.startswith("javascript:") and " " not in href
    page_html = ld("Backend Engineer").replace("<body>", '<body><nav>Home Jobs About</nav><div class="job-description">'
                                               + "Design services in Python. " * 20 + "</div>")
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        await pg.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=page_html))
        await pg.goto(f"{SITE}/careers/backend-engineer-101?utm_source=x")
        await pg.evaluate("() => { window.__opened = []; window.open = (u) => window.__opened.push(u) }")
        await pg.evaluate(unquote(href[len("javascript:"):]))
        opened = await pg.evaluate("() => window.__opened")
        await b.close()
    assert len(opened) == 1 and opened[0].startswith("http://127.0.0.1:5173/#add=")
    cap = json.loads(unquote(opened[0].split("#add=", 1)[1]))
    assert cap["h1"] == "Backend Engineer" and cap["text"].startswith("Design services in Python")
    p = intake.from_capture(cap)
    assert (p["title"], p["company"], p["jd_source"]) == ("Backend Engineer", "Acme", "json-ld")
    with pytest.raises(IntakeError):
        intake.bookmarklet("javascript:alert(1)")
