"""Provider-agnostic wrapper over AsyncOpenAI. Only /chat/completions is ever used."""
from __future__ import annotations
from dataclasses import dataclass
from openai import AsyncOpenAI
from config.settings import Settings, get_settings

TASKS = {"scoring": "scoring_model", "tailor": "tailor_model", "applier": "applier_model"}


@dataclass
class LLMClient:
    base_url: str | None
    api_key: str
    model: str
    headers: dict[str, str]
    timeout: float = 120.0

    def __post_init__(self):
        self.base_url = (self.base_url or "").strip() or None  # empty -> None
        self.api_key = (self.api_key or "").strip() or "not-needed"
        self.sdk = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key,
                               default_headers=self.headers or None, timeout=self.timeout,
                               max_retries=1)

    @classmethod
    def from_settings(cls, s: Settings | None = None, task: str | None = None) -> "LLMClient":
        s = s or get_settings()
        model = s.llm_model
        if task:
            model = getattr(s, TASKS[task]) or s.llm_model  # inherit base url/key/model
        return cls(s.llm_base_url, s.llm_api_key, model, s.extra_headers())

    def for_task(self, task: str, s: Settings | None = None) -> "LLMClient":
        return LLMClient.from_settings(s, task)

    async def chat(self, messages: list[dict], **kw) -> str:
        r = await self.sdk.chat.completions.create(model=self.model, messages=messages, **kw)
        return r.choices[0].message.content or ""
