"""Mock OpenAI-compatible server for tests. Mode controls response_format behavior."""
from __future__ import annotations
import json
from fastapi import FastAPI, Request, HTTPException, Header


def make_app(mode: str = "ok", api_key: str | None = None, models: tuple = ("mock-model",)) -> FastAPI:
    app = FastAPI(); app.state.calls = []

    @app.post("/v1/chat/completions")
    async def chat(req: Request, authorization: str | None = Header(None)):
        body = await req.json(); app.state.calls.append(body)
        if api_key and authorization != f"Bearer {api_key}":
            raise HTTPException(401, "bad key")
        if body["model"] not in models:
            raise HTTPException(404, "model not found")
        rf = (body.get("response_format") or {}).get("type")
        if mode == "no_json_schema" and rf == "json_schema":
            raise HTTPException(400, "response_format json_schema unsupported")
        if mode == "no_response_format" and rf:
            raise HTTPException(422, "response_format unsupported")
        text = '{"ok": true}'
        if mode == "fenced" or (mode == "no_response_format"):
            text = 'Sure! ```json\n{"ok": true}\n```'
        if mode == "garbled_then_ok":
            n = sum(1 for c in app.state.calls)
            text = "not json at all" if n < 2 else '{"ok": true}'
        if "pong" in json.dumps(body["messages"]): text = "pong"
        return {"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}
    return app
