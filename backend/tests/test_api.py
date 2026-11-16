from fastapi.testclient import TestClient
from main import app


def test_api_smoke():
    with TestClient(app) as c:
        assert c.get("/api/health").json() == {"ok": True}
        assert c.get("/api/pipeline/status").json()["approval_mode"] is True      # safe default
        assert c.get("/api/jobs").status_code == 200 and c.get("/api/logs").status_code == 200
        assert c.get("/api/jobs/nope").status_code == 404
        assert c.post("/api/pipeline/jobs/nope/approve").status_code == 409
        assert c.post("/api/notion/test").json()["ok"] is False                   # no creds configured
        assert c.get("/api/settings").json()["notion_token"] in ("", "***")


def test_settings_persist_and_reload(tmp_path, monkeypatch):
    import json
    from config import settings as cfg
    monkeypatch.delenv("SEEKO_NO_PERSIST"); monkeypatch.setattr(cfg, "OVERRIDES", tmp_path / "s.json"); monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    old = cfg._settings
    try:
        cfg.update_settings(llm_model="persisted-model", approval_mode=False)
        assert json.loads((tmp_path / "s.json").read_text())["llm_model"] == "persisted-model"
        assert cfg._load().llm_model == "persisted-model" and cfg._load().approval_mode is False
    finally:
        cfg._settings = old
