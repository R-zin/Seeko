"""Job boards browsed with Playwright (persistent profile in browser_data/<source>, so a manual login is reused).

Everything site-specific lives here and nowhere else: how to build a search URL for a page of results, which links
on a results page are job postings (matched on the href, which drifts far less than CSS classes), how to turn such a
link into a stable detail URL, and where the description sits when a detail page has no JSON-LD.
Terms of service of these sites restrict automation: they are off by default; keep volumes low.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, quote, quote_plus, urlsplit
from config.settings import get_settings


@dataclass(frozen=True)
class Board:
    source: str
    login_url: str
    search_url: Callable[[str, str, int, bool], str | None]   # (query, location, page index, internship) -> URL | None
    link: re.Pattern                                            # matches the href of a posting on a results page
    canonical: Callable[[str], str | None]                     # results-page href -> stable detail URL
    description_selector: str                                  # detail-page description when there is no JSON-LD
    native_internship_filter: bool = False                     # search_url handles internship=True itself
    lead_location: Callable[[str], str] | None = None          # location readable from a posting URL ("" = unknown)


def _linkedin_search(q: str, loc: str, page: int, internship: bool) -> str:
    jt = "&f_JT=I" if internship else ""
    return f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(q)}&location={quote_plus(loc)}{jt}&start={25 * page}"


_LI_ID = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*?-)?(\d{6,})")


def _linkedin_canonical(href: str) -> str | None:
    m = _LI_ID.search(href)
    return f"https://www.linkedin.com/jobs/view/{m.group(1)}/" if m else None


def _indeed_host() -> str:
    return (get_settings().indeed_host or "www.indeed.com").strip().strip("/")


def _indeed_search(q: str, loc: str, page: int, internship: bool) -> str:
    return f"https://{_indeed_host()}/jobs?q={quote_plus(q)}&l={quote_plus(loc)}&start={10 * page}"


def _indeed_canonical(href: str) -> str | None:
    jk = parse_qs(urlsplit(href).query).get("jk")
    return f"https://{_indeed_host()}/viewjob?jk={jk[0]}" if jk and re.fullmatch(r"[0-9a-f]{16}", jk[0]) else None


def _glassdoor_search(q: str, loc: str, page: int, internship: bool) -> str | None:
    if page:   # results load with a button, not a URL; one page per query
        return None
    return f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={quote_plus(q)}&locKeyword={quote_plus(loc)}"


def _glassdoor_canonical(href: str) -> str | None:
    p = urlsplit(href)
    jl = parse_qs(p.query).get("jl")
    if jl: return f"https://www.glassdoor.com/job-listing/index.htm?jl={jl[0]}"
    return f"https://www.glassdoor.com{p.path}" if "/job-listing/" in p.path else None


def _internshala_search(q: str, loc: str, page: int, internship: bool) -> str:
    # Keyword search (verified live 2026-11): /internships/keywords-<q>/ and /jobs/keywords-<q>/, then /page-N/.
    # The location is not part of the URL; it is read from each posting's slug and screened before opening it.
    kind = "internships" if internship else "jobs"
    page_part = f"page-{page + 1}/" if page else ""
    return f"https://internshala.com/{kind}/keywords-{quote(' '.join(q.lower().split()))}/{page_part}"


def _internshala_canonical(href: str) -> str | None:
    p = urlsplit(href)
    return f"https://internshala.com{p.path.rstrip('/')}" if re.match(r"^/(internship|job)/details?/", p.path) else None


def _internshala_location(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    if slug.startswith("work-from-home") or "-work-from-home-" in slug:
        return "Remote"
    m = re.search(r"(?:internship|job)-in-([a-z0-9-]+?)-at-", slug)
    return "" if not m or m.group(1) == "multiple-locations" else m.group(1).replace("-", " ").title()


BOARDS: dict[str, Board] = {
    "linkedin": Board(
        "linkedin", "https://www.linkedin.com/login", _linkedin_search, re.compile(r"linkedin\.com/(comm/)?jobs/view/"),
        _linkedin_canonical,
        ".show-more-less-html__markup, .description__text, .jobs-description__content, #job-details",
        native_internship_filter=True),
    "indeed": Board(
        "indeed", "https://secure.indeed.com/auth", _indeed_search, re.compile(r"indeed\.[a-z.]+/(viewjob|rc/clk|pagead/clk)\?.*\bjk="),
        _indeed_canonical, "#jobDescriptionText"),
    "glassdoor": Board(
        "glassdoor", "https://www.glassdoor.com/profile/login_input.htm", _glassdoor_search,
        re.compile(r"glassdoor\.[a-z.]+/(job-listing/|partner/jobListing\.htm\?.*\bjl=)"), _glassdoor_canonical,
        "[class*='JobDetails_jobDescription'], #JobDescriptionContainer"),
    "internshala": Board(
        "internshala", "https://internshala.com/login/user", _internshala_search,
        re.compile(r"internshala\.com/(internship|job)/details?/"), _internshala_canonical,
        ".internship_details, .detail_view, #details_container", native_internship_filter=True,
        lead_location=_internshala_location),
}

LOGIN_URLS = {k: b.login_url for k, b in BOARDS.items()}
