"""Readiness check: what still stands between you and a running automation, and how to fix each item.

Levels: "error" stops the pipeline (nothing useful happens until it is fixed), "warn" limits it, "ok" / "info" are
fine. Used by GET /api/doctor, `python -m jobsearch doctor` and the Setup checklist. Checks are cheap and offline
(no LLM or Notion request): they report what is configured, not whether a remote service answers right now.
"""
from __future__ import annotations
import os
import shutil
import time
from dataclasses import asdict, dataclass
from config.profile import RESUME_PATH, load_profile
from config.settings import get_settings


@dataclass
class Check:
    key: str
    title: str
    level: str            # ok | info | warn | error
    detail: str = ""
    fix: str = ""
    where: str = ""       # UI tab the fix lives on

    @property
    def ok(self) -> bool:
        return self.level in ("ok", "info")


_browser_cache: tuple[float, bool, str] | None = None


async def chromium_ready() -> tuple[bool, str]:
    """Is Playwright's Chromium installed? (Starts the driver once; cached for 5 minutes.)"""
    global _browser_cache
    if _browser_cache and time.monotonic() - _browser_cache[0] < 300:
        return _browser_cache[1], _browser_cache[2]
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            path = p.chromium.executable_path
        ok, detail = os.path.exists(path), path
    except Exception as e:   # driver missing or broken
        ok, detail = False, f"{type(e).__name__}: {e}"[:200]
    _browser_cache = (time.monotonic(), ok, detail)
    return ok, detail


async def run_checks() -> list[Check]:
    st, prof = get_settings(), load_profile()
    out: list[Check] = []
    add = lambda *a, **k: out.append(Check(*a, **k))

    # Model
    if st.llm_base_url.strip() or st.llm_api_key.strip():
        add("llm", "Language model", "ok", f"{st.llm_model} at {st.llm_base_url or 'api.openai.com'}")
    else:
        add("llm", "Language model", "error", "No endpoint configured: nothing is scored, tailored or filled.",
            "Setup → Model: base URL, key and model, then Test endpoint.", "setup")

    # Résumé + LaTeX
    if RESUME_PATH.exists() and RESUME_PATH.read_text().strip():
        add("resume", "Base résumé", "ok", f"{RESUME_PATH.name} saved")
    else:
        add("resume", "Base résumé", "error", "No base résumé saved: there is nothing to tailor or attach.",
            "Setup → Résumé: upload a .tex, .pdf or Overleaf .zip and Save as base.", "setup")
    from resume.compiler import available_engines
    engines = available_engines()
    if engines:
        add("latex", "LaTeX compiler", "ok", ", ".join(engines))
    else:
        add("latex", "LaTeX compiler", "error", "No tectonic / pdflatex / xelatex on PATH: tailored résumés cannot be compiled.",
            "Install tectonic (single binary) or TeX Live, then restart Seeko.")

    # Browser
    ok, detail = await chromium_ready()
    add("browser", "Chromium for Playwright", "ok" if ok else "error", detail if ok else
        f"Not installed ({detail}): job pages cannot be read and forms cannot be filled.",
        "" if ok else "cd backend && .venv/bin/playwright install chromium")

    # Profile
    p = prof.personal
    missing = [n for n, v in (("name", p.name), ("email", p.email), ("phone", p.phone)) if not v.strip()]
    if missing:
        add("profile", "Contact details", "error" if {"name", "email"} & set(missing) else "warn",
            f"Missing {', '.join(missing)}: application forms ask for these first.", "Setup → Profile.", "setup")
    else:
        add("profile", "Contact details", "ok", f"{p.name} · {p.email}")
    if prof.search.role_titles:
        add("roles", "Target roles", "ok", ", ".join(prof.search.role_titles[:6]))
    else:
        add("roles", "Target roles", "warn", "No target roles: every title found counts as a match.",
            "Setup → Profile → Search: add the roles you want.", "setup")

    # Sources
    sources = [s for s in st.enabled_sources if s]
    boards = [s for s in sources if s != "watchlist"]
    if not sources:
        add("sources", "Job sources", "error", "No source enabled: discovery has nowhere to look.",
            "Setup → Sources: enable career pages or a job board.", "setup")
    elif sources == ["watchlist"] and not prof.search.watchlist_urls:
        add("sources", "Job sources", "error", "Only career pages are enabled and the watchlist is empty.",
            "Setup → Profile: add company career page URLs, or enable a job board.", "setup")
    else:
        add("sources", "Job sources", "ok", ", ".join(
            ([f"{len(prof.search.watchlist_urls)} career pages"] if "watchlist" in sources else []) + boards))

    # Answer bank
    from applier.answers import pending_count
    n = pending_count()
    if n:
        add("answers", "Form questions", "warn", f"{n} question(s) from application forms are waiting for your answer.",
            "Answer them in the job review or Setup → Answers; jobs waiting on them are filled again.", "setup")

    # Mode
    if st.approval_mode:
        add("mode", "Submission mode", "info", "Approval gate: every application waits for your approval.")
    else:
        add("mode", "Submission mode", "warn", f"Full-auto: up to {st.daily_apply_cap} applications a day are submitted without review.",
            "Pipeline / Tracker → Controls to switch back to the approval gate.", "tracker")

    # Optional integrations
    if st.notion_token.strip() and st.notion_database_id.strip():
        if not shutil.which("npx"):
            add("notion", "Notion", "error", "Notion is configured but `npx` (Node.js) is not on PATH, so its MCP server cannot start.",
                "Install Node.js 20+.")
        else:
            from workers.notion_service import service
            err = service.state.get("last_error")
            add("notion", "Notion", "warn" if err else "ok", err or ("connected" if service.state.get("connected") else "configured"),
                "Setup → Notion → Save & test." if err else "", "setup")
    else:
        add("notion", "Notion", "info", "Not connected (optional).", "Setup → Notion.", "setup")
    if st.backup_database_url.strip():
        from backup import service as backup
        err = backup.state.get("last_error")
        add("backup", "Backup database", "warn" if err else "ok", err or f"last copy {backup.state.get('last_sync_at') or 'pending'}",
            "Check BACKUP_DATABASE_URL and that the server is running." if err else "")
    else:
        add("backup", "Backup database", "info", "Off (optional).", "Set BACKUP_DATABASE_URL, e.g. docker compose up -d db-backup.")
    if st.notify_webhook_url.strip():
        add("notify", "Notifications", "ok", "webhook set")
    else:
        add("notify", "Notifications", "info", "Off: you only see reviews waiting when you open Seeko.",
            "Setup → Notifications: paste a Slack, Discord or ntfy webhook.", "setup")
    return out


async def report() -> dict:
    checks = await run_checks()
    errors = [c for c in checks if c.level == "error"]
    return {"ready": not errors, "errors": len(errors), "warnings": sum(c.level == "warn" for c in checks),
            "checks": [asdict(c) | {"ok": c.ok} for c in checks]}
