"""LinkedIn / Indeed / Glassdoor scrapers (authenticated persistent profile). Selectors drift; each is isolated here.
Terms of service of these sites restrict automation: keep limits low, use at your own risk."""
from __future__ import annotations
from urllib.parse import quote_plus
from schemas import JobPosting
from .base import RateLimiter
from .browser import open_context


class _Board:
    source = ""; limiter = RateLimiter(4, 10)
    def url(self, title: str, loc: str) -> str: raise NotImplementedError
    card_sel = ""; title_sel = ""; company_sel = ""; loc_sel = ""; link_sel = ""; desc_sel = ""; easy_sel = ""

    async def search(self, titles, locations, limit=15) -> list[JobPosting]:
        pw, ctx = await open_context(self.source)
        out: list[JobPosting] = []
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            for t in titles:
                for loc in (locations or [""]):
                    if len(out) >= limit: break
                    await self.limiter.wait(); await page.goto(self.url(t, loc), wait_until="domcontentloaded")
                    cards = await page.query_selector_all(self.card_sel)
                    for c in cards[: limit - len(out)]:
                        try:
                            await c.click(); await self.limiter.wait()
                            g = lambda sel: page.locator(sel).first.inner_text() if sel else None
                            a = await c.query_selector(self.link_sel); href = await a.get_attribute("href") if a else ""
                            if href and href.startswith("/"): href = page.url.split("/", 3)[0] + "//" + page.url.split("/")[2] + href
                            out.append(JobPosting(
                                source=self.source, title=(await c.inner_text()).split("\n")[0], company=(await (await c.query_selector(self.company_sel)).inner_text()) if self.company_sel and await c.query_selector(self.company_sel) else "",
                                location=(await (await c.query_selector(self.loc_sel)).inner_text()) if self.loc_sel and await c.query_selector(self.loc_sel) else loc,
                                url=href or page.url, jd_text=await g(self.desc_sel) if self.desc_sel else "",
                                is_easy_apply=bool(self.easy_sel and await c.query_selector(self.easy_sel))))
                        except Exception: continue
        finally:
            await ctx.close(); await pw.stop()
        return out


class LinkedIn(_Board):
    source = "linkedin"
    def url(self, t, l): return f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(t)}&location={quote_plus(l)}&f_AL=false"
    card_sel = "li[data-occludable-job-id], .job-card-container"; link_sel = "a.job-card-list__title, a[href*='/jobs/view/']"
    company_sel = ".job-card-container__primary-description, .artdeco-entity-lockup__subtitle"; loc_sel = ".job-card-container__metadata-item"
    desc_sel = ".jobs-description__content, #job-details"; easy_sel = ".job-card-container__apply-method, [aria-label*='Easy Apply']"


class Indeed(_Board):
    source = "indeed"
    def url(self, t, l): return f"https://www.indeed.com/jobs?q={quote_plus(t)}&l={quote_plus(l)}"
    card_sel = "div.job_seen_beacon"; link_sel = "a.jcs-JobTitle"; company_sel = "[data-testid='company-name']"
    loc_sel = "[data-testid='text-location']"; desc_sel = "#jobDescriptionText"; easy_sel = "[data-testid='indeedApply'], .iaLabel"


class Glassdoor(_Board):
    source = "glassdoor"
    def url(self, t, l): return f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={quote_plus(t)}&locKeyword={quote_plus(l)}"
    card_sel = "li[data-test='jobListing']"; link_sel = "a[data-test='job-link']"; company_sel = "[class*='EmployerProfile_employerName']"
    loc_sel = "[data-test='emp-location']"; desc_sel = "[class*='JobDetails_jobDescription']"; easy_sel = "[data-test='easy-apply-badge']"


BOARDS = {"linkedin": LinkedIn, "indeed": Indeed, "glassdoor": Glassdoor}
