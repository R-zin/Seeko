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
    database_url: str = f"sqlite:///{ROOT / 'data' / 'seeko.db'}"

    def extra_headers(self) -> dict[str, str]:
        if not self.llm_extra_headers.strip():
            return {}
        try:
            v = json.loads(self.llm_extra_headers)
            return {str(k): str(x) for k, x in v.items()} if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}


OVERRIDES = DATA_DIR / "settings.json"   # UI edits persist here (git-ignored; holds secrets, chmod 600)


def _load() -> Settings:
    base = Settings()
    if OVERRIDES.exists():
        try: return base.model_copy(update=json.loads(OVERRIDES.read_text()))
        except (json.JSONDecodeError, OSError): pass
    return base


_settings = _load()


def get_settings() -> Settings:
    return _settings


def update_settings(**kw) -> Settings:
    global _settings
    _settings = _settings.model_copy(update={k: v for k, v in kw.items() if v is not None})
    if os.environ.get("SEEKO_NO_PERSIST") != "1":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        OVERRIDES.write_text(_settings.model_dump_json()); os.chmod(OVERRIDES, 0o600)
    return _settings
