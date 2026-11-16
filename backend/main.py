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
    task = asyncio.create_task(pipeline.loop())
    yield
    task.cancel(); await pipeline.shutdown()


app = FastAPI(title="Seeko", lifespan=lifespan)
app.include_router(llm_api.router)


@app.get("/api/health")
def health(): return {"ok": True}

# routers added as modules land (see register() calls below)
def _register():
    for name in ("settings", "resume", "jobs", "pipeline", "notion", "logs"):
        try:
            mod = __import__(f"api.{name}", fromlist=["router"])
            app.include_router(mod.router)
        except ModuleNotFoundError as e:
            if e.name != f"api.{name}": raise
_register()

if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
