"""PDF résumé parser → structured résumé → clean, compilable LaTeX.

Deterministic and truthful by construction: every word in the generated .tex comes from the PDF's
text layer. No model is involved. The output uses standard \\section* headings and \\item bullets so the
tailoring parser can segment it.

Heuristics (layout-mode text extraction from pypdf):
  * header        = lines before the first section heading (first line = name, rest = contact)
  * headings      = short lines in ALL CAPS or matching a known résumé heading vocabulary
  * bullets       = lines starting with a bullet glyph; wrapped continuation lines are re-joined
  * entries       = non-bullet lines inside a section; a wide gap splits "Company … Dates"
  * de-hyphenation, NFKC normalisation (ligatures), icon-font glyphs (Private Use Area) dropped
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .texparse import escape_text


class PdfParseError(ValueError):
    pass


HEADINGS = {
    "summary", "professional summary", "profile", "professional profile", "objective", "career objective", "about", "about me",
    "experience", "work experience", "professional experience", "employment", "employment history", "relevant experience",
    "education", "skills", "technical skills", "core skills", "key skills", "skills & interests", "skills and interests",
    "technologies", "tools", "projects", "personal projects", "selected projects", "academic projects", "certifications",
    "certificates", "licenses & certifications", "awards", "honors", "honors & awards", "achievements", "publications",
    "leadership", "leadership experience", "volunteer", "volunteering", "volunteer experience", "activities",
    "extracurricular activities", "interests", "languages", "research", "research experience", "coursework",
    "relevant coursework", "internships", "references",
}
BULLET_RE = re.compile(r"^\s*([•●▪■◦‣∙·○◆►▸➢✓\-–—\*])\s+")
DATE_RE = re.compile(r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+)?(19|20)\d{2}|present|current", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|gitlab\.com|[\w-]+\.(?:dev|io|me|com|net|org|app))/?[\w\-./?=#%]*", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


@dataclass
class Entry:
    left: str
    right: str = ""


@dataclass
class Block:
    kind: str                     # "entry" | "bullet" | "text"
    text: str = ""
    entry: Entry | None = None


@dataclass
class ParsedSection:
    name: str
    blocks: list[Block] = field(default_factory=list)


@dataclass
class ParsedResume:
    name: str
    contact: list[str]
    sections: list[ParsedSection]
    pages: int
    warnings: list[str]

    def report(self) -> dict:
        return {"name": self.name, "contact": self.contact, "pages": self.pages, "warnings": self.warnings,
                "sections": [{"name": s.name, "bullets": sum(b.kind == "bullet" for b in s.blocks),
                              "entries": sum(b.kind == "entry" for b in s.blocks),
                              "lines": sum(b.kind == "text" for b in s.blocks)} for s in self.sections]}


# ── extraction ───────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = "".join(ch for ch in s if not (0xE000 <= ord(ch) <= 0xF8FF) and (ch in "\t" or unicodedata.category(ch)[0] != "C"))
    return s.rstrip()


def extract_lines(data: bytes) -> tuple[list[str], int]:
    if not data.startswith(b"%PDF"):
        raise PdfParseError("not a PDF file")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfParseError("the PDF is password-protected")
    except PdfReadError as e:
        raise PdfParseError(f"could not read PDF: {e}") from e
    lines: list[str] = []
    for page in reader.pages:
        try:
            txt = page.extract_text(extraction_mode="layout") or ""
        except Exception:
            txt = page.extract_text() or ""
        if not txt.strip():
            txt = page.extract_text() or ""
        lines += [_clean(l) for l in txt.splitlines()]
    text_chars = sum(len(l.strip()) for l in lines)
    if text_chars < 80:
        raise PdfParseError("the PDF has no usable text layer (it looks scanned). Export your résumé as a text PDF, or upload the .tex.")
    # trim common left indent
    nonblank = [l for l in lines if l.strip()]
    indent = min((len(l) - len(l.lstrip()) for l in nonblank), default=0)
    return [l[indent:] if l.strip() else "" for l in lines], len(reader.pages)


# ── structure ────────────────────────────────────────────────────────────

COL_GAP = re.compile(r"\s{6,}")          # layout mode pads word gaps to 2-3 spaces; real columns are far wider
_ws = lambda s: re.sub(r"\s+", " ", s).strip()


def _heading_text(line: str) -> str:
    return re.sub(r"^(?:\d+(?:\.\d+)*|[IVX]+\.)\s+", "", _ws(line)).rstrip(":").strip()   # drop '1 ', '2.1 ', 'IV. '


def _is_heading(line: str) -> bool:
    s = _heading_text(line)
    if not s or len(s) > 40 or BULLET_RE.match(line) or DATE_RE.search(s) or "@" in s: return False
    norm = re.sub(r"\s+", " ", s.lower().replace("&", "&"))
    if norm in HEADINGS: return True
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters) and len(s.split()) <= 4


def _split_columns(line: str) -> tuple[str, str]:
    """'Acme Corp — Engineer        Jan 2021 – Present' -> ('Acme Corp — Engineer', 'Jan 2021 – Present').
    A wide gap (3+ spaces in layout mode) separates the left column from a right-aligned one."""
    parts = [_ws(p) for p in COL_GAP.split(line.strip()) if p.strip()]
    if len(parts) < 2: return (parts[0] if parts else ""), ""
    return " ".join(parts[:-1]), parts[-1]


def _join_wrapped(prev: str, nxt: str) -> str:
    if prev.endswith("-") and nxt[:1].islower(): return prev[:-1] + nxt
    return prev + " " + nxt


def structure(lines: list[str], pages: int) -> ParsedResume:
    warnings: list[str] = []
    first = next((i for i, l in enumerate(lines) if _is_heading(l)), None)
    if first is None:
        warnings.append("no section headings recognised — everything was placed under one section; check the result")
        first = min(3, len(lines))
    header = [l.strip() for l in lines[:first] if l.strip()]
    name = _ws(header[0]) if header else ""
    contact: list[str] = []
    for l in header[1:]:
        contact += [_ws(p).strip(" |·•,") for p in re.split(r"\s{6,}|\s*[|·•◦]\s*|\s{2,}[–—-]\s{2,}", l) if p.strip(" |·•,")]

    sections: list[ParsedSection] = []
    cur: ParsedSection | None = None if first < len(lines) and _is_heading(lines[first]) else ParsedSection("Résumé")
    if cur: sections.append(cur)
    prev_indent = 0
    for raw in lines[first:]:
        if not raw.strip():
            continue
        if _is_heading(raw):
            h = _heading_text(raw)
            cur = ParsedSection(h.title() if h.isupper() else h)
            sections.append(cur); continue
        indent = len(raw) - len(raw.lstrip())
        bm = BULLET_RE.match(raw)
        last = cur.blocks[-1] if cur.blocks else None
        if bm:
            cur.blocks.append(Block("bullet", text=_ws(raw[bm.end():])))
            prev_indent = bm.end(); continue
        left, right = _split_columns(raw)
        # continuation of a wrapped bullet / paragraph line
        if last and last.kind in ("bullet", "text") and not right and (indent >= max(prev_indent - 1, 1) or left[:1].islower()):
            last.text = _join_wrapped(last.text, left); continue
        if right or (last is None or last.kind != "text") and len(left) < 90 and not left.endswith("."):
            cur.blocks.append(Block("entry", entry=Entry(left=left, right=right)))
        else:
            cur.blocks.append(Block("text", text=left))
        prev_indent = indent
    # Summary/skills sections read better as text lines than as bold entries
    for s in sections:
        if re.search(r"summary|profile|objective|about|skill|technolog|interest|language|coursework", s.name, re.I):
            for b in s.blocks:
                if b.kind == "entry" and b.entry and not b.entry.right:
                    b.kind, b.text, b.entry = "text", b.entry.left, None
    if not any(b.kind == "bullet" for s in sections for b in s.blocks):
        warnings.append("no bullet points detected — experience will be plain lines and tailoring has fewer bullets to rephrase")
    if not name: warnings.append("could not identify your name in the header")
    return ParsedResume(name=name, contact=contact, sections=sections, pages=pages, warnings=warnings)


# ── LaTeX rendering ──────────────────────────────────────────────────────

PREAMBLE = r"""\documentclass[11pt]{article}
\usepackage[margin=0.6in]{geometry}
\usepackage[hidelinks]{hyperref}
\usepackage{enumitem}
\usepackage{titlesec}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlist[itemize]{leftmargin=1.2em, itemsep=1pt, topsep=2pt, parsep=0pt}
\titleformat{\section}{\large\bfseries}{}{0em}{}[\titlerule]
\titlespacing*{\section}{0pt}{8pt}{4pt}
% Generated by Seeko from a PDF. Every word below comes from your PDF's text — review before saving.
"""


def _contact_tex(item: str) -> str:
    if EMAIL_RE.fullmatch(item): return rf"\href{{mailto:{item}}}{{{escape_text(item)}}}"
    if URL_RE.fullmatch(item) and "." in item:
        url = item if item.startswith("http") else "https://" + item
        return rf"\href{{{url.replace('%', r'\%').replace('#', r'\#')}}}{{{escape_text(item)}}}"
    return escape_text(item)


def to_tex(r: ParsedResume) -> str:
    out = [PREAMBLE, r"\begin{document}", "", r"\begin{center}"]
    out.append(rf"  {{\LARGE\bfseries {escape_text(r.name)}}}\\[3pt]")
    if r.contact: out.append("  " + r" \textbullet{} ".join(_contact_tex(c) for c in r.contact))
    out += [r"\end{center}", ""]
    for s in r.sections:
        out.append(rf"\section*{{{escape_text(s.name)}}}")
        in_list = False
        for b in s.blocks:
            if b.kind == "bullet":
                if not in_list: out.append(r"\begin{itemize}"); in_list = True
                out.append(rf"  \item {escape_text(b.text)}")
                continue
            if in_list: out.append(r"\end{itemize}"); in_list = False
            if b.kind == "entry":
                e = b.entry
                out.append(rf"\textbf{{{escape_text(e.left)}}}" + (rf" \hfill {escape_text(e.right)}" if e.right else "") + r"\\")
            else:
                out.append(escape_text(b.text) + r"\\")
        if in_list: out.append(r"\end{itemize}")
        out.append("")
    out.append(r"\end{document}")
    tex = "\n".join(out) + "\n"
    return re.sub(r"\\\\(\n\s*\n)", r"\1", tex)          # no '\\' at a paragraph end (underfull warnings)


def pdf_to_tex(data: bytes) -> tuple[str, ParsedResume]:
    lines, pages = extract_lines(data)
    parsed = structure(lines, pages)
    return to_tex(parsed), parsed
