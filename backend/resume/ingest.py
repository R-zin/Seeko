"""Upload ingestion for the base résumé: .tex, .pdf or an Overleaf-style .zip.

Nothing here overwrites the saved base résumé. Uploads produce a *draft* (tex + analysis + compile
result); supporting files go to a staging directory that is promoted only when the user saves.
"""
from __future__ import annotations

import io
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from config.settings import DATA_DIR
from . import texparse
from .compiler import CompileResult, compile_document
from .pdfparse import PdfParseError, pdf_to_tex

RESUME_TEX = DATA_DIR / "base_resume.tex"
RESUME_PDF = DATA_DIR / "base_resume.pdf"
ASSETS_DIR = DATA_DIR / "resume_assets"
STAGING_DIR = DATA_DIR / "resume_staging"
STAGED_ASSETS = STAGING_DIR / "assets"
STAGED_SOURCE_PDF = STAGING_DIR / "source.pdf"
SOURCE_PDF = DATA_DIR / "base_source.pdf"

MAX_UPLOAD = 15 * 1024 * 1024
MAX_ZIP_FILES = 300
MAX_UNZIPPED = 60 * 1024 * 1024
ASSET_EXT = {".cls", ".sty", ".bst", ".bib", ".tex", ".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg",
             ".otf", ".ttf", ".pfb", ".tfm", ".fd", ".def", ".cfg", ".clo"}
MAIN_NAMES = ("main.tex", "resume.tex", "cv.tex", "base_resume.tex")


class IngestError(ValueError):
    pass


@dataclass
class Draft:
    kind: str                                   # tex | pdf | zip
    tex: str
    analysis: dict
    compile: dict
    notes: list[str] = field(default_factory=list)
    conversion: dict | None = None              # PDF conversion report
    staged_assets: list[str] = field(default_factory=list)
    has_source_pdf: bool = False


def list_assets(d: Path) -> set[str]:
    return {str(p.relative_to(d)) for p in d.rglob("*") if p.is_file()} if d.exists() else set()


def _reset_staging() -> None:
    shutil.rmtree(STAGING_DIR, ignore_errors=True)
    STAGED_ASSETS.mkdir(parents=True, exist_ok=True)


def _safe_member(name: str) -> PurePosixPath | None:
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or any(part in ("..", "") for part in p.parts) or name.startswith("/"): return None
    if p.parts and p.parts[0] in ("__MACOSX",) or p.name.startswith("._") or p.name == ".DS_Store": return None
    return p


def flatten_inputs(tex: str, root: Path, depth: int = 0, seen: set[str] | None = None) -> tuple[str, list[str]]:
    """Inline \\input / \\include files from root so the whole résumé is one editable document."""
    seen = seen or set(); used: list[str] = []
    if depth > 6: return tex, used
    mask = texparse.comment_mask(tex)

    def repl(m: re.Match) -> str:
        if mask[m.start()]: return m.group(0)
        name = m.group(2).strip()
        cand = [name, name + ".tex"]
        for c in cand:
            p = (root / c)
            try:
                rp = p.resolve()
            except OSError:
                continue
            if root.resolve() not in rp.parents or not rp.is_file() or c in seen: continue
            seen.add(c); used.append(c)
            inner, more = flatten_inputs(texparse.decode_tex(rp.read_bytes()), root, depth + 1, seen)
            used.extend(more)
            return f"% ── begin {c} (inlined by Seeko) ──\n{inner.rstrip()}\n% ── end {c} ──\n"
        return m.group(0)

    out = re.sub(r"\\(input|include)\s*\{([^}]*)\}", repl, tex)
    return out, used


async def _compile_draft(tex: str, assets: Path) -> CompileResult:
    return await compile_document(tex, STAGING_DIR / "draft.pdf", assets_dir=assets, timeout=90)


async def ingest(filename: str, data: bytes) -> Draft:
    if len(data) > MAX_UPLOAD: raise IngestError("file is larger than 15 MB")
    if not data: raise IngestError("empty file")
    ext = Path(filename or "").suffix.lower()
    _reset_staging()
    notes: list[str] = []

    if ext == ".pdf" or data[:4] == b"%PDF":
        try:
            tex, parsed = pdf_to_tex(data)
        except PdfParseError as e:
            raise IngestError(str(e)) from e
        STAGED_SOURCE_PDF.write_bytes(data)
        res = await _compile_draft(tex, STAGED_ASSETS)
        conv = parsed.report()
        if res.ok and res.pages > parsed.pages:
            notes.append(f"the converted résumé runs to {res.pages} pages; your PDF had {parsed.pages}. Trim it in the editor before saving.")
        notes.append("converted from PDF — layout is re-typeset in a clean template; review the text before saving")
        return Draft(kind="pdf", tex=tex, analysis=texparse.analyze(tex), compile=res.to_dict(), notes=notes + conv["warnings"],
                     conversion=conv, has_source_pdf=True)

    if ext == ".zip" or data[:2] == b"PK":
        tex, staged = _extract_zip(data, notes)
        res = await _compile_draft(tex, STAGED_ASSETS)
        return Draft(kind="zip", tex=tex, analysis=texparse.analyze(tex, list_assets(STAGED_ASSETS)), compile=res.to_dict(),
                     notes=notes, staged_assets=staged)

    if ext in (".tex", ".ltx", "") or b"\\documentclass" in data[:20000]:
        tex = texparse.decode_tex(data)
        if "\\documentclass" not in tex: raise IngestError("this .tex has no \\documentclass — upload the main file (or a .zip of the project)")
        # keep already-saved assets available for the draft compile
        if ASSETS_DIR.exists(): shutil.copytree(ASSETS_DIR, STAGED_ASSETS, dirs_exist_ok=True)
        res = await _compile_draft(tex, STAGED_ASSETS)
        return Draft(kind="tex", tex=tex, analysis=texparse.analyze(tex, list_assets(STAGED_ASSETS)), compile=res.to_dict(),
                     notes=notes, staged_assets=sorted(list_assets(STAGED_ASSETS)))

    raise IngestError(f"unsupported file type '{ext or 'unknown'}' — upload a .tex, .pdf or .zip")


def _extract_zip(data: bytes, notes: list[str]) -> tuple[str, list[str]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise IngestError("not a valid .zip archive") from e
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_ZIP_FILES: raise IngestError(f"zip has more than {MAX_ZIP_FILES} files")
    if sum(i.file_size for i in infos) > MAX_UNZIPPED: raise IngestError("zip expands to more than 60 MB")
    skipped: list[str] = []
    for i in infos:
        p = _safe_member(i.filename)
        if p is None: skipped.append(i.filename); continue
        if stat.S_ISLNK(i.external_attr >> 16): skipped.append(i.filename + " (symlink)"); continue
        if p.suffix.lower() not in ASSET_EXT: skipped.append(i.filename); continue
        dst = STAGED_ASSETS / Path(*p.parts); dst.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(i) as src, open(dst, "wb") as out: shutil.copyfileobj(src, out, 1 << 16)
    if skipped: notes.append(f"ignored {len(skipped)} file(s): " + ", ".join(skipped[:6]) + ("…" if len(skipped) > 6 else ""))

    # Strip a single common top-level folder (Overleaf exports often have one)
    tops = {p.parts[0] for p in (Path(x) for x in list_assets(STAGED_ASSETS))}
    if len(tops) == 1 and (STAGED_ASSETS / next(iter(tops))).is_dir():
        inner = STAGED_ASSETS / next(iter(tops)); tmp = STAGING_DIR / "_lift"; inner.rename(tmp)
        shutil.rmtree(STAGED_ASSETS); tmp.rename(STAGED_ASSETS)

    mains = []
    for p in STAGED_ASSETS.rglob("*.tex"):
        txt = texparse.decode_tex(p.read_bytes())
        if re.search(r"^[^%\n]*\\documentclass", txt, re.M) and "\\begin{document}" in txt: mains.append((p, txt))
    if not mains: raise IngestError("no main .tex (with \\documentclass and \\begin{document}) found in the zip")
    mains.sort(key=lambda x: (x[0].name.lower() not in MAIN_NAMES, len(x[0].parts), -len(x[1])))
    main, tex = mains[0]
    if len(mains) > 1: notes.append(f"several main files found; using {main.relative_to(STAGED_ASSETS)}")
    tex, used = flatten_inputs(tex, main.parent)
    if used: notes.append(f"inlined {len(used)} \\input file(s): " + ", ".join(used[:6]))
    main.unlink()                                             # the draft replaces it
    return tex, sorted(list_assets(STAGED_ASSETS))


async def save_base(tex: str, promote_staged: bool) -> CompileResult:
    """Validate, compile and persist a new base résumé (and, if requested, promote staged assets/source PDF)."""
    assets = STAGED_ASSETS if promote_staged and STAGED_ASSETS.exists() else ASSETS_DIR
    res = await compile_document(tex, DATA_DIR / "base_resume.next.pdf", assets_dir=assets, timeout=120)
    if not res.ok: return res
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "base_resume.next.pdf").replace(RESUME_PDF)
    RESUME_TEX.write_text(tex, encoding="utf-8")
    if promote_staged:
        if STAGED_ASSETS.exists():
            shutil.rmtree(ASSETS_DIR, ignore_errors=True); shutil.copytree(STAGED_ASSETS, ASSETS_DIR)
        if STAGED_SOURCE_PDF.exists(): shutil.copy(STAGED_SOURCE_PDF, SOURCE_PDF)
        shutil.rmtree(STAGING_DIR, ignore_errors=True)
    res.pdf = str(RESUME_PDF)
    return res
