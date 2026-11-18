"""Deterministic screening: job vs internship, remote, title relevance, location, freshness. Pure functions."""
from __future__ import annotations
import math
import re
from datetime import datetime, timedelta
from .extract import parse_date

_INTERN = re.compile(r"\b(intern|interns|internship|internships|trainee|trainees|apprentice|apprenticeship|co-?op|"
                     r"working student|summer analyst|summer associate|industrial training)\b", re.I)
_REMOTE = re.compile(r"\b(remote|work from home|work-from-home|wfh|telecommute|anywhere|fully distributed)\b", re.I)


def classify_job_type(title: str, employment_type: str = "", url: str = "") -> str:
    """'internship' when the posting says so (schema.org INTERN, the title, or an /internship/ URL), else 'job'."""
    if "intern" in (employment_type or "").lower():
        return "internship"
    if _INTERN.search(title or ""):
        return "internship"
    if re.search(r"/internships?/", url or "", re.I):
        return "internship"
    return "job"


def detect_remote(location: str = "", title: str = "", location_type: str = "") -> bool:
    if "telecommute" in (location_type or "").lower():
        return True
    return bool(_REMOTE.search(location or "") or _REMOTE.search(title or ""))


# ── Title relevance ─────────────────────────────────────────────────────────────────────────────────────────────
_PHRASES = [
    (r"\bsoftware development engineer\b", "software engineer"), (r"\bsde\b", "software engineer"),
    (r"\bswe\b", "software engineer"), (r"\bmachine learning\b", "ml"), (r"\bartificial intelligence\b", "ai"),
    (r"\bfront[\s-]?end\b", "frontend"), (r"\bback[\s-]?end\b", "backend"), (r"\bfull[\s-]?stack\b", "fullstack"),
    (r"\bdata science\b", "data scientist"), (r"\bdev\s?ops\b", "devops"), (r"\bsite reliability\b", "sre"),
    (r"\bquality assurance\b", "qa"), (r"\bui\s*/\s*ux\b", "ux"), (r"\buser experience\b", "ux"),
    (r"\bproduct manage(r|ment)\b", "pm product"), (r"\bnode\.?js\b", "node"), (r"\breact\.?js\b", "react"),
]
_TOKEN_MAP = {"developer": "engineer", "developers": "engineer", "engineers": "engineer", "engineering": "engineer",
              "programmer": "engineer", "dev": "engineer", "scientists": "scientist", "analysts": "analyst",
              "analytics": "analyst", "designers": "designer", "managers": "manager"}
_STOP = {"senior", "sr", "junior", "jr", "lead", "principal", "staff", "head", "chief", "i", "ii", "iii", "iv", "v",
         "level", "the", "a", "an", "of", "and", "for", "to", "in", "at", "with", "on", "or", "intern", "interns",
         "internship", "internships", "trainee", "apprentice", "entry", "associate", "mid", "new", "grad", "graduate",
         "fresher", "freshers", "remote", "hybrid", "onsite", "full", "time", "part", "contract", "temporary",
         "summer", "winter", "fall", "spring", "co", "op", "working", "student", "job", "role", "position", "opening",
         "hiring", "urgent", "f", "m", "d", "x", "w"} | {str(y) for y in range(2020, 2031)} | {str(n) for n in range(10)}


def core_tokens(title: str) -> list[str]:
    t = (title or "").lower()
    for pat, rep in _PHRASES:
        t = re.sub(pat, rep, t)
    toks = re.findall(r"[a-z0-9+#]+", t)
    out = []
    for tok in toks:
        tok = _TOKEN_MAP.get(tok, tok)
        if tok not in _STOP and tok not in out:
            out.append(tok)
    return out


def title_relevance(title: str, role_titles: list[str]) -> float:
    """Best fraction of a target role's core words present in the posting title (0..1). No targets -> 1."""
    if not role_titles:
        return 1.0
    have = set(core_tokens(title))
    best = 0.0
    for role in role_titles:
        want = core_tokens(role)
        if want:
            best = max(best, len(have & set(want)) / len(want))
    return best


def title_matches(title: str, role_titles: list[str]) -> bool:
    """All words of 1-2 word targets, at least two thirds of longer ones. 'Software Engineer' does not match
    'Sales Engineer'; it does match 'Senior Software Development Engineer II' and 'Software Engineering Intern'."""
    if not role_titles:
        return True
    have = set(core_tokens(title))
    for role in role_titles:
        want = core_tokens(role)
        if not want:
            continue
        need = len(want) if len(want) <= 2 else math.ceil(len(want) * 2 / 3)
        if len(have & set(want)) >= need:
            return True
    return False


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    low = (text or "").lower()
    return [k for k in keywords if re.search(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z0-9])", low)]


def relevance(title: str, jd_text: str, role_titles: list[str], keywords: list[str]) -> float:
    t = title_relevance(title, role_titles)
    k = len(keyword_hits(f"{title}\n{jd_text}", keywords)) / len(keywords) if keywords else 1.0
    return round(0.7 * t + 0.3 * k, 3)


# ── Location ────────────────────────────────────────────────────────────────────────────────────────────────────
_LOC_ALIASES = {"bangalore": "bengaluru", "bombay": "mumbai", "gurgaon": "gurugram", "calcutta": "kolkata",
                "madras": "chennai", "trivandrum": "thiruvananthapuram", "cochin": "kochi", "ernakulam": "kochi",
                "calicut": "kozhikode", "nyc": "new york", "sf": "san francisco", "bay area": "san francisco",
                "usa": "united states", "us": "united states", "uk": "united kingdom", "ncr": "delhi",
                "new delhi": "delhi", "wfh": "remote", "work from home": "remote"}


def _norm_loc(s: str) -> str:
    s = " " + re.sub(r"[^a-z0-9]+", " ", (s or "").lower()) + " "
    for a, b in _LOC_ALIASES.items():
        s = s.replace(f" {a} ", f" {b} ")
    return s


def location_ok(location: str, remote: bool, wanted: list[str], remote_only: bool) -> bool:
    if remote_only:
        return remote
    places = [w for w in wanted if _norm_loc(w).strip() != "remote"]
    if remote and len(places) < len(wanted):     # "Remote" is one of the wanted locations
        return True
    if not places:
        return not wanted or remote              # only "Remote" wanted -> must be remote; nothing wanted -> anything
    if not (location or "").strip():
        return True                              # unknown location: keep it rather than guess
    have = _norm_loc(location)
    return any(_norm_loc(p) in have for p in places)


# ── Freshness ───────────────────────────────────────────────────────────────────────────────────────────────────
def freshness_problem(posted_at: datetime | None, valid_through: datetime | None, max_age_days: int,
                      now: datetime) -> str | None:
    if valid_through and valid_through < now:
        return "expired"
    if max_age_days and posted_at and posted_at < now - timedelta(days=max_age_days):
        return "too_old"
    return None


def is_excluded(company: str, excluded: list[str]) -> bool:
    from scraper.dedupe import norm_text
    c = norm_text(company or "")
    return bool(c) and any(norm_text(x) == c for x in excluded)


MIN_JD_CHARS = 200


def screen(p: dict, spec: dict, now: datetime) -> tuple[bool, str, dict]:
    """Decide whether a fetched posting is kept. Returns (keep, reason, enriched posting)."""
    p = dict(p)
    p["job_type"] = classify_job_type(p.get("title", ""), p.get("employment_type", ""), p.get("url", ""))
    if p.get("is_remote") is None:
        p["is_remote"] = detect_remote(p.get("location", ""), p.get("title", ""), p.get("location_type", ""))
    p["relevance"] = relevance(p.get("title", ""), p.get("jd_text", ""), spec.get("role_titles", []), spec.get("keywords", []))
    if not (p.get("title") or "").strip():
        return False, "no_title", p
    if len((p.get("jd_text") or "").strip()) < MIN_JD_CHARS:
        return False, "no_description", p
    if is_excluded(p.get("company", ""), spec.get("excluded_companies", [])):
        return False, "excluded_company", p
    if p["job_type"] not in spec.get("job_types", ["job", "internship"]):
        return False, f"unwanted_{p['job_type']}", p
    if not title_matches(p["title"], spec.get("role_titles", [])):
        return False, "irrelevant_title", p
    if not location_ok(p.get("location", ""), p["is_remote"], spec.get("locations", []), spec.get("remote_only", False)):
        return False, "location", p
    stale = freshness_problem(parse_date(p.get("posted_at")), parse_date(p.get("valid_through")), spec.get("max_age_days", 0), now)
    if stale:
        return False, stale, p
    return True, "", p
