from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from db import init_db
from api import llm as llm_api

DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    import asyncio
    from workers import pipeline
    pipeline.recover_orphans()
    from jobsearch.store import Store
    if n := Store().mark_interrupted():
        from db import log
        log(f"{n} job search run(s) were interrupted by a restart; resume them from the Tracker or `python -m jobsearch resume`", "warn")
    from workers.notion_service import service as notion
    from backup import service as backup
    import agenda
    tasks = [asyncio.create_task(pipeline.loop()), asyncio.create_task(notion.run()), asyncio.create_task(backup.run()),
             asyncio.create_task(agenda.run())]
    yield
    for t in tasks: t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await pipeline.shutdown()


app = FastAPI(title="Seeko", lifespan=lifespan)
app.include_router(llm_api.router)

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def origin_allowed(origin: str) -> bool:
    """Browsers send Origin on cross-site writes. Only Seeko's own pages (local, or APP_URL) may change things."""
    from urllib.parse import urlsplit
    from config.settings import get_settings
    try:
        o = urlsplit(origin)
        own = urlsplit(get_settings().app_url)
    except ValueError:
        return False
    return o.scheme in ("http", "https") and (o.hostname in LOCAL_HOSTS or (o.scheme, o.netloc) == (own.scheme, own.netloc))


@app.middleware("http")
async def block_cross_site_writes(request, call_next):
    """CSRF guard for a local app without logins: a page on another site must not be able to submit applications
    (e.g. POST /api/pipeline/approve-ready) or change settings through your browser. Non-browser clients send no Origin."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        cross = request.headers.get("sec-fetch-site") == "cross-site"
        if (origin and not origin_allowed(origin)) or (cross and not origin):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
    return await call_next(request)


@app.get("/api/health")
def health(): return {"ok": True}

# routers added as modules land (see register() calls below)
def _register():
    for name in ("settings", "resume", "jobs", "pipeline", "search", "notion", "backup", "doctor", "answers", "notify",
                 "agenda", "logs"):
        try:
            mod = __import__(f"api.{name}", fromlist=["router"])
            app.include_router(mod.router)
        except ModuleNotFoundError as e:
            if e.name != f"api.{name}": raise
_register()

if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
