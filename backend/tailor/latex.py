from __future__ import annotations
import asyncio, shutil, tempfile
from pathlib import Path
from pypdf import PdfReader


def find_compiler() -> list[str] | None:
    if shutil.which("tectonic"): return ["tectonic", "--keep-logs", "-X", "compile"] if False else ["tectonic"]
    if shutil.which("pdflatex"): return ["pdflatex", "-interaction=nonstopmode", "-halt-on-error"]
    return None


def page_count(pdf: Path) -> int:
    return len(PdfReader(str(pdf)).pages)


async def compile_tex(tex: str, out_pdf: Path, timeout: float = 120) -> tuple[bool, str]:
    """Compile tex source to out_pdf. Returns (ok, log)."""
    cmd = find_compiler()
    if not cmd: return False, "no LaTeX compiler found (install tectonic or pdflatex)"
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "resume.tex"; src.write_text(tex)
        args = cmd + ([str(src)] if cmd[0] == "tectonic" else ["-output-directory", td, str(src)])
        try:
            p = await asyncio.create_subprocess_exec(*args, cwd=td, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            p.kill(); return False, "compile timeout"
        log = out.decode(errors="replace")[-4000:]
        pdf = Path(td) / "resume.pdf"
        if p.returncode != 0 or not pdf.exists(): return False, log
        out_pdf.parent.mkdir(parents=True, exist_ok=True); shutil.copy(pdf, out_pdf)
        return True, log
