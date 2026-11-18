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


def test_search_api():
    with TestClient(app) as c:
        r = c.get("/api/search/runs")
        assert r.status_code == 200 and r.json()["running"] is False and isinstance(r.json()["runs"], list)
        assert c.get("/api/search/runs/nope").status_code == 404
        assert c.post("/api/search/runs", json={"sources": ["myspace"]}).status_code == 422
        assert c.post("/api/search/runs/nope/resume").status_code == 404
        assert c.get("/api/jobs?job_type=internship&q=50%25_off").status_code == 200
        s = c.get("/api/settings").json()
        assert s["search_max_details"] >= 1 and s["search_respect_robots"] is True and "indeed_host" in s
        assert c.put("/api/settings", json={"search_max_details": 0}).status_code == 422
        st = c.get("/api/pipeline/status").json()
        assert st["searching"] is False


def test_export_and_description_search():
    import csv, io
    from db import Job, SessionLocal
    with TestClient(app) as c:
        with SessionLocal() as s:
            s.merge(Job(id="exp1", source="lever", company="Acme, Inc.", title="Platform Engineer", url="https://x.test/1",
                        canonical_url="https://x.test/1", dedupe_key="acme|platform engineer",
                        jd_text="Line one, with comma\nLine two: Terraform", job_type="job")); s.commit()
        r = c.get("/api/jobs/export?format=csv")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        rows = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
        row = next(x for x in rows if x["id"] == "exp1")
        assert row["jd_text"] == "Line one, with comma\nLine two: Terraform" and row["company"] == "Acme, Inc."
        js = c.get("/api/jobs/export?format=json&q=terraform").json()
        assert [x["id"] for x in js] == ["exp1"]
        assert [x["id"] for x in c.get("/api/jobs?q=terraform").json()] == ["exp1"]
        assert c.get("/api/jobs?state=open").status_code == 200 and c.get("/api/jobs?state=nope").status_code == 422
        assert c.post("/api/search/cancel").status_code == 409
