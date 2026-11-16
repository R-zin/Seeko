import httpx, json
from scraper.dedupe import canonical_url, dedupe_key, save_if_new
from scraper.ats import detect_ats, fetch_watchlist
from scraper.scorer import score_job
from schemas import JobPosting, Score
import db


def test_canonical_url_strips_tracking():
    assert canonical_url("https://www.X.com/jobs/1/?utm_source=a&gh_src=b&id=7#frag") == "https://x.com/jobs/1?id=7"


def test_dedupe_key_normalizes():
    assert dedupe_key("Acme, Inc.", "Sr. Engineer") == dedupe_key("ACME", "sr engineer")


def test_save_if_new_dedupes_by_key_and_url(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    eng = create_engine(f"sqlite:///{tmp_path/'t.db'}"); db.Base.metadata.create_all(eng)
    monkeypatch.setattr("scraper.dedupe.SessionLocal", sessionmaker(eng, expire_on_commit=False))
    a = JobPosting(source="x", company="Acme Inc", title="Engineer", url="https://a.com/1?utm_source=z")
    assert save_if_new(a) is not None
    assert save_if_new(a.model_copy(update={"url": "https://b.com/other"})) is None  # same company+title
    assert save_if_new(a.model_copy(update={"title": "Other", "url": "https://a.com/1"})) is None  # same canonical url
    assert save_if_new(a.model_copy(update={"title": "Other", "url": "https://a.com/2"})) is not None


def test_detect_ats():
    assert detect_ats("https://boards.greenhouse.io/acme/jobs/1") == ("greenhouse", "acme")
    assert detect_ats("https://jobs.lever.co/acme") == ("lever", "acme")
    assert detect_ats("https://jobs.ashbyhq.com/acme") == ("ashby", "acme")
    assert detect_ats("https://acme.wd5.myworkdayjobs.com/en-US/Careers") == ("workday", "acme.wd5.myworkdayjobs.com|Careers")
    assert detect_ats("https://example.com") is None


async def test_fetch_watchlist_with_mock_transport():
    def h(req):
        if "greenhouse" in req.url.host:
            return httpx.Response(200, json={"jobs": [{"title": "SWE", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "location": {"name": "NYC"}, "content": "<p>Python</p>"}]})
        return httpx.Response(200, json=[{"text": "ML Eng", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {"location": "Remote"}, "descriptionPlain": "PyTorch"}])
    c = httpx.AsyncClient(transport=httpx.MockTransport(h))
    jobs = await fetch_watchlist(["https://boards.greenhouse.io/acme", "https://jobs.lever.co/acme"], c)
    assert [(j.source, j.title) for j in jobs] == [("greenhouse", "SWE"), ("lever", "ML Eng")]
    assert jobs[0].jd_text == "Python"


async def test_score_job(monkeypatch):
    async def fake(llm, schema, msgs, **kw): return Score(score=77, missing_keywords=["Kafka"])
    monkeypatch.setattr("scraper.scorer.complete_json", fake)
    s = await score_job("jd", "t", "c", resume="r", llm=object())
    assert s.score == 77 and s.missing_keywords == ["Kafka"]


async def test_workday_paginates_with_page_size_20_and_retries_429(monkeypatch):
    import asyncio as aio
    from scraper import ats
    async def nosleep(_): pass
    monkeypatch.setattr(ats.asyncio, "sleep", nosleep)
    seen = {"limits": [], "429": 0}
    def h(req):
        if req.method == "POST":
            body = json.loads(req.content); seen["limits"].append(body["limit"])
            if body["offset"] == 0 and seen["429"] == 0: seen["429"] += 1; return httpx.Response(429)
            n = 20 if body["offset"] < 40 else 0
            return httpx.Response(200, json={"jobPostings": [{"title": f"J{body['offset']+i}", "externalPath": f"/job/x/{body['offset']+i}", "locationsText": "NYC"} for i in range(n)]})
        return httpx.Response(200, json={"jobPostingInfo": {"jobDescription": "<p>desc</p>"}})
    c = httpx.AsyncClient(transport=httpx.MockTransport(h))
    jobs = await ats.fetch_workday(c, "acme.wd5.myworkdayjobs.com|Careers", limit=30)
    assert len(jobs) == 30 and set(seen["limits"]) == {20} and seen["429"] == 1 and jobs[0].jd_text == "desc"
