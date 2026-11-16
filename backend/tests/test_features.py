from config import settings as cfg
from workers import pipeline as P
from tailor import cover_letter as CL
from tailor.cover_letter import Letter


def test_discovery_due_respects_interval(monkeypatch):
    monkeypatch.setattr(P, "get_settings", lambda: type("S", (), {"discovery_interval_min": 0})())
    assert not P.discovery_due(10_000)                                     # 0 = manual only
    monkeypatch.setattr(P, "get_settings", lambda: type("S", (), {"discovery_interval_min": 30})())
    P.state["last_discovery"] = 1000.0
    assert not P.discovery_due(1000 + 29 * 60) and P.discovery_due(1000 + 30 * 60)


async def test_cover_letter_saved_and_grounded_prompt(tmp_path, monkeypatch):
    seen = {}
    async def fake(llm, schema, msgs, **kw): seen["sys"] = msgs[0]["content"]; seen["user"] = msgs[1]["content"]; return Letter(letter="Dear team")
    monkeypatch.setattr(CL, "complete_json", fake); monkeypatch.setattr(CL, "ARTIFACTS_DIR", tmp_path)
    out = await CL.generate_cover_letter("j1", "Eng", "Acme", "build apis", llm=object(), resume="RESUME-TEXT")
    assert out == "Dear team" and (tmp_path / "j1" / "cover_letter.txt").read_text() == "Dear team"
    assert "ONLY facts" in seen["sys"] and "RESUME-TEXT" in seen["user"]
