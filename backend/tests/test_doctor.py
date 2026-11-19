"""Readiness check: blocking problems are errors with a fix; a configured install is ready."""
import pytest
from fastapi.testclient import TestClient
import doctor
from config.profile import CandidateProfile
from config.settings import get_settings


@pytest.fixture
def env(monkeypatch, tmp_path):
    async def chromium(): return True, "/opt/chromium"
    monkeypatch.setattr(doctor, "chromium_ready", chromium)
    monkeypatch.setattr("resume.compiler.available_engines", lambda: ["tectonic"])
    monkeypatch.setattr(doctor, "RESUME_PATH", tmp_path / "base_resume.tex")
    state = {"settings": get_settings(), "profile": CandidateProfile()}
    monkeypatch.setattr(doctor, "get_settings", lambda: state["settings"])
    monkeypatch.setattr(doctor, "load_profile", lambda: state["profile"])
    monkeypatch.setattr("applier.answers.pending_count", lambda *a, **k: 0)
    return state, tmp_path


async def test_fresh_install_lists_what_blocks_the_automation(env):
    state, _ = env
    state["settings"] = get_settings().model_copy(update={"llm_base_url": "", "llm_api_key": "", "enabled_sources": ["watchlist"]})
    rep = await doctor.report()
    errors = {c["key"]: c for c in rep["checks"] if c["level"] == "error"}
    assert not rep["ready"] and set(errors) == {"llm", "resume", "profile", "sources"}
    assert all(c["fix"] for c in errors.values())
    assert {c["key"]: c["level"] for c in rep["checks"]}["notion"] == "info"      # optional things never block


async def test_configured_install_is_ready(env, monkeypatch):
    state, tmp = env
    (tmp / "base_resume.tex").write_text(r"\documentclass{article}\begin{document}x\end{document}")
    state["settings"] = get_settings().model_copy(update={"llm_base_url": "http://localhost:11434/v1", "enabled_sources": ["watchlist", "linkedin"]})
    p = CandidateProfile(); p.personal.name, p.personal.email, p.personal.phone = "Asha", "a@x.test", "+91 1"
    p.search.role_titles = ["Backend Engineer"]
    state["profile"] = p
    rep = await doctor.report()
    assert rep["ready"] and rep["errors"] == 0, [c for c in rep["checks"] if c["level"] == "error"]
    monkeypatch.setattr("applier.answers.pending_count", lambda *a, **k: 2)
    assert {c["key"]: c["level"] for c in (await doctor.report())["checks"]}["answers"] == "warn"


async def test_missing_compiler_and_browser_are_errors(env, monkeypatch):
    async def no_chromium(): return False, "Executable doesn't exist"
    monkeypatch.setattr(doctor, "chromium_ready", no_chromium)
    monkeypatch.setattr("resume.compiler.available_engines", lambda: [])
    levels = {c["key"]: c["level"] for c in (await doctor.report())["checks"]}
    assert levels["latex"] == "error" and levels["browser"] == "error"


def test_doctor_endpoint():
    from main import app
    with TestClient(app) as c:
        r = c.get("/api/doctor").json()
        assert {"ready", "errors", "checks"} <= set(r) and any(x["key"] == "llm" for x in r["checks"])
