"""Automated browsing with Playwright.

Careful by construction: every navigation waits a jittered delay, every source has a page and detail budget, careers
pages obey robots.txt, and the first CAPTCHA / login wall / rate-limit response stops that source for the run. Nothing
here clicks "apply", fills forms or tries to get past a bot check.
"""
from __future__ import annotations
import asyncio
import random
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncContextManager, AsyncIterator, Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
import httpx
from playwright.async_api import BrowserContext, Error as PWError, Page, TimeoutError as PWTimeout
from .extract import detect_block

BrowserFactory = Callable[[str, bool], AsyncContextManager[BrowserContext]]

ANCHORS_JS = """() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
  href: a.href,
  text: (a.innerText || a.textContent || '').trim().slice(0, 400),
  label: (a.getAttribute('aria-label') || a.getAttribute('title') || '').trim().slice(0, 300)
}))"""

PAGE_INFO_JS = """(descSel) => {
  const meta = n => (document.querySelector(`meta[property="${n}"], meta[name="${n}"]`) || {}).content || '';
  const txt = el => (el && el.innerText || '').trim();
  const h1 = txt(document.querySelector('h1'));
  let desc = '', how = '';
  if (descSel) {
    for (const el of document.querySelectorAll(descSel)) { const t = txt(el); if (t.length > desc.length) desc = t; }
    if (desc) how = 'selector';
  }
  if (desc.length < 200) {
    const specific = '[class*="description" i], [id*="description" i], [class*="job-details" i], [class*="jobdetails" i],' +
      '[class*="job-description" i], [class*="posting" i], [itemprop="description"], [data-testid*="description" i]';
    let best = '';
    for (const el of document.querySelectorAll(specific)) { const t = txt(el); if (t.length > best.length && t.length < 60000) best = t; }
    if (best.length < 200) best = txt(document.querySelector('main, article, [role="main"]'));
    if (best.length < 200) best = txt(document.body);
    if (best.length > desc.length) { desc = best; how = 'page'; }
  }
  return {title: meta('og:title') || h1 || document.title, h1, site: meta('og:site_name'), doc_title: document.title,
          desc: desc.slice(0, 60000), how, text_head: txt(document.body).slice(0, 3000)};
}"""

_NEXT = re.compile(r"^\s*(next|next page|›|»|>|more jobs|load more( jobs)?|show more( jobs| results)?|see more( jobs)?)\s*$", re.I)


class Pacer:
    """Minimum jittered gap between navigations of one source."""
    def __init__(self, min_s: float, max_s: float):
        self.min_s, self.max_s, self._last = min_s, max(max_s, min_s), 0.0

    async def wait(self) -> None:
        gap = random.uniform(self.min_s, self.max_s)
        delta = time.monotonic() - self._last
        if delta < gap:
            await asyncio.sleep(gap - delta)
        self._last = time.monotonic()


class Robots:
    """robots.txt per origin (cached). Unreachable or 4xx robots.txt means allowed, per RFC 9309."""
    def __init__(self, enabled: bool, client: httpx.AsyncClient | None, agent: str = "Seeko"):
        self.enabled, self.client, self.agent = enabled, client, agent
        self._cache: dict[str, RobotFileParser] = {}

    async def allowed(self, url: str) -> bool:
        if not self.enabled or self.client is None:
            return True
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._cache:
            rp = RobotFileParser()
            try:
                r = await self.client.get(origin + "/robots.txt", timeout=10, follow_redirects=True)
                rp.parse(r.text.splitlines() if r.status_code < 400 else [])
            except httpx.HTTPError:
                rp.parse([])
            self._cache[origin] = rp
        return self._cache[origin].can_fetch(self.agent, url)


@dataclass
class PageRead:
    url: str
    status: int | None = None
    blocked: str | None = None
    error: str | None = None
    exhausted: bool = False      # the source's page budget is used up; nothing was loaded
    html: str = ""
    info: dict = field(default_factory=dict)
    anchors: list[dict] = field(default_factory=list)


class Session:
    """One source's browser context with a single reused tab. `max_loads` caps page loads (the daily budget)."""
    def __init__(self, context: BrowserContext, pacer: Pacer, nav_timeout_ms: int = 30_000, max_loads: int | None = None):
        self.context, self.pacer, self.nav_timeout_ms = context, pacer, nav_timeout_ms
        self.max_loads, self.loads = max_loads, 0
        self._page: Page | None = None

    def _spend(self) -> bool:
        if self.max_loads is not None and self.loads >= self.max_loads:
            return False
        self.loads += 1
        return True

    async def page(self) -> Page:
        if self._page is None or self._page.is_closed():
            self._page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self._page

    async def _goto(self, url: str) -> PageRead:
        if not self._spend():
            return PageRead(url=url, exhausted=True, error="page budget used up")
        page = await self.page()
        await self.pacer.wait()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
        except (PWTimeout, PWError) as e:
            return PageRead(url=url, error=f"navigation failed: {str(e).splitlines()[0][:200]}")
        await self._settle(page)
        status = resp.status if resp else None
        read = PageRead(url=page.url, status=status)
        try:
            head = await page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 3000)")
            read.blocked = detect_block(status, page.url, await page.title(), head)
        except PWError as e:
            read.error = f"page unreadable: {str(e).splitlines()[0][:200]}"
        if not read.blocked and status and status >= 400:
            read.error = f"http {status}"
        return read

    @staticmethod
    async def _settle(page: Page) -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=6_000)
        except (PWTimeout, PWError):
            pass   # long-polling pages never go idle; the DOM is there already

    async def anchors(self, scrolls: int = 2) -> list[dict]:
        """Links in every frame (careers pages often embed their ATS in an iframe), after loading lazy content."""
        page = await self.page()
        for _ in range(scrolls):
            try:
                await page.evaluate("() => window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
                await page.wait_for_timeout(700)
            except PWError:
                break
        out: list[dict] = []
        for frame in page.frames:
            try:
                out += await frame.evaluate(ANCHORS_JS)
            except PWError:
                continue
        return out

    async def read_listing(self, url: str, scrolls: int = 2) -> PageRead:
        read = await self._goto(url)
        if not (read.blocked or read.error):
            read.anchors = await self.anchors(scrolls)
        return read

    async def more(self) -> PageRead | None:
        """Follow the listing's own pagination (rel=next, a "Next" link, a "Load more" button). None = no more."""
        page = await self.page()
        candidates = [page.locator('a[rel="next"]'), page.get_by_role("link", name=_NEXT), page.get_by_role("button", name=_NEXT)]
        for loc in candidates:
            el = loc.first
            try:
                if not (await el.count() and await el.is_visible() and await el.is_enabled()):
                    continue
                if not self._spend():
                    return PageRead(url=page.url, exhausted=True, error="page budget used up")
                await self.pacer.wait()
                await el.click(timeout=5_000)
            except (PWTimeout, PWError):
                continue
            await self._settle(page)
            read = PageRead(url=page.url)
            try:
                head = await page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 3000)")
                read.blocked = detect_block(None, page.url, await page.title(), head)
            except PWError as e:
                read.error = str(e).splitlines()[0][:200]
            if not (read.blocked or read.error):
                read.anchors = await self.anchors()
            return read
        return None

    async def read_detail(self, url: str, description_selector: str = "") -> PageRead:
        read = await self._goto(url)
        if read.blocked or read.error:
            return read
        page = await self.page()
        try:
            read.html = await page.content()
            read.info = await page.evaluate(PAGE_INFO_JS, description_selector or "")
        except PWError as e:
            read.error = f"page unreadable: {str(e).splitlines()[0][:200]}"
        return read


@asynccontextmanager
async def persistent_browser(profile: str, headless: bool) -> AsyncIterator[BrowserContext]:
    """Default factory: the persistent profile in browser_data/<profile>, shared with Setup's "Sign in" button."""
    from scraper.browser import open_context
    pw, ctx = await open_context(profile, headless)
    try:
        yield ctx
    finally:
        try:
            await ctx.close()
        finally:
            await pw.stop()
