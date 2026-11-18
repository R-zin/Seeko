from __future__ import annotations
import hashlib, re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from sqlalchemy import select
from db import Job, SessionLocal
from schemas import JobPosting
from jobsearch.classify import classify_job_type

_TRACK = re.compile(r"^(utm_|ref|trk|tracking|source|gh_src|lever-source|src$|refId|trackingId|currentJobId)", re.I)
_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|gmbh)\b\.?", re.I)


def norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _SUFFIX.sub("", s.lower())).strip()


def canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    q = sorted((k, v) for k, v in parse_qsl(p.query) if not _TRACK.match(k))
    return urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), urlencode(q), ""))


def dedupe_key(company: str, title: str) -> str:
    return f"{norm_text(company)}|{norm_text(title)}"


def save_if_new(j: JobPosting) -> Job | None:
    """Insert job unless (company,title) or canonical URL already exists. Returns Job or None if duplicate.
    (The job search graph uses jobsearch.store, which does the same with INSERT ... ON CONFLICT and records sightings.)"""
    cu, dk = canonical_url(j.url), dedupe_key(j.company, j.title)
    with SessionLocal() as s:
        if s.scalar(select(Job).where((Job.dedupe_key == dk) | (Job.canonical_url == cu))): return None
        job = Job(id=hashlib.sha1(cu.encode()).hexdigest()[:16], source=j.source, company=j.company, title=j.title,
                  location=j.location, url=j.url, canonical_url=cu, dedupe_key=dk, jd_text=j.jd_text,
                  is_easy_apply=j.is_easy_apply, discovered_at=j.discovered_at, employment_type=j.employment_type,
                  posted_at=j.posted_at, is_remote=bool(j.is_remote), last_seen_at=j.discovered_at,
                  job_type=classify_job_type(j.title, j.employment_type, j.url))
        s.add(job); s.commit(); return job
