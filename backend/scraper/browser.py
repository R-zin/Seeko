"""Persistent Playwright contexts so manual logins survive between runs."""
from __future__ import annotations
import asyncio
from playwright.async_api import async_playwright
from config.settings import BROWSER_DIR
from .boards import LOGIN_URLS


async def open_context(name: str, headless: bool = True):
    pw = await async_playwright().start()
    try:
        ctx = await pw.chromium.launch_persistent_context(str(BROWSER_DIR / name), headless=headless,
                                                          viewport={"width": 1280, "height": 900})
    except Exception:
        await pw.stop(); raise
    return pw, ctx


async def interactive_login(source: str):
    """Open a visible browser at the login page; user signs in, then closes the window."""
    pw, ctx = await open_context(source, headless=False)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto(LOGIN_URLS[source])
    closed = asyncio.Event(); ctx.on("close", lambda _: closed.set())
    await closed.wait(); await pw.stop()
