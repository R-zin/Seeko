from pathlib import Path
import pytest
from tailor import tailor as T
from tailor.latex import compile_tex, page_count
from config import settings as cfg

BASE = (Path(__file__).parent / "fixtures" / "resume.tex").read_text()


class FakeLLM:
    """Returns scripted TailorOut JSON strings through complete_json by stubbing it."""
    base_url = None; model = "fake"


@pytest.fixture(autouse=True)
def tmp_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(T, "load_profile", lambda: type("P", (), {"model_dump_json": lambda s: "{}"})())


def script(monkeypatch, outputs):
    it = iter(outputs)
    async def fake(llm, schema, msgs, **kw):
        o = next(it); return T.TailorOut(tex=o, summary="s")
    monkeypatch.setattr(T, "complete_json", fake)


async def test_base_compiles_one_page(tmp_path):
    ok, log = await compile_tex(BASE, tmp_path / "b.pdf")
    assert ok, log; assert page_count(tmp_path / "b.pdf") == 1


async def test_good_tailor(monkeypatch):
    good = BASE.replace("Backend engineer with experience building APIs", "Backend engineer building scalable APIs")
    script(monkeypatch, [good])
    r = await T.tailor_resume("j1", "jd", "Eng", "Co", llm=FakeLLM(), base=BASE)
    assert not r.fell_back and r.page_count == 1 and "scalable" in r.diff


async def test_overflow_triggers_condense(monkeypatch):
    long = BASE.replace(r"\end{itemize}", "".join(r"\item Built REST APIs in Python serving 1M requests per day." + "\n" for _ in range(90)) + r"\end{itemize}")
    script(monkeypatch, [long, BASE.replace("APIs and", "APIs plus")])
    r = await T.tailor_resume("j2", "jd", "Eng", "Co", llm=FakeLLM(), base=BASE)
    assert not r.fell_back and r.page_count == 1


async def test_persistent_failure_falls_back(monkeypatch):
    bad = BASE.replace(r"\end{itemize}", r"\item \badmacro{x}" + "\n" + r"\end{itemize}", 1)
    script(monkeypatch, [bad, bad, bad])
    r = await T.tailor_resume("j3", "jd", "Eng", "Co", llm=FakeLLM(), base=BASE)
    assert r.fell_back and Path(r.pdf_path).exists()


def test_guard_rejects_invented_employer_and_tool():
    assert T.validate(BASE, BASE.replace("Acme Corp", "Google")), "invented employer must be rejected"
    errs = T.validate(BASE, BASE.replace("Docker", "Docker, Kubernetes"))
    assert any("Kubernetes" in e for e in errs)
    assert not T.validate(BASE, BASE)


def test_guard_rejects_preamble_and_section_change():
    assert "preamble changed" in T.validate(BASE, BASE.replace("11pt", "12pt"))
    assert "section set/order changed" in T.validate(BASE, BASE.replace("{Skills}", "{Abilities}"))
