"""Notifications: shaped per destination, filtered by event, never repeated within minutes, never raising."""
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import db
import notify
from config.settings import get_settings
from db import Job


@pytest.fixture
def hook(monkeypatch):
    sent = []

    def handler(req):
        sent.append((str(req.url), json.loads(req.content)))
        return httpx.Response(500 if "broken" in str(req.url) else 200)
    monkeypatch.setattr(notify, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(notify, "_recent", {})
    state = {"s": get_settings().model_copy(update={"notify_webhook_url": "https://hooks.slack.com/services/T/B/x",
                                                    "notify_events": ["needs_review", "failed", "new_jobs"],
                                                    "app_url": "http://127.0.0.1:8000"})}
    monkeypatch.setattr(notify, "get_settings", lambda: state["s"])
    monkeypatch.setattr(db, "get_settings", lambda: state["s"])
    return sent, state


def test_payload_shapes():
    assert notify.kind_of("https://hooks.slack.com/services/a") == "slack"
    assert notify.kind_of("https://discord.com/api/webhooks/1/abc") == "discord"
    assert notify.kind_of("https://ntfy.sh/seeko-jobs") == "ntfy" and notify.kind_of("https://example.test/h") == "json"
    url, body = notify.request("https://ntfy.sh/seeko-jobs", "failed", "Failed: X", "boom", "http://app/#p", None)
    assert url == "https://ntfy.sh/" and body == {"topic": "seeko-jobs", "title": "Failed: X", "message": "boom",
                                                  "tags": ["failed"], "click": "http://app/#p"}
    _, body = notify.request("https://discord.com/api/webhooks/1/a", "x", "T", "t" * 3000, None, None)
    assert len(body["content"]) == 2000 and body["allowed_mentions"] == {"parse": []}
    _, body = notify.request("https://hooks.slack.com/x", "x", "T", "text", "http://a", None)
    assert body == {"text": "*T*\ntext\n<http://a|Open in Seeko>"}
    _, body = notify.request("https://example.test/h", "new_jobs", "T", "t", None, {"n": 1})
    assert body == {"event": "new_jobs", "title": "T", "text": "t", "url": None, "data": {"n": 1}}


async def test_send_filters_events_and_repeats(hook):
    sent, state = hook
    assert await notify.send("needs_review", "Review: A", "filled")
    assert not await notify.send("needs_review", "Review: A", "filled")          # same message within 10 minutes
    assert not await notify.send("applied", "Applied: A", "x")                   # event switched off
    assert await notify.send("applied", "Applied: A", "x", force=True)
    assert [b["text"].split("\n")[0] for _, b in sent] == ["*Review: A*", "*Applied: A*"]
    state["s"] = state["s"].model_copy(update={"notify_webhook_url": "https://broken.test/h"})
    assert not await notify.send("failed", "Failed: B", "x")                    # a refusing endpoint: False, no exception
    state["s"] = state["s"].model_copy(update={"notify_webhook_url": ""})
    assert not await notify.send("failed", "Failed: C", "x")


async def test_pipeline_failure_and_search_results_notify(hook, tmp_path, monkeypatch):
    sent, _ = hook
    from jobsearch.service import announce
    from workers import pipeline as P
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}"); db.migrate(eng)
    SL = sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(P, "SessionLocal", SL); monkeypatch.setattr(P, "log", lambda *a, **k: None)
    with SL() as s:
        for i, (t, rel) in enumerate((("Backend Engineer", 0.9), ("Data Intern", 0.5), ("SRE", 0.7))):
            s.add(Job(id=f"j{i}", source="careers", company="Acme", title=t, url=f"u{i}", canonical_url=f"u{i}",
                      dedupe_key=f"a|{i}", relevance=rel, job_type="internship" if "Intern" in t else "job"))
        s.commit()
    P._failed("j0", "Backend Engineer", ValueError("form has no submit button"))
    announce({"run_id": "r1", "new_job_ids": ["j0", "j1", "j2"]}, SL)
    import asyncio
    await asyncio.gather(*notify._tasks)
    texts = [b["text"] for _, b in sent]
    assert any(t.startswith("*Failed: Backend Engineer*\nform has no submit button") for t in texts)
    found = next(t for t in texts if t.startswith("*3 new jobs found*"))
    assert found.index("Backend Engineer") < found.index("SRE") < found.index("Data Intern — Acme (internship)")
    assert "http://127.0.0.1:8000/#tracker" in found


def test_settings_mask_and_test_endpoint():
    from main import app
    with TestClient(app) as c:
        assert c.post("/api/notify/test").status_code == 400                      # nothing configured
        assert c.put("/api/settings", json={"notify_webhook_url": "ftp://x"}).status_code == 422
        assert c.put("/api/settings", json={"notify_events": ["failed", "made_up"]}).status_code == 200
        s = c.get("/api/settings").json()
        assert s["notify_events"] == ["failed"] and s["notify_webhook_url"] == "" and s["digest_hour"] == 8
        assert {e["key"] for e in c.get("/api/notify/events").json()} >= {"needs_review", "digest"}
        c.put("/api/settings", json={"notify_events": ["needs_review", "applied", "failed", "new_jobs", "digest"]})
