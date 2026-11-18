"""App settings: env/.env defaults, overridable at runtime from the DB (UI)."""
from __future__ import annotations
import json, os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
ARTIFACTS_DIR = ROOT / "artifacts"
BROWSER_DIR = ROOT / "browser_data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_extra_headers: str = ""  # JSON string
    scoring_model: str = ""
    tailor_model: str = ""
    applier_model: str = ""

    notion_token: str = ""
    notion_database_id: str = ""

    approval_mode: bool = True          # approval gate by default
    daily_apply_cap: int = 10
    discovery_interval_min: int = 0     # 0 = manual only
    min_match_score: int = 60
    enabled_sources: list[str] = Field(default_factory=lambda: ["watchlist"])
    # Any SQLAlchemy URL (postgresql://… recommended). Empty = embedded PostgreSQL in data/postgres.
    # Deployment config: read from env/.env only, never from the UI overrides file.
    database_url: str = ""

    # Job search (LangGraph). Defaults are deliberately polite: few pages, jittered delays, bounded detail visits.
    search_max_queries: int = 6         # per board, per run (titles x locations x job types)
    search_max_pages: int = 3           # listing pages per query / careers page
    search_max_details: int = 25        # detail pages opened per source per run; the rest wait for the next run
    search_delay_min_s: float = 3.0
    search_delay_max_s: float = 8.0
    search_revisit_days: int = 14       # a page rejected after reading is not re-read for this long
    search_respect_robots: bool = True  # careers pages: obey robots.txt
    search_llm_review: bool = True      # LLM double-checks pages that had no structured job data
    search_headless: bool = True
    search_daily_pages: int = 80        # page loads per browsed source per rolling 24 h, across all runs
    search_block_cooldown_h: float = 12 # after a CAPTCHA / login wall / 429, leave that source alone this long
    indeed_host: str = "www.indeed.com"   # country site, e.g. in.indeed.com, uk.indeed.com

    def extra_headers(self) -> dict[str, str]:
        if not self.llm_extra_headers.strip():
            return {}
        try:
            v = json.loads(self.llm_extra_headers)
            return {str(k): str(x) for k, x in v.items()} if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}


OVERRIDES = DATA_DIR / "settings.json"   # UI edits persist here (git-ignored; holds secrets, chmod 600)
ENV_ONLY = {"database_url"}               # older settings.json files pinned the SQLite path; ignore it


def _load() -> Settings:
    base = Settings()
    if OVERRIDES.exists():
        try:
            data = {k: v for k, v in json.loads(OVERRIDES.read_text()).items() if k not in ENV_ONLY}
            return base.model_copy(update=data)
        except (json.JSONDecodeError, OSError, AttributeError): pass
    return base


_settings = _load()


def get_settings() -> Settings:
    return _settings


def update_settings(**kw) -> Settings:
    global _settings
    _settings = _settings.model_copy(update={k: v for k, v in kw.items() if v is not None})
    if os.environ.get("SEEKO_NO_PERSIST") != "1":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        OVERRIDES.write_text(_settings.model_dump_json(exclude=ENV_ONLY)); os.chmod(OVERRIDES, 0o600)
    return _settings
