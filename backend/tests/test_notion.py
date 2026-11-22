"""Notion: MCP client, tracker schema, live push, and two-way pull - against a fake MCP server over stdio."""
import json
import os
import sys
from datetime import date
from pathlib import Path
import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
import db
from config.settings import get_settings
from db import Job
from mcp_notion.client import NotionError, NotionMCP
from mcp_notion.schema import TEXT_LIMIT, check, description_blocks, encode_properties, setup_patch
from workers import notion_service as NS

FAKE = str(Path(__file__).parent / "fake_notion_server.py")


def fake(schema: str = "legacy") -> NotionMCP:
    return NotionMCP("tok", command=sys.executable, args=[FAKE, "--schema", schema])


@pytest.fixture
def SL(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    db.migrate(eng)
    yield sessionmaker(eng, expire_on_commit=False)
    eng.dispose()


@pytest.fixture
def logs(monkeypatch):
    out = []
    monkeypatch.setattr(NS, "log", lambda msg, level="info", job_id="": out.append((level, msg)))
    return out


@pytest.fixture
def settings(monkeypatch):
    st = get_settings().model_copy(update={"notion_token": "tok", "notion_database_id": "db1", "notion_pull_interval_s": 0})
    monkeypatch.setattr(NS, "get_settings", lambda: st)
    return st


@pytest.fixture
async def sync(SL, settings, logs):
    made = []

    def build(schema: str = "legacy"):
        s = NS.NotionSync(session_factory=SL, client_factory=lambda tok: fake(schema), pace_s=0, debounce_s=0)
        made.append(s); return s
    yield build
    for s in made: await s.close()


def add_job(SL, jid: str, **kw):
    with SL() as s:
        s.add(Job(id=jid, source="greenhouse", company="Acme", title=kw.pop("title", f"Engineer {jid}"), url=f"https://x/{jid}",
                  canonical_url=f"https://x/{jid}", dedupe_key=f"acme|{jid}", **kw))
        s.commit()


def job(SL, jid: str) -> Job:
    with SL() as s: return s.get(Job, jid)


async def dump(s: NS.NotionSync) -> dict:
    return await s.client.call("fake-dump", {})


def text_of(prop: dict) -> str:
    return "".join(x["text"]["content"] for x in next(iter(prop.values())))


# ── client & schema ──────────────────────────────────────────────────────────────────────────────────────────────
async def test_client_raises_on_notion_error_payload():
    async with fake() as client:
        assert "API-query-data-source" in client.tools
        with pytest.raises(NotionError) as e:   # the real server returns HTTP errors as a normal text result
            await client.call("API-patch-page", {"page_id": "nope", "properties": {}})
        assert e.value.status == 404 and e.value.gone and not e.value.transient


def test_schema_check_maps_by_type_and_skips_what_cannot_be_written():
    r = check({"Name": {"type": "title"}, "Status": {"type": "status", "status": {"options": [{"name": "Applied"}]}},
               "Source": {"type": "rich_text"}, "Notes": {"type": "number"}})
    assert r.ok and r.title_property == "Name" and r.wrong_type[0]["property"] == "Notes" and "Company" in r.missing
    props = encode_properties({"title": "Eng", "status": "Applied", "source": "lever", "notes": "x"}, r)
    assert props == {"Name": {"title": [{"type": "text", "text": {"content": "Eng"}}]}, "Status": {"status": {"name": "Applied"}},
                     "Source": {"rich_text": [{"type": "text", "text": {"content": "lever"}}]}}
    # a status option the database does not have is not sent (Notion would reject the whole page)
    assert "Status" not in encode_properties({"title": "Eng", "status": "Tailoring"}, r)
    patch = setup_patch(r)
    assert "Notes" not in patch and patch["Application Status"]["select"]["options"][3]["name"] == "Interviewing"


def test_long_text_and_descriptions_respect_notion_limits():
    r = check({"Role": {"type": "title"}, "Notes": {"type": "rich_text"}})
    notes = encode_properties({"title": "x", "notes": "n" * (TEXT_LIMIT * 2 + 5)}, r)["Notes"]["rich_text"]
    assert [len(x["text"]["content"]) for x in notes] == [TEXT_LIMIT, TEXT_LIMIT, 5]
    blocks = description_blocks("About us\n\n- Python\n• SQL\nWe ship.")
    assert [b["type"] for b in blocks] == ["heading_2", "paragraph", "bulleted_list_item", "bulleted_list_item", "paragraph"]
    long = description_blocks("\n".join(f"Line {i} " + "w" * 60 for i in range(400)))
    assert len(long) < 40 and all(len(x["text"]["content"]) <= TEXT_LIMIT for b in long for x in b[b["type"]]["rich_text"])
    assert description_blocks("  ") == []


async def test_connect_reports_schema_and_setup_adds_only_missing(sync):
    s = sync("legacy")
    r = await s.connect()
    assert r["ok"] and r["data_source_id"] == "ds1" and "Applied Date" in r["missing"] and "Role" in r["mapped"]
    res = await s.setup()
    assert res["missing"] == [] and res["wrong_type"] == [] and "Application Status" in res["added"]
    assert "Status" not in res["added"] and set(res["two_way"]) == {"Application Status", "Applied Date", "Next Step",
                                                                     "Next Step Date", "Notes"}
    props = (await dump(s))["props"]
    assert props["Status"] == {"type": "select"}   # existing property untouched
    assert [o["name"] for o in props["Application Status"]["select"]["options"]][:2] == ["Not applied", "Applied"]


# ── push ─────────────────────────────────────────────────────────────────────────────────────────────────────────
async def test_push_creates_page_with_description_and_updates_only_on_change(sync, SL):
    s = sync(); await s.connect(); await s.setup()
    add_job(SL, "j1", jd_text="About us\n- Python\n- SQL", score=81.0, location="Kochi", job_type="internship",
            next_step_date=date(2026, 12, 1))
    assert await s.drain() == 1
    d = await dump(s)
    page = d["pages"]["page-1"]; p = page["properties"]
    assert text_of(p["Role"]) == "Engineer j1" and text_of(p["Seeko ID"]) == "j1" and p["Match Score"] == {"number": 81.0}
    assert p["Application Status"] == {"select": {"name": "Not applied"}} and p["Job Type"] == {"select": {"name": "Internship"}}
    assert p["Status"] == {"select": {"name": "Scraped"}} and p["Next Step Date"] == {"date": {"start": "2026-12-01"}}
    assert p["Discovered"]["date"]["start"] == job(SL, "j1").discovered_at.date().isoformat()
    assert [b["type"] for b in page["children"]] == ["heading_2", "paragraph", "bulleted_list_item", "bulleted_list_item"]
    j = job(SL, "j1")
    assert j.notion_page_id == "page-1" and not j.notion_dirty and j.notion_base["notes"] == "" and j.notion_base["body"]

    calls = len(d["calls"])
    assert await s.push("j1") == "unchanged" and len((await dump(s))["calls"]) == calls   # nothing to send

    with SL() as ses:                       # any ORM change to a shown column marks the job dirty
        ses.get(Job, "j1").status = "tailored"; ses.commit()
    assert job(SL, "j1").notion_dirty
    assert await s.drain() == 1
    d = await dump(s)
    assert d["pages"]["page-1"]["properties"]["Status"] == {"select": {"name": "Tailored"}}
    assert d["calls"][calls:] == ["patch-page"] and len(d["pages"]) == 1


async def test_bare_database_gets_title_only_until_set_up(sync, SL):
    s = sync("bare"); r = await s.connect()
    assert r["title_property"] == "Name" and "Company" in r["missing"]
    add_job(SL, "j1")
    assert await s.drain() == 1
    assert list((await dump(s))["pages"]["page-1"]["properties"]) == ["Name"]
    await s.setup()          # adds the properties and re-pushes every job
    await s.drain()
    p = (await dump(s))["pages"]["page-1"]["properties"]
    assert text_of(p["Company"]) == "Acme" and text_of(p["Seeko ID"]) == "j1"


async def test_lost_page_id_never_creates_a_duplicate(sync, SL):
    s = sync(); await s.connect(); await s.setup()
    await s.client.call("fake-add-page", {"properties": {"Role": {"title": [{"type": "text", "text": {"content": "Engineer j1"}}]},
                                                         "Seeko ID": {"rich_text": [{"type": "text", "text": {"content": "j1"}}]}}})
    add_job(SL, "j1")
    await s.drain()
    d = await dump(s)
    assert job(SL, "j1").notion_page_id == "page-1" and "post-page" not in d["calls"] and len(d["pages"]) == 1


async def test_page_deleted_in_notion_is_not_recreated_until_resync(sync, SL, logs):
    s = sync(); await s.connect(); await s.setup()
    add_job(SL, "j1"); await s.drain()
    await s.client.call("fake-edit-page", {"page_id": "page-1", "in_trash": True})
    with SL() as ses: ses.get(Job, "j1").title = "Renamed"; ses.commit()
    await s.drain()
    j = job(SL, "j1")
    assert j.notion_base["gone"] and not j.notion_dirty and len((await dump(s))["pages"]) == 1
    assert any("deleted in Notion" in m for _, m in logs)
    s.resync(); await s.drain()
    assert job(SL, "j1").notion_page_id == "page-2" and not job(SL, "j1").notion_base.get("gone")


async def test_a_rejected_job_backs_off_alone(sync, SL, logs):
    s = sync(); await s.connect(); await s.setup()
    add_job(SL, "bad", title="reject me"); add_job(SL, "ok")
    await s.drain()
    bad, ok = job(SL, "bad"), job(SL, "ok")
    assert ok.notion_page_id and not ok.notion_dirty
    assert bad.notion_dirty and bad.notion_attempts == 1 and bad.notion_retry_at and "rejected title" in bad.notion_error
    assert s.pending_ids() == []                     # waits for its retry time
    assert sum("Notion sync failed" in m for _, m in logs) == 1


async def test_skipped_jobs_get_no_new_page_but_existing_pages_follow(sync, SL):
    s = sync(); await s.connect(); await s.setup()
    add_job(SL, "low", status="skipped"); add_job(SL, "j1")
    await s.drain()
    assert job(SL, "low").notion_page_id == "" and not job(SL, "low").notion_dirty
    with SL() as ses: ses.get(Job, "j1").status = "skipped"; ses.commit()
    await s.drain()
    assert (await dump(s))["pages"]["page-1"]["properties"]["Status"] == {"select": {"name": "Skipped"}}


# ── dirty marking ────────────────────────────────────────────────────────────────────────────────────────────────
def test_shown_columns_mark_dirty_and_bookkeeping_does_not(SL):
    woke = []
    db.on_jobs_changed(lambda: woke.append(1))
    try:
        add_job(SL, "j1")
        assert woke                                    # a new job wakes the syncer
        with SL() as s:
            s.execute(update(Job).values(notion_dirty=False)); s.commit()
        n = len(woke)
        with SL() as s:
            j = s.get(Job, "j1"); j.notion_hash = "abc"; j.notion_synced_at = db.utcnow(); j.times_seen = 4; s.commit()
        assert not job(SL, "j1").notion_dirty and len(woke) == n
        with SL() as s:
            s.get(Job, "j1").notes = "call back"; s.commit()
        assert job(SL, "j1").notion_dirty and len(woke) == n + 1
    finally:
        db._change_listeners.clear()


def test_store_closures_mark_dirty(SL):
    from jobsearch.store import Store
    from tests.test_jobsearch_store import posting
    st = Store(SL)
    st.start_run("r1", {}, "test")
    ids = st.persist_run("r1", [posting(1), posting(2)], [], [])["new_job_ids"]
    with SL() as s:
        s.execute(update(Job).values(notion_dirty=False)); s.commit()
    st.start_run("r2", {}, "test")
    st.persist_run("r2", [], [], [], [{"source": "greenhouse", "company": "Acme", "urls": ["https://boards.greenhouse.io/acme/jobs/1"]}])
    with SL() as s:
        dirty = {j.id: j.notion_dirty for j in s.scalars(select(Job))}
    assert sorted(dirty.values()) == [False, True] and dirty[ids[1]]


# ── pull ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_three_way_merge_rules():
    assert NS.merge(None, "a", "b") == ("keep", "a")          # never synced: push establishes the base first
    assert NS.merge("a", "b", "a") == ("keep", "b")           # only Seeko changed: it gets pushed
    assert NS.merge("a", "a", "b") == ("adopt", "b")          # only Notion changed
    assert NS.merge("a", "b", "b") == ("converge", "b")
    assert NS.merge("a", "b", "c") == ("conflict", "b")       # both changed: Seeko wins


def rt(text: str) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": text}}]}


async def test_tracking_edits_in_notion_come_back(sync, SL, logs):
    s = sync(); await s.connect(); await s.setup()
    add_job(SL, "j1"); add_job(SL, "j2"); await s.drain()
    await s.client.call("fake-edit-page", {"page_id": "page-1", "properties": {
        "Application Status": {"select": {"name": "Interviewing"}}, "Notes": rt("Recruiter called"),
        "Next Step": rt("Onsite"), "Next Step Date": {"date": {"start": "2026-12-03"}}}})
    await s.client.call("fake-add-page", {"properties": {"Role": {"title": [{"type": "text", "text": {"content": "Mine"}}]}}})
    assert await s.pull() == 1
    j = job(SL, "j1")
    assert (j.application_status, j.notes, j.next_step, j.next_step_date) == ("interviewing", "Recruiter called", "Onsite", date(2026, 12, 3))
    assert j.applied_at is None and j.notion_base["application_status"] == "Interviewing"
    assert job(SL, "j2").application_status == "not_applied"
    assert await s.pull() == 0                        # nothing new since

    # Marking it applied in Notion without a date: Seeko dates it and pushes the date back
    await s.client.call("fake-edit-page", {"page_id": "page-1", "properties": {"Application Status": {"select": {"name": "Applied"}}}})
    await s.pull(); await s.drain()
    j = job(SL, "j1")
    assert j.application_status == "applied" and j.applied_at is not None and not j.notion_dirty
    assert (await dump(s))["pages"]["page-1"]["properties"]["Applied Date"]["date"]["start"] == j.applied_at.date().isoformat()

    # Both sides edit Notes before a sync: Seeko's value stays and is pushed
    with SL() as ses: ses.get(Job, "j1").notes = "mine"; ses.commit()
    await s.client.call("fake-edit-page", {"page_id": "page-1", "properties": {"Notes": rt("theirs")}})
    await s.pull(); await s.drain()
    assert job(SL, "j1").notes == "mine" and text_of((await dump(s))["pages"]["page-1"]["properties"]["Notes"]) == "mine"
    assert any("conflict" in m for _, m in logs)


async def test_custom_application_status_from_notion_is_kept(sync, SL):
    s = sync(); await s.connect(); await s.setup()
    add_job(SL, "j1"); await s.drain()
    await s.client.call("fake-edit-page", {"page_id": "page-1", "properties": {"Application Status": {"select": {"name": "Phone screen"}}}})
    await s.pull(); await s.drain()
    assert job(SL, "j1").application_status == "Phone screen"
    calls = (await dump(s))["calls"]
    assert calls[-1] != "post-page"


@pytest.mark.skipif(not os.environ.get("NOTION_TOKEN"), reason="needs NOTION_TOKEN + node")
async def test_live_connect():
    async with NotionMCP(os.environ["NOTION_TOKEN"]) as c: assert c.has_tool("query", "data-source")


async def test_unrelated_push_never_overwrites_a_notion_edit_not_yet_pulled(sync, SL):
    s = sync(); await s.connect(); await s.setup()
    add_job(SL, "j1"); await s.drain()
    await s.client.call("fake-edit-page", {"page_id": "page-1", "properties": {
        "Notes": rt("written in Notion"), "Application Status": {"select": {"name": "Offer"}}}})
    with SL() as ses: ses.get(Job, "j1").status = "tailored"; ses.commit()     # pushed before the next pull
    await s.drain()
    p = (await dump(s))["pages"]["page-1"]["properties"]
    assert p["Status"] == {"select": {"name": "Tailored"}}
    assert text_of(p["Notes"]) == "written in Notion" and p["Application Status"] == {"select": {"name": "Offer"}}
    await s.pull()
    j = job(SL, "j1")
    assert j.notes == "written in Notion" and j.application_status == "offer"
    # Seeko's own edits still go out
    with SL() as ses: ses.get(Job, "j1").next_step = "Negotiate"; ses.commit()
    await s.drain()
    p = (await dump(s))["pages"]["page-1"]["properties"]
    assert text_of(p["Next Step"]) == "Negotiate" and text_of(p["Notes"]) == "written in Notion"
