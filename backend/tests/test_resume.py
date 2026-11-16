import io
import os
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resume import compiler as C, ingest as I, texparse as T
from resume.pdfparse import PdfParseError, extract_lines, pdf_to_tex, structure

FX = Path(__file__).parent / "fixtures"
BASE = (FX / "resume.tex").read_text()
JAKE = (FX / "jake_resume.tex").read_text()


@pytest.fixture
def staging(tmp_path, monkeypatch):
    """Point every ingest path at a temp dir so tests never touch data/."""
    for name, rel in [("RESUME_TEX", "base_resume.tex"), ("RESUME_PDF", "base_resume.pdf"), ("ASSETS_DIR", "assets"),
                      ("STAGING_DIR", "staging"), ("STAGED_ASSETS", "staging/assets"),
                      ("STAGED_SOURCE_PDF", "staging/source.pdf"), ("SOURCE_PDF", "base_source.pdf")]:
        monkeypatch.setattr(I, name, tmp_path / rel)
    monkeypatch.setattr(C, "CACHE_DIR", tmp_path / "cache")
    return tmp_path


async def _pdf_of(tex: str, tmp: Path) -> bytes:
    r = await C.compile_document(tex, tmp / "x.pdf"); assert r.ok, r.summary()
    return (tmp / "x.pdf").read_bytes()


# ── .tex parser ──────────────────────────────────────────────────────────

def test_segments_of_plain_article():
    d = T.parse(BASE)
    assert [(s.id, s.kind, s.section) for s in d.segments] == [
        ("sum1", "summary", "Summary"), ("sk1", "skills", "Skills"), ("b1", "bullet", "Experience"), ("b2", "bullet", "Experience")]
    assert d.segment("b2").text == r"Reduced pipeline runtime by 40\% using SQL optimization."


def test_segments_of_jake_template_only_one_arg_item_macros():
    d = T.parse(JAKE)
    assert d.bullet_macros == ["resumeItem"]
    assert [s.id for s in d.segments if s.kind == "bullet"] == ["b1", "b2", "b3"]
    assert d.segment("b1").text == "Designed a Go service handling 20k events/sec on Kafka"   # comment after brace excluded
    assert all("Globex" not in s.text for s in d.segments)                                      # headings frozen


def test_apply_edits_is_byte_identical_outside_segments():
    d = T.parse(JAKE)
    out = T.apply_edits(d, {"b3": "X"})
    assert out.replace(r"\resumeItem{X}", r"\resumeItem{Built a log search tool in Rust with a React front end}") == JAKE


def test_comments_and_escapes():
    t = "a % x {\n\\% {b}"
    assert T.strip_comments(t) == "a \n\\% {b}" and T.balanced(r"\{ { } \}") and not T.balanced("{ \\}")
    assert T.fix_bare_specials(r"40% & C# in $a_b$ \textbf{x_y}") == r"40\% \& C\# in $a_b$ \textbf{x\_y}"


def test_unsafe_and_missing_assets_detected():
    evil = BASE.replace(r"\begin{document}", "\\immediate\\write18{rm -rf ~}\n\\input{/etc/passwd}\n\\input{../x}\n\\begin{document}")
    d = T.parse(evil)
    assert any("write18" in u for u in d.unsafe) and any("absolute" in u for u in d.unsafe) and any("parent" in u for u in d.unsafe)
    rep = T.analyze(BASE.replace("{article}", "{awesome-cv}").replace(r"\begin{document}", "\\input{sections/head}\n\\begin{document}"))
    assert any("awesome-cv.cls" in m for m in rep["missing_assets"]) and any("sections/head" in m for m in rep["missing_assets"])


def test_decode_handles_bom_crlf_and_cp1252():
    assert T.decode_tex(b"\xef\xbb\xbf\\documentclass{x}\r\nA") == "\\documentclass{x}\nA"
    assert T.decode_tex("R\xe9sum\xe9".encode("cp1252")) == "Résumé"


def test_plain_text_for_prompts():
    pt = T.plain_text(BASE)
    assert "Built REST APIs in Python serving 1M requests per day." in pt and "\\" not in pt and "40% using SQL" in pt


# ── compiler ─────────────────────────────────────────────────────────────

async def test_compile_errors_carry_line_numbers(tmp_path, staging):
    bad = BASE.replace("Python, SQL", r"Python, \nosuchmacro SQL")
    r = await C.compile_document(bad, tmp_path / "b.pdf", use_cache=False)
    assert not r.ok and r.errors[0].line == 11 and "Undefined control sequence" in r.errors[0].message


async def test_preflight_blocks_shell_escape_before_running(tmp_path, staging):
    r = await C.compile_document(BASE.replace(r"\begin{document}", "\\write18{touch pwned}\n\\begin{document}"), tmp_path / "x.pdf")
    assert not r.ok and "refusing to compile" in r.errors[0].message and not Path("pwned").exists()


async def test_cache_hit_on_identical_source(tmp_path, staging):
    a = await C.compile_document(BASE, tmp_path / "a.pdf"); b = await C.compile_document(BASE, tmp_path / "b.pdf")
    assert a.ok and not a.cached and b.cached and b.pages == 1


async def test_timeout_kills_whole_process_group(tmp_path, staging, monkeypatch):
    marker = tmp_path / "child.pid"
    monkeypatch.setattr(C, "engine_order", lambda *a, **k: ["fake"])
    monkeypatch.setattr(C, "_cmd", lambda e, m, td: ["sh", "-c", f"sleep 30 & echo $! > {marker}; wait"])
    t0 = __import__("time").monotonic()
    r = await C.compile_document(BASE, tmp_path / "t.pdf", timeout=1, use_cache=False)
    # Killing only the parent leaves the grandchild holding the pipe open and the call hangs ~30 s.
    assert __import__("time").monotonic() - t0 < 8, "compile hung: process group was not killed"
    assert not r.ok and "timed out" in r.errors[0].message
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):           # the backgrounded grandchild is gone too
        for _ in range(20):
            os.kill(pid, 0); __import__("time").sleep(0.05)
        raise AssertionError("child still alive")


async def test_infra_failure_falls_through_to_next_engine(tmp_path, staging, monkeypatch):
    seen = []
    async def fake_once(engine, tex, td, timeout):
        seen.append(engine)
        if engine == "first": return False, "error: network failure: could not download bundle"
        return False, "! Undefined control sequence.\nl.3 \\bad"
    monkeypatch.setattr(C, "engine_order", lambda *a, **k: ["first", "second", "third"])
    monkeypatch.setattr(C, "_compile_once", fake_once)
    r = await C.compile_document(BASE, tmp_path / "f.pdf", use_cache=False)
    assert seen == ["first", "second"] and r.errors[0].line == 3       # source error on 2nd engine stops the chain


def test_log_parser_formats():
    errs, warns, over = C.parse_log("./resume.tex:12: Undefined control sequence.\nOverfull \\hbox (3pt too wide)\nLaTeX Warning: Reference x undefined")
    assert errs[0].line == 12 and over == 1 and warns == ["Reference x undefined"]
    errs, *_ = C.parse_log("! Missing $ inserted.\n<inserted text>\nl.42 a_b")
    assert errs[0].line == 42 and errs[0].context == "a_b"


# ── PDF parser ───────────────────────────────────────────────────────────

async def test_pdf_roundtrip_plain(tmp_path, staging):
    tex, p = pdf_to_tex(await _pdf_of(BASE, tmp_path))
    rep = p.report()
    assert rep["name"] == "Jane Doe" and rep["contact"] == ["jane@example.com", "Austin, TX"]
    assert [s["name"] for s in rep["sections"]] == ["Summary", "Skills", "Experience", "Education"]
    assert r"\item Reduced pipeline runtime by 40\% using SQL optimization." in tex
    assert r"\textbf{Acme Corp – Software Engineer} \hfill 2021–2024" in tex
    r = await C.compile_document(tex, tmp_path / "c.pdf", use_cache=False)
    assert r.ok and r.pages == 1 and r.overfull == 0
    assert [s.kind for s in T.parse(tex).segments] == ["summary", "skills", "bullet", "bullet"]


async def test_pdf_roundtrip_numbered_sections_and_two_column_entries(tmp_path, staging):
    tex, p = pdf_to_tex(await _pdf_of(JAKE, tmp_path))
    assert [s.name for s in p.sections] == ["Experience", "Projects", "Technical Skills"]
    assert r"\textbf{Globex} \hfill Jun 2022 – Present" in tex and r"\item Designed a Go service handling 20k events/sec on Kafka" in tex
    assert r"\href{https://github.com/samlee}{github.com/samlee}" in tex


def test_pdf_wrapped_bullets_rejoined_and_dehyphenated():
    lines = ["Ada Byron", "ada@x.io", "EXPERIENCE", "Analytical Engines Ltd          1843",
             "  • Wrote the first published algo-", "    rithm for the engine and its", "    notes", "  • Second bullet"]
    p = structure(lines, 1)
    bullets = [b.text for b in p.sections[0].blocks if b.kind == "bullet"]
    assert bullets == ["Wrote the first published algorithm for the engine and its notes", "Second bullet"]
    assert p.sections[0].name == "Experience"


def test_pdf_errors():
    with pytest.raises(PdfParseError, match="not a PDF"): extract_lines(b"hello")
    from pypdf import PdfWriter
    w = PdfWriter(); w.add_blank_page(612, 792); buf = io.BytesIO(); w.write(buf)
    with pytest.raises(PdfParseError, match="no usable text layer"): extract_lines(buf.getvalue())


# ── ingest ───────────────────────────────────────────────────────────────

def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items(): z.writestr(n, b)
    return buf.getvalue()


async def test_zip_overleaf_project_flattened_and_assets_staged(staging):
    main = BASE.replace(r"\section*{Experience}", r"\input{sections/experience}").split(r"\section*{Education}")[0] + \
        "\\section*{Education}\nB.S. Computer Science, State University, 2020\n\\end{document}\n"
    exp = BASE.split(r"\section*{Experience}")[1].split(r"\section*{Education}")[0]
    data = _zip({"proj/main.tex": main.encode(), "proj/sections/experience.tex": (r"\section*{Experience}" + exp).encode(),
                 "proj/logo.png": b"\x89PNG", "proj/notes.exe": b"MZ", "../evil.tex": b"x", "proj/__MACOSX/._main.tex": b"x"})
    d = await I.ingest("resume.zip", data)
    assert d.kind == "zip" and d.compile["ok"], d.compile
    assert r"\item Built REST APIs" in d.tex and "inlined by Seeko" in d.tex
    assert "logo.png" in d.staged_assets and not any("exe" in a or "evil" in a for a in d.staged_assets)
    assert any("ignored" in n for n in d.notes)
    assert not (staging / "evil.tex").exists() and not (staging.parent / "evil.tex").exists()


async def test_zip_symlink_rejected(staging):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("main.tex", BASE)
        info = zipfile.ZipInfo("link.sty"); info.external_attr = (0o120777 << 16); z.writestr(info, "/etc/passwd")
    d = await I.ingest("p.zip", buf.getvalue())
    assert "link.sty" not in d.staged_assets and any("symlink" in n for n in d.notes)


async def test_ingest_rejections(staging):
    for name, data, msg in [("a.docx", b"PK\x03\x04junk", "not a valid .zip"), ("a.txt", b"hello", "unsupported"),
                            ("a.tex", b"\\begin{document}x", "has no .documentclass"), ("a.pdf", b"%PDF-1.4 broken", "could not read PDF|no usable text")]:
        with pytest.raises(I.IngestError, match=msg): await I.ingest(name, data)


async def test_upload_never_overwrites_base_until_saved(staging, tmp_path):
    (staging / "base_resume.tex").write_text(BASE)
    d = await I.ingest("cv.pdf", await _pdf_of(JAKE, tmp_path))
    assert d.kind == "pdf" and d.has_source_pdf and (staging / "base_resume.tex").read_text() == BASE
    res = await I.save_base(d.tex, promote_staged=True)
    assert res.ok and "Sam Lee" in (staging / "base_resume.tex").read_text()
    assert (staging / "base_source.pdf").exists() and not (staging / "staging").exists()


# ── API ──────────────────────────────────────────────────────────────────

async def test_api_upload_analyze_save_roundtrip(staging, tmp_path, monkeypatch):
    import api.resume as R
    for n in ("ASSETS_DIR", "RESUME_PDF", "SOURCE_PDF", "STAGED_ASSETS", "STAGED_SOURCE_PDF", "STAGING_DIR"):
        monkeypatch.setattr(R, n, getattr(I, n))
    monkeypatch.setattr(R, "load_resume", lambda: (staging / "base_resume.tex").read_text() if (staging / "base_resume.tex").exists() else "")
    from main import app
    pdf = await _pdf_of(BASE, tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/resume/upload", files={"file": ("cv.pdf", pdf, "application/pdf")}).json()
        assert r["kind"] == "pdf" and r["compile"]["ok"] and r["analysis"]["segments"]["bullet"] == 2
        assert c.get("/api/resume/source.pdf?staged=1").status_code == 200
        bad = c.put("/api/resume", json={"tex": r["tex"].replace("Python, SQL", r"\oops"), "promote_staged": True})
        assert bad.status_code == 422 and bad.json()["detail"]["compile"]["errors"][0]["line"]
        ok = c.put("/api/resume", json={"tex": r["tex"], "promote_staged": True}).json()
        assert ok["ok"] and ok["analysis"]["editable_total"] == 4
        g = c.get("/api/resume").json()
        assert g["has_pdf"] and g["has_source_pdf"] and "Jane Doe" in g["tex"]
        legacy = c.put("/api/resume", files={"file": ("r.tex", BASE.encode(), "text/x-tex")})
        assert legacy.status_code == 200
        assert c.post("/api/resume/upload", files={"file": ("x.txt", b"hi", "text/plain")}).status_code == 422
