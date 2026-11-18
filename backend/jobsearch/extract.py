"""Turn a fetched job page into a posting. Pure functions over HTML/text, so they are tested without a browser.

Order of trust: schema.org JobPosting JSON-LD (what Google for Jobs reads: title, company, location, employment
type, dates, full description) -> the board's description element -> the page's main text. `jd_source` records which.
"""
from __future__ import annotations
import html as _html
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from scraper.textutil import html_to_text, tidy

_LD = re.compile(r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)


def parse_date(v) -> datetime | None:
    """ISO date/datetime (or a datetime) -> naive UTC. Anything unparseable -> None."""
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip().replace("Z", "+00:00")
    for cand in (s, s[:10]):
        try:
            d = datetime.fromisoformat(cand)
            return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
        except ValueError:
            continue
    return None


def _loads(raw: str):
    raw = raw.strip().removeprefix("<!--").removesuffix("-->").strip()
    try:
        return json.loads(raw, strict=False)      # strict=False: raw newlines inside strings are common
    except json.JSONDecodeError:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", raw), strict=False)   # trailing commas
        except json.JSONDecodeError:
            return None


def _walk(node):
    if isinstance(node, list):
        for x in node: yield from _walk(x)
    elif isinstance(node, dict):
        yield node
        for k in ("@graph", "mainEntity", "itemListElement", "item"):
            if k in node: yield from _walk(node[k])


def _is_posting(d: dict) -> bool:
    t = d.get("@type")
    return "JobPosting" in (t if isinstance(t, list) else [t])


def jsonld_postings(html: str) -> list[dict]:
    out = []
    for m in _LD.finditer(html or ""):
        data = _loads(m.group(1))
        if data is not None:
            out += [d for d in _walk(data) if _is_posting(d)]
    return out


def _name(v) -> str:
    if isinstance(v, dict): return str(v.get("name") or "").strip()
    if isinstance(v, list): return _name(v[0]) if v else ""
    return str(v or "").strip()


def _place(loc) -> str:
    if isinstance(loc, list):
        return " / ".join(dict.fromkeys(p for p in (_place(x) for x in loc) if p))
    if not isinstance(loc, dict):
        return str(loc or "").strip()
    a = loc.get("address", loc)
    if isinstance(a, str): return a.strip()
    if not isinstance(a, dict): return ""
    parts = [a.get("addressLocality"), a.get("addressRegion"), _name(a.get("addressCountry"))]
    return ", ".join(dict.fromkeys(str(p).strip() for p in parts if p and str(p).strip()))


def posting_from_jsonld(d: dict, page_url: str) -> dict:
    et = d.get("employmentType") or ""
    loc_type = d.get("jobLocationType") or ""
    location = _place(d.get("jobLocation"))
    if not location and d.get("applicantLocationRequirements"):
        location = _place(d.get("applicantLocationRequirements"))
    return {
        "title": _html.unescape(str(d.get("title") or "")).strip(),
        "company": _html.unescape(_name(d.get("hiringOrganization"))),
        "location": location,
        "location_type": " ".join(loc_type) if isinstance(loc_type, list) else str(loc_type),
        "employment_type": ",".join(et) if isinstance(et, list) else str(et),
        "posted_at": iso(parse_date(d.get("datePosted"))),
        "valid_through": iso(parse_date(d.get("validThrough"))),
        "jd_text": html_to_text(str(d.get("description") or "")),
        "url": page_url,
        "jd_source": "json-ld",
    }


def iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _company_from_title(doc_title: str) -> str:
    # "Backend Engineer - Acme | Careers" -> best effort only; the LLM review may fill it in properly.
    parts = [p.strip() for p in re.split(r"\s[|\-–—]\s", doc_title or "") if p.strip()]
    return parts[1] if len(parts) >= 2 and len(parts[1]) <= 60 else ""


def extract_posting(html: str, url: str, info: dict | None = None, lead: dict | None = None) -> dict | None:
    """Best posting for a detail page. `info` is what the browser read from the rendered page (title, site name,
    description text and how it was found); `lead` carries what the results page already said."""
    info, lead = info or {}, lead or {}
    found = jsonld_postings(html)
    if found:
        # A page can embed several (e.g. "similar jobs"); prefer the one whose title matches the lead.
        want = (lead.get("title") or "").lower()
        best = next((d for d in found if want and str(d.get("title", "")).lower() == want), found[0])
        p = posting_from_jsonld(best, url)
        if len(p["jd_text"]) < 200 and len(info.get("desc", "")) > len(p["jd_text"]):
            p["jd_text"] = tidy(info["desc"])           # some sites truncate the JSON-LD description
        p["company"] = p["company"] or lead.get("company", "") or info.get("site", "")
        p["location"] = p["location"] or lead.get("location", "")
        return p
    desc = tidy(info.get("desc", ""))
    if not desc:
        return None
    title = (info.get("h1") or lead.get("title") or info.get("title") or "").strip()
    return {
        "title": title[:300],
        "company": lead.get("company") or info.get("site") or _company_from_title(info.get("doc_title", "")),
        "location": lead.get("location", ""),
        "location_type": "",
        "employment_type": "",
        "posted_at": None,
        "valid_through": None,
        "jd_text": desc,
        "url": url,
        "jd_source": info.get("how") or "page",
    }


# ── Blocks (CAPTCHA, login walls, rate limits). We stop the source for this run; we never try to get around them. ──
_BLOCK_TEXT = re.compile(r"verify you are (a )?human|are you a robot|unusual traffic|complete the security check|"
                         r"press (&|and) hold|checking (if the site connection is secure|your browser)|"
                         r"\bcaptcha\b|access denied|request (was )?blocked|attention required|just a moment\.\.\.", re.I)
_LOGIN_PATH = re.compile(r"/(login|signin|sign-in|sign_in|uas/login|authwall|checkpoint|account/login|auth)(/|$)", re.I)


def detect_block(status: int | None, final_url: str, doc_title: str = "", text_head: str = "") -> str | None:
    if status in (401, 403, 407, 429, 999):
        return f"http {status}"
    path = urlsplit(final_url or "").path
    if re.search(r"captcha|/challenge", final_url or "", re.I):
        return "captcha"
    if _LOGIN_PATH.search(path):
        return "login required"
    if _BLOCK_TEXT.search(doc_title or ""):
        return "bot check"
    if len(text_head or "") < 1500 and _BLOCK_TEXT.search(text_head or ""):
        return "bot check"
    return None
