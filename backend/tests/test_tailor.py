import json
from pathlib import Path

import pytest

from tailor import tailor as T
from tailor.tailor import Edit, TailorEdits
from resume.compiler import compile_document, page_count

FX = Path(__file__).parent / "fixtures"
BASE = (FX / "resume.tex").read_text()
JAKE = (FX / "jake_resume.tex").read_text()


class FakeLLM:
    base_url = None; model = "fake"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(T, "ASSETS_DIR", tmp_path / "no-assets")
    monkeypatch.setattr(T, "load_profile", lambda: type("P", (), {"model_dump_json": lambda s: "{}"})())


def script(monkeypatch, *proposals):
    """Each proposal: dict id->text (+ optional summary). Returned in order by the fake model."""
    it = iter(proposals); calls = []
    async def fake(llm, schema, msgs, **kw):
        calls.append(msgs); p = dict(next(it))
        return TailorEdits(edits=[Edit(id=k, text=v) for k, v in p.items() if k != "_summary"], summary=p.get("_summary", "s"))
    monkeypatch.setattr(T, "complete_json", fake)
    return calls


async def run(base=BASE, job="j"):
    return await T.tailor_resume(job, "Python AWS APIs", "Backend Engineer", "Stripe", llm=FakeLLM(), base=base)


async def test_base_compiles_one_page(tmp_path):
    r = await compile_document(BASE, tmp_path / "b.pdf")
    assert r.ok and page_count(tmp_path / "b.pdf") == 1


async def test_good_edits_applied_and_everything_else_byte_identical(monkeypatch):
    script(monkeypatch, {"sum1": "Backend engineer building APIs and data pipelines in Python on AWS.",
                         "sk1": "AWS, Python, SQL, Docker"})
    r = await run()
    assert not r.fell_back and r.page_count == 1
    assert "on AWS." in r.tex and "AWS, Python, SQL, Docker" in r.tex
    assert r.tex.split(r"\begin{document}")[0] == BASE.split(r"\begin{document}")[0]
    assert len(r.report["accepted"]) == 2 and not r.report["rejected"]


async def test_invented_number_and_tool_rejected_but_good_edit_kept(monkeypatch):
    script(monkeypatch, {"b1": "Built REST APIs in Python serving 5M requests per day.",          # new number
                         "b2": "Reduced pipeline runtime by 40\\% using SQL and Kubernetes.",       # new tool
                         "sum1": "Backend engineer building Python APIs and data pipelines."})
    r = await run()
    reasons = {x["id"]: x["reason"] for x in r.report["rejected"]}
    assert "new numbers" in reasons["b1"] and "Kubernetes" in reasons["b2"]
    assert not r.fell_back and "building Python APIs" in r.tex and "1M requests" in r.tex


async def test_skills_may_reorder_but_not_add(monkeypatch):
    script(monkeypatch, {"sk1": "Python, SQL, Docker, AWS, Terraform"})
    r = await run()
    assert r.fell_back and "skills changed" in r.report["rejected"][0]["reason"]


async def test_bare_specials_are_auto_escaped(monkeypatch):
    script(monkeypatch, {"b2": "Reduced pipeline runtime by 40% using SQL optimization & tuning."})
    r = await run()
    assert not r.fell_back and r"40\% using SQL optimization \& tuning." in r.tex


async def test_new_latex_command_rejected(monkeypatch):
    script(monkeypatch, {"b1": r"Built REST APIs in Python \input{secret} serving 1M requests per day."})
    r = await run()
    assert r.fell_back and "new LaTeX commands" in r.report["rejected"][0]["reason"]


async def test_compile_error_reverts_only_offending_segment(monkeypatch):
    # \textbf with an unbalanced inner group passes brace-shape? No — use an undefined-but-allowed pattern:
    # \emph is allowed; '\emph{' + '^' outside math triggers 'Missing $ inserted' only in that bullet.
    script(monkeypatch, {"b1": r"Built REST APIs in Python serving 1M requests per day \emph{x^y}.",
                         "sum1": "Backend engineer building Python APIs and data pipelines."})
    r = await run()
    reverted = {x["id"] for x in r.report["reverted"]}
    assert reverted == {"b1"} and not r.fell_back and "building Python APIs" in r.tex and "x^y" not in r.tex


async def test_overflow_condense_then_revert_biggest_growth(monkeypatch):
    """Fake engine: 2 pages once edits add more than 20 characters. The condense pass doesn't shrink,
    so the pipeline must revert the edit that grew most (b1) and keep the smaller one (sum1)."""
    from resume.compiler import CompileResult
    async def fake_compile(tex, pdf):
        Path(pdf).write_bytes(b"%PDF-1.4"); grew = len(tex) - len(BASE)
        return CompileResult(ok=True, pdf=str(pdf), pages=2 if grew > 20 else 1, engine="fake")
    monkeypatch.setattr(T, "_compile", fake_compile)
    sum_new = "Backend engineer with experience building Python APIs and data pipelines."   # +7 chars
    b1_new = "Built REST APIs in Python and SQL serving 1M requests per day for APIs."       # +17 chars
    calls = script(monkeypatch, {"sum1": sum_new, "b1": b1_new}, {"sum1": sum_new, "b1": b1_new})
    r = await run()
    assert len(calls) == 2                                                   # tailor + one condense pass
    assert [x["id"] for x in r.report["reverted"]] == ["b1"] and "overflow" in r.report["reverted"][0]["reason"]
    assert not r.fell_back and r.page_count == 1 and sum_new in r.tex and b1_new not in r.tex


async def test_unknown_ids_and_model_failure_fall_back(monkeypatch):
    script(monkeypatch, {"zz9": "nonsense"})
    r = await run()
    assert r.fell_back and r.report["rejected"][0]["reason"] == "unknown segment id"

    async def boom(*a, **k): raise RuntimeError("endpoint down")
    monkeypatch.setattr(T, "complete_json", boom)
    r = await run(job="j3")
    assert r.fell_back and "endpoint down" in r.note and Path(r.pdf_path).exists()


async def test_jake_template_resumeitem_macros(monkeypatch):
    script(monkeypatch, {"b3": "Built a Rust log search tool with a React front end",
                         "sk1": "\n    \\textbf{Tools}{: Kafka, Docker, Jenkins, GitHub Actions} \\\\\n    \\textbf{Languages}{: Go, Rust, Python, TypeScript}\n  }}"})
    r = await run(base=JAKE, job="jk")
    assert not r.fell_back, r.report
    assert r"\resumeItem{Built a Rust log search tool with a React front end}" in r.tex
    assert "% keep this metric" in r.tex                                    # comments untouched


def test_whole_document_guard():
    assert T.validate(BASE, BASE.replace("Acme Corp", "Google"))
    assert any("Kubernetes" in e for e in T.validate(BASE, BASE.replace("Docker", "Docker, Kubernetes")))
    assert "preamble changed" in T.validate(BASE, BASE.replace("11pt", "12pt"))
    assert "section set/order changed" in T.validate(BASE, BASE.replace("{Skills}", "{Abilities}"))
    assert not T.validate(BASE, BASE)
