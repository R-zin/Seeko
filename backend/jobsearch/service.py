"""Running the job search graph: one run at a time, checkpointed in PostgreSQL so an interrupted run can resume."""
from __future__ import annotations
import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator
from langgraph.checkpoint.memory import InMemorySaver
import db
from .graph import Deps, build_graph
from .spec import SearchSpec


class RunInProgress(RuntimeError):
    pass


_lock = asyncio.Lock()
_memory_saver = InMemorySaver()          # SQLite / tests: checkpoints live for the life of the process
_pg_setup_done = False
_background: set[asyncio.Task] = set()   # strong refs: the event loop only keeps weak ones


def is_running() -> bool:
    return _lock.locked() or any(not t.done() for t in _background)


def spawn(coro) -> asyncio.Task:
    t = asyncio.get_running_loop().create_task(coro)
    _background.add(t); t.add_done_callback(_background.discard)
    return t


def checkpoint_conn_string() -> str | None:
    url = db.get_engine().url
    if url.get_backend_name() != "postgresql":
        return None
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@asynccontextmanager
async def checkpointer() -> AsyncIterator[object]:
    """AsyncPostgresSaver on PostgreSQL (tables created once per process), in-memory otherwise."""
    global _pg_setup_done
    conn = checkpoint_conn_string()
    if conn is None:
        yield _memory_saver
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    async with AsyncPostgresSaver.from_conn_string(conn) as saver:
        if not _pg_setup_done:
            await saver.setup()
            _pg_setup_done = True
        yield saver


def _result(values: dict, run_id: str) -> dict:
    return {"run_id": values.get("run_id", run_id), "new_job_ids": values.get("new_job_ids") or [],
            "stats": values.get("stats") or {}}


async def _invoke(inputs: dict | None, run_id: str, d: Deps) -> dict:
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 40}
    async with checkpointer() as saver:
        app = build_graph(d).compile(checkpointer=saver)
        if inputs is None:
            snap = await app.aget_state(config)
            if not snap.values:
                raise LookupError(f"no checkpoint for run {run_id}")
            if not snap.next:
                return _result(snap.values, run_id)        # already finished
            await asyncio.to_thread(d.store.set_status, run_id, "running")
        try:
            out = await app.ainvoke(inputs, config)
        except asyncio.CancelledError:
            await asyncio.to_thread(d.store.finish_run, run_id, "interrupted", None, "cancelled")
            raise
        except Exception as e:
            await asyncio.to_thread(d.store.finish_run, run_id, "failed", None, f"{type(e).__name__}: {e}")
            d.log(f"job search {run_id} failed: {e}", "error")
            raise
    return _result(out, run_id)


async def run_search(spec: SearchSpec | None = None, *, run_id: str | None = None, trigger: str = "manual",
                     deps: Deps | None = None) -> dict:
    """Run a complete search. Raises RunInProgress when another run holds the browser profiles."""
    if _lock.locked():
        raise RunInProgress("a job search is already running")
    async with _lock:
        d = deps or Deps()
        run_id = run_id or uuid.uuid4().hex[:12]
        spec = spec or SearchSpec.from_profile()
        return await _invoke({"run_id": run_id, "spec": spec.model_dump(mode="json"), "trigger": trigger}, run_id, d)


async def resume_run(run_id: str, deps: Deps | None = None) -> dict:
    """Continue an interrupted or failed run from its last checkpoint (completed steps are not repeated)."""
    if _lock.locked():
        raise RunInProgress("a job search is already running")
    async with _lock:
        return await _invoke(None, run_id, deps or Deps())


def start_background(spec: SearchSpec | None = None, trigger: str = "api", run_id: str | None = None) -> str:
    """Fire-and-forget run for the API; returns the run id immediately."""
    if is_running():
        raise RunInProgress("a job search is already running")
    run_id = run_id or uuid.uuid4().hex[:12]

    async def go():
        try:
            await run_search(spec, run_id=run_id, trigger=trigger)
        except RunInProgress:
            db.log("job search not started: another run is in progress", "warn")
        except Exception:
            pass   # already recorded on the run and in the log
    spawn(go())
    return run_id
