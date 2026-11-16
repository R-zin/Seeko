from __future__ import annotations
import time
from fastapi import APIRouter
from openai import APIStatusError, APIConnectionError, AuthenticationError, NotFoundError
from pydantic import BaseModel
from config.settings import get_settings, update_settings
from llm.client import LLMClient
from llm import structured

router = APIRouter(prefix="/api/llm")


class Ping(BaseModel):
    ok: bool


class LLMSettings(BaseModel):
    llm_base_url: str | None = None; llm_api_key: str | None = None; llm_model: str | None = None
    llm_extra_headers: str | None = None; scoring_model: str | None = None
    tailor_model: str | None = None; applier_model: str | None = None


def diagnose(e: Exception, base_url: str | None, model: str) -> str:
    if isinstance(e, AuthenticationError) or (isinstance(e, APIStatusError) and e.status_code in (401, 403)):
        return "401/403: authentication failed - check LLM_API_KEY."
    if isinstance(e, NotFoundError) or (isinstance(e, APIStatusError) and e.status_code == 404):
        return (f"404: not found - the base URL path is probably wrong (most servers need a trailing /v1, got {base_url!r}) "
                f"or model {model!r} does not exist on this endpoint.")
    if isinstance(e, APIConnectionError):
        return f"Connection failed to {base_url or 'api.openai.com'} - is the server running and reachable?"
    return f"{type(e).__name__}: {e}"


@router.get("/settings")
def get_llm_settings():
    s = get_settings()
    return {"llm_base_url": s.llm_base_url, "llm_api_key": "***" if s.llm_api_key else "", "llm_model": s.llm_model,
            "llm_extra_headers": s.llm_extra_headers, "scoring_model": s.scoring_model,
            "tailor_model": s.tailor_model, "applier_model": s.applier_model}


@router.put("/settings")
def put_llm_settings(body: LLMSettings):
    d = body.model_dump()
    if d.get("llm_api_key") == "***": d["llm_api_key"] = None  # unchanged
    update_settings(**d)
    return {"ok": True}


@router.post("/test")
async def test_endpoint(task: str | None = None):
    llm = LLMClient.from_settings(task=task)
    out: dict = {"base_url": llm.base_url, "model": llm.model, "checks": {}}
    t0 = time.perf_counter()
    try:
        txt = await llm.chat([{"role": "user", "content": "Reply with the single word: pong"}], max_tokens=16)
        out["checks"]["connectivity"] = {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000), "reply": txt[:60]}
    except Exception as e:
        out["checks"]["connectivity"] = {"ok": False, "error": diagnose(e, llm.base_url, llm.model)}
        out["ok"] = False
        return out
    t1 = time.perf_counter()
    try:
        r = await structured.complete_json(llm, Ping, [{"role": "user", "content": 'Return {"ok": true}'}])
        out["checks"]["json_output"] = {"ok": bool(r.ok), "tier": structured.last_tier,
                                        "latency_ms": round((time.perf_counter() - t1) * 1000)}
    except Exception as e:
        out["checks"]["json_output"] = {"ok": False, "error": diagnose(e, llm.base_url, llm.model)}
    out["ok"] = all(c["ok"] for c in out["checks"].values())
    return out
