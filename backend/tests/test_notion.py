import sys, os, pytest
from pathlib import Path
from mcp_notion.client import NotionMCP
from mcp_notion.schema import discover
from mcp_notion.sync import upsert_job

FAKE = str(Path(__file__).parent / "fake_notion_server.py")


def fake(): return NotionMCP("tok", command=sys.executable, args=[FAKE])


async def test_connect_and_discover_schema():
    async with fake() as client:
        assert "API-retrieve-a-database" in client.tools
        r = await discover(client, "db1")
        assert not r["ok"] and r["missing"] == ["Applied Date"]


async def test_create_then_update_page():
    async with fake() as client:
        job = dict(title="Eng", company="Co", source="greenhouse", url="https://x", status_label="Scraped", score=82)
        pid = await upsert_job(client, "db1", job)
        pid2 = await upsert_job(client, "db1", {**job, "page_id": pid, "status_label": "Applied", "applied_at": "2026-11-16"})
        assert pid == pid2


@pytest.mark.skipif(not os.environ.get("NOTION_TOKEN"), reason="needs NOTION_TOKEN + node")
async def test_live_connect():
    async with NotionMCP(os.environ["NOTION_TOKEN"]) as c: assert c.tools
