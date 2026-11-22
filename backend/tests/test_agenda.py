"""Follow-up agenda, daily digest (once a day, after the hour) and automatic "No response"."""
import json
from datetime import date, datetime, timedelta
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import agenda
import db
import notify
from config.settings import get_settings
from db import Job, utcnow

TODAY = date(2026, 11, 19)
NOW = datetime(2026, 11, 19, 6, 0)


@pytest.fixture
def SL(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}"); db.migrate(eng)
    yield sessionmaker(eng, expire_on_commit=False)
    eng.dispose()


def add(SL, jid, **kw):
    with SL() as s:
        s.add(Job(id=jid, source="careers", company=kw.pop("company", "Acme"), title=kw.pop("title", jid), url=f"https://x/{jid}",
                  canonical_url=f"https://x/{jid}", dedupe_key=f"acme|{jid}", **kw)); s.commit()


@pytest.fixture
def jobs(SL):
    add(SL, "overdue", application_status="interviewing", next_step="Onsite", next_step_date=TODAY - timedelta(days=1))
    add(SL, "today", application_status="assessment", next_step="Take-home due", next_step_date=TODAY)
    add(SL, "later", application_status="interviewing", next_step_date=TODAY + timedelta(days=12))
    add(SL, "rejected", application_status="rejected", next_step_date=TODAY)
    add(SL, "stale", application_status="applied", applied_at=NOW - timedelta(days=10))
    add(SL, "step_overdue", application_status="assessment", applied_at=NOW - timedelta(days=20),
        next_step="Take-home", next_step_date=TODAY - timedelta(days=2))      # listed once: as a next step, not a follow-up
    add(SL, "long_overdue", application_status="assessment", applied_at=NOW - timedelta(days=40),
        next_step="Call", next_step_date=TODAY - timedelta(days=30))        # stale step: a follow-up now
    add(SL, "recent", application_status="applied", applied_at=NOW - timedelta(days=3))
    add(SL, "planned", application_status="applied", applied_at=NOW - timedelta(days=12), next_step_date=TODAY + timedelta(days=2))
    add(SL, "closing", status="tailored", valid_through=NOW + timedelta(days=3))
    add(SL, "closing_later", status="scored", valid_through=NOW + timedelta(days=12))
    add(SL, "review", status="needs_review")
    return SL


def test_agenda_lists_what_needs_you(jobs):
    a = agenda.build(jobs, now=NOW, today=TODAY)
    assert [x["id"] for x in a["next_steps"]] == ["step_overdue", "overdue", "today", "planned"]   # by date; "later" > 7 days
    assert {x["id"]: x["when"] for x in a["next_steps"]}["today"] == "today"
    assert [(x["id"], x["days"]) for x in a["follow_up"]] == [("long_overdue", 40), ("stale", 10)]   # not "planned": booked
    assert [(x["id"], x["days"]) for x in a["apply_by"]] == [("closing", 3)]
    assert a["review"] == 1
    text = agenda.digest_text(a)
    assert "2026-11-18 (overdue) Onsite — overdue at Acme" in text and "stale at Acme — applied 10 days ago" in text
    assert "closing at Acme — closes 2026-11-22" in text and "1 filled form(s) waiting" in text
    assert agenda.digest_text({"next_steps": [], "follow_up": [], "apply_by": [], "review": 0, "questions": 0}) == ""


def test_auto_no_response_skips_jobs_with_plans(jobs):
    assert agenda.auto_ghost(9, jobs, now=NOW, today=TODAY) == 1
    with jobs() as s:
        assert s.get(Job, "stale").application_status == "ghosted" and s.get(Job, "stale").notion_dirty
        assert s.get(Job, "planned").application_status == "applied" and s.get(Job, "recent").application_status == "applied"


async def test_digest_once_a_day_after_the_hour(jobs, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: sent.append(json.loads(r.content)) or httpx.Response(200))))
    monkeypatch.setattr(notify, "_recent", {})
    st = get_settings().model_copy(update={"notify_webhook_url": "https://ntfy.sh/seeko", "digest_hour": 8,
                                           "notify_events": ["digest"], "auto_ghost_days": 30})
    monkeypatch.setattr(agenda, "get_settings", lambda: st); monkeypatch.setattr(notify, "get_settings", lambda: st)
    monkeypatch.setattr(agenda, "log", lambda *a, **k: None)
    assert await agenda.tick(jobs, datetime(2026, 11, 19, 7, 30)) == {"ghosted": 0}      # before the hour: no digest
    assert await agenda.tick(jobs, datetime(2026, 11, 19, 8, 5)) == {"digest": True}
    assert await agenda.tick(jobs, datetime(2026, 11, 19, 20, 0)) == {}                  # once a day
    assert len(sent) == 1 and sent[0]["topic"] == "seeko" and "Follow up" in sent[0]["message"]
    assert (await agenda.tick(jobs, datetime(2026, 11, 20, 9, 0))).get("digest") is True   # next day again
    assert db.get_state("digest_on", session_factory=jobs) == "2026-11-20"


def test_agenda_endpoint():
    from main import app
    with TestClient(app) as c:
        a = c.get("/api/agenda").json()
        assert {"next_steps", "follow_up", "apply_by", "review", "questions"} <= set(a)
        assert c.post("/api/agenda/digest").status_code == 400                           # no webhook configured
