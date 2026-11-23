"""The built UI in a real browser: Tracker tab (summary, application board drag-and-drop and menu, register filters),
command palette, keyboard shortcuts and theme. Needs frontend/dist (CI builds it first; locally: npx vite build)."""
import asyncio
import os
import socket
import threading
import time
from datetime import datetime, timedelta
import pytest
from playwright.async_api import async_playwright
import db
from db import Job, utcnow

DIST = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist", "index.html")
if not os.path.exists(DIST):
    if os.environ.get("CI"):
        raise RuntimeError("frontend/dist is missing: CI must build the frontend before the backend tests")
    pytest.skip("frontend not built (cd frontend && npx vite build)", allow_module_level=True)


@pytest.fixture(scope="module")
def server():
    import uvicorn
    from main import app
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    for _ in range(200):
        if srv.started: break
        time.sleep(0.05)
    assert srv.started, "UI server did not start"
    now, today = utcnow(), datetime.now().date()
    rows = [("ui-a1", "Acme", "Backend Engineer", "interviewing", 88, "Onsite loop", 2),
            ("ui-a2", "Initech", "Platform Engineer", "assessment", 81, "Take-home", -1),
            ("ui-a3", "Globex", "Data Analyst Intern", "applied", 74, "", None),
            ("ui-a4", "Hooli", "Site Reliability Engineer", "offer", 96, "Offer call", 3),
            ("ui-a5", "Vercel", "Senior Backend Engineer", "applied", 91, "", None),
            ("ui-a6", "Stripe", "Staff Infra Engineer", "rejected", 70, "", None),
            ("ui-a7", "Umbrella", "ML Engineer", "not_applied", 79, "", None)]
    with db.SessionLocal() as s:
        for jid, co, title, app_status, score, step, d in rows:
            s.merge(Job(id=jid, source="greenhouse", company=co, title=title, url=f"https://jobs.test/{jid}",
                        canonical_url=f"https://jobs.test/{jid}", dedupe_key=f"ui|{jid}", jd_text="Python and SQL.",
                        status="tailored" if app_status == "not_applied" else "applied", score=score,
                        application_status=app_status, applied_at=None if app_status == "not_applied" else now - timedelta(days=9),
                        next_step=step, next_step_date=today + timedelta(days=d) if d is not None else None))
        s.commit()
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True; t.join(timeout=10)


@pytest.fixture
async def page(server):
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1440, "height": 1800})   # tall: drags never scroll mid-gesture
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors
        yield pg
        await b.close()


async def status_of(pg, jid: str) -> str:
    return await pg.evaluate(f"fetch('/api/jobs/{jid}').then(r => r.json()).then(j => j.application_status)")


async def test_tracker_summary_and_board(server, page):
    await page.goto(server + "/#tracker")
    board = page.get_by_role("heading", name="Application board")
    await board.wait_for()
    summary = page.get_by_label("Summary")
    assert "applications" in (await summary.inner_text()).lower()
    for col in ("Applied", "Assessment", "Interviewing", "Offer", "Closed"):
        await page.get_by_label(f"{col} column").wait_for()

    # drag a card to another column: its application status changes
    card = page.get_by_label("Applied column").get_by_role("listitem").filter(has_text="Senior Backend Engineer")
    await card.drag_to(page.get_by_label("Interviewing column"))
    await page.get_by_label("Interviewing column").get_by_text("Senior Backend Engineer").wait_for()
    for _ in range(40):
        if await status_of(page, "ui-a5") == "interviewing": break
        await asyncio.sleep(0.1)
    assert await status_of(page, "ui-a5") == "interviewing"

    # the card menu does the same without dragging (keyboard users)
    await page.get_by_label("Move Data Analyst Intern").select_option("rejected")
    await page.get_by_label("Closed column").get_by_text("Data Analyst Intern").wait_for()
    assert await status_of(page, "ui-a3") == "rejected"

    # jobs not applied to yet are not on the board
    assert not await page.get_by_label("Applied column").get_by_text("ML Engineer").count()
    assert page.errors == []


async def test_register_filter_and_bulk_bar(server, page):
    await page.goto(server + "/#tracker")
    await page.get_by_label("Application", exact=True).select_option("offer")
    reg = page.locator("section#s-03 tbody")
    await reg.get_by_text("Site Reliability Engineer").wait_for()
    await page.wait_for_function("() => document.querySelectorAll('section#s-03 tbody tr').length === 1")
    await page.get_by_label("Select Site Reliability Engineer").check()
    bar = page.get_by_role("toolbar", name="Bulk actions")
    assert "1 selected" in await bar.inner_text()
    await bar.get_by_role("button", name="Clear").click()
    await bar.wait_for(state="detached")
    assert page.errors == []


async def test_palette_shortcuts_and_theme(server, page):
    await page.goto(server + "/#pipeline")
    await page.get_by_role("heading", name="Queue").wait_for()
    await page.keyboard.press("Control+k")
    dialog = page.get_by_role("dialog", name="Command palette")
    await page.keyboard.type("Hooli")
    await dialog.get_by_role("option").filter(has_text="Site Reliability Engineer").wait_for()
    await page.keyboard.press("Enter")
    await dialog.wait_for(state="detached")
    await page.locator("[aria-current=true]").filter(has_text="Site Reliability Engineer").wait_for()

    await page.keyboard.press("?")
    await page.get_by_role("dialog", name="Keyboard shortcuts").wait_for()
    await page.keyboard.press("Escape")
    await page.keyboard.press("g"); await page.keyboard.press("t")
    await page.wait_for_function("() => location.hash === '#tracker'")
    await page.keyboard.press("t")
    await page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
    assert page.errors == []
