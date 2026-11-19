"""Add one job you found yourself: by its link, from the bookmarklet, or by pasting it.

* link — one careful read with the same code as the search (`Session.read_detail` + `extract_posting`): one page load
  in that site's browser profile, robots.txt obeyed on careers pages, and a CAPTCHA / login wall stops it (never
  bypassed). For sites that need you signed in, use the bookmarklet instead.
* capture (bookmarklet) — the page text and its JSON-LD, sent from *your* browser tab. Seeko opens nothing: it only
  parses what you were looking at.
* paste — title, company and description typed in (a link is still needed for Seeko to apply).

A job already stored (same link, or same company + title) is returned rather than added twice. Mode "apply" lets the
pipeline score, tailor and fill it (never auto-skipped for a low score: you chose it); "applied" only tracks it.
"""
from __future__ import annotations
import re
from urllib.parse import urlsplit
from config.profile import load_profile
from config.settings import get_settings
from scraper.ats import detect_ats
from scraper.boards import BOARDS
from scraper.dedupe import canonical_url
from scraper.textutil import tidy
from . import classify
from .browse import Pacer, Robots, Session
from .extract import extract_posting

MODES = ("apply", "applied")
MAX_TEXT = 60_000


class IntakeError(Exception):
    pass


# Runs in your browser tab when you click the bookmarklet: reads the posting you are looking at (selected text wins,
# then the description element, then the main text) plus any JobPosting JSON-LD, and opens Seeko with it in the URL
# fragment (#add=...), which never leaves your machine except to Seeko itself.
BOOKMARKLET_JS = (
    "(()=>{const t=e=>e&&(e.innerText||'').trim()||'',q=s=>document.querySelector(s);let d='';"
    "for(const g of ['.jobs-description__content,#job-details,.show-more-less-html__markup,#jobDescriptionText,"
    "[class*=\"job-description\" i],[class*=\"jobdescription\" i],[data-testid*=\"description\" i],[itemprop=description]',"
    "'main,article,[role=main]']){for(const e of document.querySelectorAll(g)){const x=t(e);"
    "if(x.length>d.length&&x.length<80000)d=x}if(d.length>300)break}"
    "const s=String(getSelection()||'').trim(),c={url:location.href,title:document.title,h1:t(q('h1')),"
    "site:(q('meta[property=\"og:site_name\"]')||{}).content||'',text:(s.length>200?s:d||t(document.body)).slice(0,20000),"
    "jsonld:[...document.querySelectorAll('script[type=\"application/ld+json\"]')].map(e=>e.textContent)"
    ".filter(x=>x.includes('JobPosting')).map(x=>x.slice(0,20000)).slice(0,2)};"
    "window.open(ORIGIN+'/#add='+encodeURIComponent(JSON.stringify(c)),'_blank')})()"
)


def bookmarklet(origin: str) -> str:
    """The bookmarklet link for a Seeko UI at `origin` (e.g. http://127.0.0.1:5173)."""
    import json
    from urllib.parse import quote
    if not re.match(r"^https?://[\w.\-\[\]:]+$", origin or ""):
        raise IntakeError("origin must look like http://127.0.0.1:5173")
    return "javascript:" + quote(BOOKMARKLET_JS.replace("ORIGIN", json.dumps(origin.rstrip("/"))), safe="")


def source_for(url: str) -> tuple[str, object | None, str]:
    """(source name, board config or None, canonical URL) for a posting link."""
    for name, board in BOARDS.items():
        if board.link.search(url):
            return name, board, board.canonical(url) or canonical_url(url)
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    for name, board in BOARDS.items():                       # a board page that is not a plain posting link
        if name in host:
            return name, board, canonical_url(url)
    ats = detect_ats(url)
    return (ats[0] if ats else "careers"), None, canonical_url(url)


def _company_from_host(url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    parts = [p for p in host.split(".") if p not in ("careers", "jobs", "boards", "apply", "work")]
    return parts[0].capitalize() if parts else ""


def _check_url(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^https?://[^\s/]+\.[^\s/]+", url):
        raise IntakeError("that does not look like a web link (it should start with http:// or https://)")
    return url


def from_capture(capture: dict) -> dict:
    """A posting from what the bookmarklet read in your browser: JSON-LD first, then the page's text."""
    url = _check_url(capture.get("url", ""))
    if not url:
        raise IntakeError("the capture has no page address")
    ld = "".join(f'<script type="application/ld+json">{x}</script>' for x in (capture.get("jsonld") or [])[:3]
                 if isinstance(x, str))
    title = str(capture.get("title") or "")
    info = {"desc": str(capture.get("text") or "")[:MAX_TEXT], "title": title, "doc_title": title,
            "h1": str(capture.get("h1") or ""), "site": str(capture.get("site") or ""), "how": "page"}
    p = extract_posting(ld, url, info)
    if p is None:
        raise IntakeError("no job description in that page; select the description text and click the bookmarklet again")
    return p


def from_paste(url: str, title: str, company: str, location: str, description: str) -> dict:
    if not title.strip():
        raise IntakeError("a job title is needed")
    return {"title": title.strip()[:300], "company": company.strip()[:300], "location": location.strip()[:300],
            "location_type": "", "employment_type": "", "posted_at": None, "valid_through": None,
            "jd_text": tidy(description)[:MAX_TEXT], "url": _check_url(url), "jd_source": "pasted"}


async def fetch_posting(url: str, deps=None, headless: bool = True) -> dict:
    """Read one posting page the careful way. Raises IntakeError with what to do instead when it cannot."""
    from jobsearch import service
    from jobsearch.graph import Deps
    d = deps or Deps()
    url = _check_url(url)
    if not url:
        raise IntakeError("a link is needed")
    if service.is_running():   # the search holds the same browser profiles
        raise IntakeError("a job search is using the browser right now; try again when it finishes, or use the bookmarklet")
    source, board, _ = source_for(url)
    st = get_settings()
    async with d.http() as client, d.browser(board.source if board else "search", headless) as ctx:
        if not await Robots(st.search_respect_robots and board is None, client).allowed(url):
            raise IntakeError("this site's robots.txt asks automated readers to stay away from that page; "
                              "open it in your browser and use the bookmarklet instead")
        read = await Session(ctx, Pacer(0, 0), max_loads=1).read_detail(url, board.description_selector if board else "")
    if read.blocked:
        raise IntakeError(f"the site answered with a {read.blocked} — Seeko does not get around those. Open the posting "
                          f"in your browser and use the bookmarklet, or paste the description")
    if read.error:
        raise IntakeError(read.error)
    p = extract_posting(read.html, url, read.info)
    if p is None:
        raise IntakeError("no job description found on that page; use the bookmarklet or paste it")
    return p


def enrich(p: dict, url: str = "") -> dict:
    """Source, canonical link, job type, remote and relevance - as the search records them."""
    p = dict(p)
    url = p.get("url") or url
    if url:
        source, _, cu = source_for(url)
    else:   # pasted without a link: a stable stand-in so the same job is recognised again
        from scraper.dedupe import dedupe_key
        source, cu = "manual", f"manual:{dedupe_key(p.get('company', ''), p['title'])}"
    p.update(url=url, source=source, canonical_url=cu)
    p["company"] = (p.get("company") or "").strip() or (_company_from_host(url) if url else "")
    if not (p.get("title") or "").strip():
        raise IntakeError("could not tell the job title from that page; paste it instead")
    p["job_type"] = classify.classify_job_type(p["title"], p.get("employment_type", ""), url)
    if p.get("is_remote") is None:
        p["is_remote"] = classify.detect_remote(p.get("location", ""), p["title"], p.get("location_type", ""))
    prof = load_profile()
    p["relevance"] = classify.relevance(p["title"], p.get("jd_text", ""), prof.search.role_titles, prof.search.keywords)
    return p


async def add_job(*, url: str = "", title: str = "", company: str = "", location: str = "", description: str = "",
                  capture: dict | None = None, mode: str = "apply", preview: bool = False, headless: bool = True,
                  deps=None) -> dict:
    """Read / parse a job and store it. Returns {"job": ..., "created": bool, "preview": bool}."""
    from jobsearch.graph import Deps
    if mode not in MODES:
        raise IntakeError(f"mode must be one of {MODES}")
    d = deps or Deps()
    if capture:
        p = from_capture(capture)
    elif description.strip() or (title.strip() and not url.strip()):
        p = from_paste(url, title, company, location, description)
    elif url.strip():
        p = await fetch_posting(url, d, headless)
    else:
        raise IntakeError("give a link, or a title and description")
    for k, v in (("title", title), ("company", company), ("location", location)):
        if v.strip(): p[k] = v.strip()          # what you typed wins over what was read
    p = enrich(p, url)
    if mode == "apply" and not p["url"]:
        raise IntakeError("Seeko needs the posting's link to apply; add it, or choose “I applied already”")
    if preview:
        return {"job": {k: p.get(k) for k in ("title", "company", "location", "url", "source", "job_type", "is_remote",
                                               "employment_type", "posted_at", "valid_through", "jd_source", "relevance")}
                | {"jd_text": (p.get("jd_text") or "")[:1500], "jd_chars": len(p.get("jd_text") or "")},
                "created": False, "preview": True}
    job_id, created = d.store.add_job(p, origin="user", applied=(mode == "applied"))
    return {"job_id": job_id, "created": created, "preview": False}
