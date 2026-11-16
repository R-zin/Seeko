"""Direct company career pages: Greenhouse, Lever, Ashby, Workday via their public JSON endpoints."""
from __future__ import annotations
import asyncio, re
from urllib.parse import urlsplit
import httpx
from schemas import JobPosting

_TAGS = re.compile(r"<[^>]+>")
def _text(html: str) -> str: return re.sub(r"\s+", " ", _TAGS.sub(" ", html or "")).strip()


def detect_ats(url: str) -> tuple[str, str] | None:
    """Return (ats, slug) for a careers URL, else None."""
    p = urlsplit(url); host, parts = p.netloc.lower(), [x for x in p.path.split("/") if x]
    if "greenhouse.io" in host and parts: return "greenhouse", parts[0] if parts[0] != "embed" else parts[-1]
    if host.endswith("lever.co") and parts: return "lever", parts[0]
    if host.endswith("ashbyhq.com") and parts: return "ashby", parts[0]
    if "myworkdayjobs.com" in host:
        site = next((x for x in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", x)), "")
        return "workday", f"{host}|{site}"
    return None


async def fetch_greenhouse(c: httpx.AsyncClient, slug: str) -> list[JobPosting]:
    r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}); r.raise_for_status()
    return [JobPosting(source="greenhouse", company=slug, title=j["title"], location=(j.get("location") or {}).get("name", ""),
                       url=j["absolute_url"], jd_text=_text(j.get("content", ""))) for j in r.json().get("jobs", [])]


async def fetch_lever(c: httpx.AsyncClient, slug: str) -> list[JobPosting]:
    r = await c.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}); r.raise_for_status()
    return [JobPosting(source="lever", company=slug, title=j["text"], location=(j.get("categories") or {}).get("location", "") or "",
                       url=j["hostedUrl"], jd_text=j.get("descriptionPlain", "")) for j in r.json()]


async def fetch_ashby(c: httpx.AsyncClient, slug: str) -> list[JobPosting]:
    r = await c.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "false"}); r.raise_for_status()
    return [JobPosting(source="ashby", company=slug, title=j["title"], location=j.get("location", "") or "",
                       url=j["jobUrl"], jd_text=j.get("descriptionPlain", "")) for j in r.json().get("jobs", [])]


async def _wd_request(c: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
    """Workday rate-limits aggressively: back off and retry on 429/5xx."""
    for attempt in range(4):
        r = await c.request(method, url, **kw)
        if r.status_code not in (429, 500, 502, 503): return r
        await asyncio.sleep(min(2 ** attempt * 2, 20))
    return r


async def fetch_workday(c: httpx.AsyncClient, slug: str, limit: int = 40, detail_delay: float = 0.6) -> list[JobPosting]:
    host, site = slug.split("|"); tenant = host.split(".")[0]
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    hdr = {"Content-Type": "application/json", "Accept": "application/json"}
    out: list[JobPosting] = []; offset = 0
    while len(out) < limit:
        r = await _wd_request(c, "POST", f"{base}/jobs", headers=hdr,
                              json={"limit": 20, "offset": offset, "searchText": "", "appliedFacets": {}})  # Workday max page size = 20
        r.raise_for_status(); postings = r.json().get("jobPostings", [])
        if not postings: break
        for j in postings:
            if len(out) >= limit: break
            path = j["externalPath"]
            await asyncio.sleep(detail_delay)
            d = await _wd_request(c, "GET", f"{base}{path}", headers={"Accept": "application/json"})
            info = d.json().get("jobPostingInfo", {}) if d.status_code == 200 else {}
            out.append(JobPosting(source="workday", company=tenant, title=j["title"], location=j.get("locationsText", ""),
                                  url=f"https://{host}/{site}{path}", jd_text=_text(info.get("jobDescription", ""))))
        offset += 20
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby, "workday": fetch_workday}


async def fetch_watchlist(urls: list[str], client: httpx.AsyncClient | None = None) -> list[JobPosting]:
    own = client is None; c = client or httpx.AsyncClient(timeout=30, headers={"User-Agent": "Mozilla/5.0 Seeko"})
    jobs: list[JobPosting] = []
    try:
        for u in urls:
            d = detect_ats(u)
            if not d: continue
            try: jobs += await FETCHERS[d[0]](c, d[1])
            except Exception as e: print(f"watchlist {u}: {e}")
    finally:
        if own: await c.aclose()
    return jobs
