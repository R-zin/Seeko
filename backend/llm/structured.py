"""Tiered structured output: json_schema -> json_object -> prompt + regex + validation."""
from __future__ import annotations
import json, re
from typing import TypeVar
from openai import APIStatusError, BadRequestError
from pydantic import BaseModel, ValidationError
from .client import LLMClient

T = TypeVar("T", bound=BaseModel)
_tier_cache: dict[tuple, str] = {}  # (base_url, model) -> tier that worked
last_tier: str | None = None


def extract_json(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ValueError("no JSON found")
    open_c = text[start]; close_c = "}" if open_c == "{" else "]"
    depth = 0; in_str = False; esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            esc = (c == "\\" and not esc)
            if c == '"' and not esc: in_str = False
            continue
        if c == '"': in_str = True
        elif c == open_c: depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0: return text[start:i + 1]
    raise ValueError("unbalanced JSON")


def _unsupported(e: Exception) -> bool:
    return isinstance(e, APIStatusError) and e.status_code in (400, 422)


async def complete_json(llm: LLMClient, schema: type[T], messages: list[dict],
                        repair_retries: int = 2, **kw) -> T:
    global last_tier
    key = (llm.base_url, llm.model)
    sch = schema.model_json_schema()
    instr = ("Respond with ONLY a JSON object matching this JSON Schema, no prose, no markdown:\n"
             + json.dumps(sch))
    tiers = ["json_schema", "json_object", "prompt"]
    if key in _tier_cache:
        tiers = tiers[tiers.index(_tier_cache[key]):]
    last_err: Exception | None = None
    for tier in tiers:
        msgs = list(messages)
        rf = None
        if tier == "json_schema":
            rf = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": sch, "strict": False}}
        elif tier == "json_object":
            rf = {"type": "json_object"}
            msgs = [{"role": "system", "content": instr}] + msgs
        else:
            msgs = [{"role": "system", "content": instr}] + msgs
        attempts = 1 + (repair_retries if tier != "json_schema" else repair_retries)
        for _ in range(attempts):
            try:
                extra = {"response_format": rf} if rf else {}
                text = await llm.chat(msgs, **extra, **kw)
            except Exception as e:
                if _unsupported(e) and tier != "prompt":
                    last_err = e
                    break  # drop to next tier
                raise
            try:
                obj = schema.model_validate_json(extract_json(text))
                _tier_cache[key] = tier; last_tier = tier
                return obj
            except (ValueError, ValidationError) as ve:
                last_err = ve
                msgs = msgs + [{"role": "assistant", "content": text},
                               {"role": "user", "content": f"Invalid output: {ve}. Return corrected JSON only."}]
        else:
            continue
    raise RuntimeError(f"structured output failed: {last_err}")
