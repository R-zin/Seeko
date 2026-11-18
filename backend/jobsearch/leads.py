"""Results page -> leads (links to individual postings). Pure functions over the anchors the browser collected."""
from __future__ import annotations
import re
from urllib.parse import urlsplit
from scraper.boards import Board
from scraper.dedupe import canonical_url

# Where companies host postings; links to these count even when they leave the careers site.
ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "smartrecruiters.com", "workable.com",
             "recruitee.com", "bamboohr.com", "breezy.hr", "jobvite.com", "icims.com", "teamtailor.com", "personio.de",
             "personio.com", "zohorecruit.com", "zohorecruit.in", "keka.com", "darwinbox.in", "freshteam.com",
             "successfactors.com", "taleo.net", "oraclecloud.com", "rippling.com", "dover.com", "wellfound.com")
_JOB_PATH = re.compile(
    r"/(jobs?|careers?|positions?|openings?|vacanc(y|ies)|opportunit(y|ies)|requisitions?|postings?|job-?details?|"
    r"roles?)/[^/?#]{2,}|[?&](gh_jid|jobid|job_id|jid|requisitionid|jobreq|posting_id)=", re.I)
_LISTING_TAIL = re.compile(r"/(jobs|careers|positions|openings|search|teams?|departments?|locations?|benefits|"
                           r"culture|about|life|students|university|early-careers)/?$", re.I)
_NAV_TEXT = {"jobs", "careers", "all jobs", "view all jobs", "see all jobs", "open positions", "open roles",
             "search jobs", "apply", "apply now", "learn more", "read more", "back", "next", "previous", "home",
             "view jobs", "explore jobs", "job openings", "openings", "view job", "details", "view details",
             "see more", "show more", "load more", "more", "privacy policy", "cookie policy", "terms", "login",
             "sign in", "sign up", "register", "join our talent community", "linkedin", "twitter", "facebook"}


def _site(host: str) -> str:
    labels = host.lower().removeprefix("www.").split(".")
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "ac", "gov", "edu") and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _label(a: dict) -> str:
    text = (a.get("text") or "").strip()
    first = next((ln.strip() for ln in text.split("\n") if ln.strip()), "")
    return re.sub(r"\s+", " ", first or (a.get("label") or "").strip())


def board_leads(anchors: list[dict], board: Board) -> list[dict]:
    out, seen = [], set()
    for a in anchors:
        href = a.get("href") or ""
        if not board.link.search(href):
            continue
        url = board.canonical(href)
        if not url:
            continue
        cu = canonical_url(url)
        if cu in seen:
            # The same card often has several links (logo, title); keep the most descriptive label.
            title = _label(a)
            for lead in out:
                if lead["canonical_url"] == cu and len(title) > len(lead["title"]) and len(title) < 160:
                    lead["title"] = title
            continue
        seen.add(cu)
        out.append({"source": board.source, "url": url, "canonical_url": cu, "title": _label(a)[:300], "company": "",
                    "location": board.lead_location(url) if board.lead_location else "", "complete": False})
    return out


def _ats_posting_path(host: str, path: str) -> bool:
    parts = [p for p in path.split("/") if p]
    if "greenhouse.io" in host: return "jobs" in parts and parts[-1] != "jobs"
    if "myworkdayjobs.com" in host: return "job" in parts
    return len(parts) >= 2          # lever/ashby/smartrecruiters/...: /<company>/<posting-id>


def careers_leads(anchors: list[dict], page_url: str, source: str = "careers") -> list[dict]:
    """Links on an arbitrary careers page that look like individual postings."""
    base = urlsplit(page_url)
    site, here = _site(base.netloc), canonical_url(page_url)
    out, seen = [], set()
    for a in anchors:
        href = a.get("href") or ""
        p = urlsplit(href)
        if p.scheme not in ("http", "https"):
            continue
        host = p.netloc.lower()
        ats = any(host == h or host.endswith("." + h) for h in ATS_HOSTS)
        if not ats and _site(host) != site:
            continue
        cu = canonical_url(href)
        if cu == here or cu in seen:
            continue
        title = _label(a)
        if not (4 <= len(title) <= 140) or title.lower().strip(" →›»>") in _NAV_TEXT:
            continue
        if ats:
            if not _ats_posting_path(host, p.path):
                continue
        elif not _JOB_PATH.search(p.path + ("?" + p.query if p.query else "")) or _LISTING_TAIL.search(p.path):
            continue
        seen.add(cu)
        out.append({"source": source, "url": href.split("#")[0], "canonical_url": cu, "title": title[:300],
                    "company": "", "location": "", "complete": False})
    return out
