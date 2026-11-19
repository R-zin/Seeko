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


def test_search_start_and_resume_endpoints():
    import time
    from jobsearch.store import Store

    def settle(c):
        for _ in range(100):
            if not c.get("/api/search/runs").json()["running"]: return
            time.sleep(0.05)
        raise AssertionError("search did not finish")

    with TestClient(app) as c:
        r = c.post("/api/search/runs", json={"sources": ["watchlist"], "role_titles": ["Nobody"]})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        settle(c)
        run = c.get(f"/api/search/runs/{run_id}").json()
        assert run["status"] == "done" and run["trigger"] == "api" and run["spec"]["role_titles"] == ["Nobody"]
        assert c.post(f"/api/search/runs/{run_id}/resume").status_code == 409      # finished runs are not resumed
        st = Store(); st.start_run("api-interrupted", {}, "api"); st.mark_interrupted()
        assert c.post("/api/search/runs/api-interrupted/resume").status_code == 202
        settle(c)
        logs = [x["message"] for x in c.get("/api/logs?level=warn").json()]
        assert any("api-interrupted" in m and "no checkpoint" in m for m in logs)


def test_tracking_patch_notion_and_backup_endpoints():
    from db import Job, SessionLocal
    with TestClient(app) as c:
        with SessionLocal() as s:
            s.merge(Job(id="trk1", source="lever", company="Acme", title="Engineer", url="https://x/trk1",
                        canonical_url="https://x/trk1", dedupe_key="acme|engineer-trk1")); s.commit()
        j = c.get("/api/jobs/trk1").json()
        assert j["application_status"] == "not_applied" and j["notion_state"] == "off" and j["next_step_date"] is None
        r = c.patch("/api/jobs/trk1", json={"application_status": "Interviewing", "next_step": "Onsite",
                                            "next_step_date": "2026-12-03", "notes": "bring portfolio"})
        assert r.status_code == 200
        j = r.json()
        assert (j["application_status"], j["next_step"], j["next_step_date"], j["notes"]) == ("interviewing", "Onsite", "2026-12-03", "bring portfolio")
        assert j["applied_at"] is None
        j = c.patch("/api/jobs/trk1", json={"application_status": "applied"}).json()
        assert j["application_status"] == "applied" and j["applied_at"]          # marking applied dates it
        j = c.patch("/api/jobs/trk1", json={"applied_at": "2026-11-02"}).json()
        assert j["applied_at"].startswith("2026-11-02") and j["notes"] == "bring portfolio"   # untouched fields stay
        assert c.patch("/api/jobs/trk1", json={"application_status": ""}).status_code == 422
        assert c.patch("/api/jobs/nope", json={"notes": "x"}).status_code == 404
        assert [x["id"] for x in c.get("/api/jobs?application_status=applied").json()] == ["trk1"]
        assert c.get("/api/jobs?application_status=offer").json() == []
        assert [x["key"] for x in c.get("/api/jobs/application-statuses").json()][:2] == ["not_applied", "applied"]
        st = c.get("/api/notion/status").json()
        assert st["configured"] is False and st["running"] is True and "Interviewing" in st["application_statuses"]
        assert c.post("/api/notion/setup").status_code == 400
        assert c.get("/api/backup").json()["configured"] is False and c.post("/api/backup/sync").status_code == 400
        assert c.get("/api/settings").json()["notion_pull_interval_s"] == 60


def test_cross_site_writes_are_refused():
    with TestClient(app) as c:
        evil = {"Origin": "https://jobs-are-great.example"}
        assert c.post("/api/pipeline/approve-ready", headers=evil).status_code == 403
        assert c.put("/api/settings", json={"approval_mode": False}, headers=evil).status_code == 403
        assert c.post("/api/pipeline/approve-ready", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert c.post("/api/pipeline/approve-ready", headers={"Origin": "http://evil.test:8000"}).status_code == 403  # DNS rebinding
        assert c.get("/api/jobs", headers=evil).status_code == 200                          # reading is not a write
        for ok in ("http://127.0.0.1:5173", "http://localhost:8000"):
            assert c.post("/api/pipeline/approve-ready", headers={"Origin": ok}).json() == {"ok": True, "approved": []}
        assert c.post("/api/pipeline/approve-ready").status_code == 200                     # CLI / scripts: no Origin
