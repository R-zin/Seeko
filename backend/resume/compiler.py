"""Hardened LaTeX → PDF compile pipeline.

* preflight: refuses shell-escape / \\directlua / absolute or ../ file access before anything runs
* engine selection: tectonic → pdflatex → xelatex/lualatex (fontspec documents skip pdflatex);
  falls through to the next engine only on *infrastructure* failures, not on errors in the source
* isolation: fresh temp dir with only the résumé assets, -no-shell-escape, new process group that is
  killed as a whole on timeout, output size cap
* structured diagnostics: errors with file/line parsed from the log, overfull-box count, LaTeX warnings
* content-addressed cache (source + assets + engine) and a global concurrency limit
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from pypdf import PdfReader

from config.settings import DATA_DIR
from .texparse import parse

CACHE_DIR = DATA_DIR / ".latex_cache"
CACHE_MAX = 300
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_CONCURRENCY = 2


@dataclass
class LatexIssue:
    message: str
    line: int | None = None
    file: str = ""
    context: str = ""


@dataclass
class CompileResult:
    ok: bool
    pdf: str = ""
    pages: int = 0
    engine: str = ""
    seconds: float = 0.0
    cached: bool = False
    errors: list[LatexIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    overfull: int = 0
    log_tail: str = ""

    def summary(self) -> str:
        if self.ok: return f"compiled with {self.engine}: {self.pages} page(s)"
        if not self.errors: return self.log_tail[-600:] or "compile failed"
        e = self.errors[0]
        return f"line {e.line}: {e.message}" if e.line else e.message

    def to_dict(self) -> dict:
        d = asdict(self); d["summary"] = self.summary(); d.pop("log_tail"); return d


class UnsafeTex(ValueError):
    pass


# ── engines ──────────────────────────────────────────────────────────────

def available_engines() -> list[str]:
    return [e for e in ("tectonic", "pdflatex", "xelatex", "lualatex") if shutil.which(e)]


def engine_order(needs_unicode: bool, prefer: str | None = None) -> list[str]:
    eng = available_engines()
    if needs_unicode: eng = [e for e in eng if e != "pdflatex"]
    if prefer and prefer in eng: eng = [prefer] + [e for e in eng if e != prefer]
    return eng


def _cmd(engine: str, main: str, td: str) -> list[str]:
    if engine == "tectonic":
        return ["tectonic", "--keep-logs", "--outdir", td, main]
    return [engine, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", "-file-line-error",
            "-output-directory", td, main]


# ── log parsing ──────────────────────────────────────────────────────────

_FLE = re.compile(r"^(?:error: )?(?:\./)?([^\s:]+\.(?:tex|cls|sty)):(\d+): (.+)$", re.M)
_BANG = re.compile(r"^! (.+)$", re.M)
_LNUM = re.compile(r"^l\.(\d+) ?(.*)$", re.M)
_INFRA = re.compile(r"network|could not (?:download|fetch)|failed to (?:open|download)|bundle|No such file or directory: '?tectonic|"
                    r"I can't find the format file|Fatal format file error|command not found", re.I)


def parse_log(log: str, main: str = "resume.tex") -> tuple[list[LatexIssue], list[str], int]:
    errs: list[LatexIssue] = []
    for f, ln, msg in _FLE.findall(log):
        if msg.lower().startswith("warning"): continue
        errs.append(LatexIssue(message=msg.strip(), line=int(ln), file=f))
    if not errs:
        for m in _BANG.finditer(log):
            ln = _LNUM.search(log, m.end())
            errs.append(LatexIssue(message=m.group(1).strip(), line=int(ln.group(1)) if ln else None, file=main,
                                   context=(ln.group(2).strip() if ln else "")))
    if not errs:
        for m in re.finditer(r"^error: (.+)$", log, re.M):
            if "halted" in m.group(1) or "previous errors" in m.group(1): continue
            errs.append(LatexIssue(message=m.group(1).strip()))
    seen, uniq = set(), []
    for e in errs:
        k = (e.line, e.message)
        if k not in seen: seen.add(k); uniq.append(e)
    warns = sorted(set(re.findall(r"LaTeX Warning: ([^\n]+)", log)))[:20]
    overfull = len(re.findall(r"Overfull \\[hv]box", log))
    return uniq[:20], warns, overfull


# ── helpers ──────────────────────────────────────────────────────────────

def page_count(pdf: Path | str) -> int:
    return len(PdfReader(str(pdf)).pages)


def _assets_digest(assets_dir: Path | None) -> str:
    if not assets_dir or not assets_dir.exists(): return ""
    h = hashlib.sha256()
    for p in sorted(assets_dir.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(assets_dir)).encode()); h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def _copy_assets(assets_dir: Path | None, td: Path) -> None:
    if not assets_dir or not assets_dir.exists(): return
    root = assets_dir.resolve()
    for p in assets_dir.rglob("*"):
        if not p.is_file() or p.is_symlink(): continue
        rp = p.resolve()
        if root not in rp.parents: continue           # never follow links out of the asset dir
        dst = td / p.relative_to(assets_dir); dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, dst)


_sem: dict[int, asyncio.Semaphore] = {}


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if id(loop) not in _sem: _sem.clear(); _sem[id(loop)] = asyncio.Semaphore(MAX_CONCURRENCY)
    return _sem[id(loop)]


async def _run(cmd: list[str], cwd: str, timeout: float) -> tuple[int | None, str]:
    env = {**os.environ, "openout_any": "p", "openin_any": "p", "shell_escape": "f"}   # kpathsea paranoid mode
    p = await asyncio.create_subprocess_exec(*cmd, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL,
                                             start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
        return p.returncode, out.decode(errors="replace")
    except asyncio.TimeoutError:
        try: os.killpg(p.pid, signal.SIGKILL)               # kill the whole group (engine + helpers)
        except ProcessLookupError: pass
        await p.wait()
        return None, f"compile timed out after {timeout:.0f}s"


async def _compile_once(engine: str, tex: str, td: Path, timeout: float) -> tuple[bool, str]:
    main = "resume.tex"; (td / main).write_text(tex, encoding="utf-8")
    cmd = _cmd(engine, main, str(td))
    deadline = time.monotonic() + timeout; log = ""
    for _ in range(3 if engine != "tectonic" else 1):        # tectonic reruns itself; others need passes
        rc, out = await _run(cmd, str(td), max(1.0, deadline - time.monotonic()))
        logf = td / "resume.log"
        log = out + ("\n" + logf.read_text(errors="replace") if logf.exists() else "")
        if rc != 0: return False, log
        if not re.search(r"Rerun to get|Label\(s\) may have changed", log): break
    return (td / "resume.pdf").exists(), log


# ── public API ───────────────────────────────────────────────────────────

def preflight(tex: str) -> None:
    doc = parse(tex)
    if doc.unsafe: raise UnsafeTex("refusing to compile: " + "; ".join(doc.unsafe))


async def compile_document(tex: str, out_pdf: Path | str, assets_dir: Path | None = None, timeout: float = 90,
                           prefer: str | None = None, use_cache: bool = True) -> CompileResult:
    out_pdf = Path(out_pdf)
    try:
        preflight(tex)
    except UnsafeTex as e:
        return CompileResult(ok=False, errors=[LatexIssue(message=str(e))])
    doc = parse(tex)
    engines = engine_order(doc.needs_unicode_engine, prefer)
    if not engines:
        need = "xelatex/lualatex or tectonic (document uses fontspec)" if doc.needs_unicode_engine else "tectonic or pdflatex"
        return CompileResult(ok=False, errors=[LatexIssue(message=f"no suitable LaTeX engine installed — install {need}")])

    key = hashlib.sha256(("|".join(engines) + "\0" + tex + "\0" + _assets_digest(assets_dir)).encode()).hexdigest()
    cached = CACHE_DIR / f"{key}.pdf"
    if use_cache and cached.exists():
        out_pdf.parent.mkdir(parents=True, exist_ok=True); shutil.copy(cached, out_pdf); os.utime(cached)
        return CompileResult(ok=True, pdf=str(out_pdf), pages=page_count(out_pdf), engine=engines[0], cached=True)

    t0 = time.monotonic(); last = CompileResult(ok=False)
    async with _semaphore():
        for engine in engines:
            with tempfile.TemporaryDirectory(prefix="seeko-tex-") as tdn:
                td = Path(tdn); _copy_assets(assets_dir, td)
                ok, log = await _compile_once(engine, tex, td, timeout)
                errs, warns, overfull = parse_log(log)
                pdf = td / "resume.pdf"
                if ok and pdf.exists():
                    if pdf.stat().st_size > MAX_PDF_BYTES:
                        return CompileResult(ok=False, engine=engine, errors=[LatexIssue(message="PDF exceeds 20 MB size cap")])
                    out_pdf.parent.mkdir(parents=True, exist_ok=True); shutil.copy(pdf, out_pdf)
                    if use_cache: _store_cache(pdf, cached)
                    return CompileResult(ok=True, pdf=str(out_pdf), pages=page_count(out_pdf), engine=engine,
                                         seconds=round(time.monotonic() - t0, 2), warnings=warns, overfull=overfull)
                last = CompileResult(ok=False, engine=engine, seconds=round(time.monotonic() - t0, 2), errors=errs,
                                     warnings=warns, overfull=overfull, log_tail=log[-3000:])
                timed_out = "timed out" in log
                # Source errors reproduce on every engine — only fall through on infrastructure failures.
                if (errs and not _INFRA.search(log)) or timed_out:
                    if timed_out and not errs: last.errors = [LatexIssue(message=f"compile timed out after {timeout:.0f}s")]
                    break
    return last


def _store_cache(pdf: Path, dst: Path) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True); shutil.copy(pdf, dst)
        files = sorted(CACHE_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime)
        for old in files[:-CACHE_MAX]: old.unlink(missing_ok=True)
    except OSError:
        pass
