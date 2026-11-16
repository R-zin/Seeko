import httpx, pytest
from pydantic import BaseModel
from llm.client import LLMClient
from llm import structured
from tests.mock_llm import make_app
from main import app as main_app
from config.settings import update_settings


class Ping(BaseModel):
    ok: bool


def client_for(app, key="k", model="mock-model"):
    llm = LLMClient("http://mock/v1", key, model, {})
    llm.sdk._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock/v1")
    return llm


def reset(): structured._tier_cache.clear()


async def test_json_schema_tier():
    reset(); app = make_app("ok"); r = await structured.complete_json(client_for(app), Ping, [{"role": "user", "content": "x"}])
    assert r.ok and structured.last_tier == "json_schema"


async def test_falls_back_to_json_object():
    reset(); app = make_app("no_json_schema"); r = await structured.complete_json(client_for(app), Ping, [{"role": "user", "content": "x"}])
    assert r.ok and structured.last_tier == "json_object"
    assert [c.get("response_format", {}).get("type") for c in app.state.calls][:2] == ["json_schema", "json_object"]


async def test_falls_back_to_prompt_with_regex():
    reset(); app = make_app("no_response_format"); r = await structured.complete_json(client_for(app), Ping, [{"role": "user", "content": "x"}])
    assert r.ok and structured.last_tier == "prompt"


async def test_repair_retry():
    reset(); app = make_app("garbled_then_ok"); r = await structured.complete_json(client_for(app), Ping, [{"role": "user", "content": "x"}])
    assert r.ok and len(app.state.calls) >= 2


def test_blank_config_normalization():
    c = LLMClient("", "", "m", {})
    assert c.base_url is None and c.api_key == "not-needed"


async def _run_test_endpoint(app, **settings):
    from api import llm as api
    update_settings(llm_base_url="http://mock/v1", **settings)
    real = LLMClient.from_settings
    def patched(s=None, task=None):
        c = real(s, task); c.sdk._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock/v1"); return c
    LLMClient.from_settings = staticmethod(patched)
    try:
        return await api.test_endpoint()
    finally:
        LLMClient.from_settings = staticmethod(real)


async def test_endpoint_ok():
    reset(); out = await _run_test_endpoint(make_app("ok", api_key="k"), llm_api_key="k", llm_model="mock-model")
    assert out["ok"] and out["checks"]["connectivity"]["latency_ms"] >= 0


async def test_endpoint_401():
    reset(); out = await _run_test_endpoint(make_app("ok", api_key="right"), llm_api_key="wrong", llm_model="mock-model")
    assert not out["ok"] and "401" in out["checks"]["connectivity"]["error"]


async def test_endpoint_unknown_model_404():
    reset(); out = await _run_test_endpoint(make_app("ok"), llm_api_key="k", llm_model="nope")
    assert not out["ok"] and "404" in out["checks"]["connectivity"]["error"]
