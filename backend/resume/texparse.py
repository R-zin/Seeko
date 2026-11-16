"""LaTeX résumé parser.

Turns a .tex résumé into a structural model:
  * sections (\\section, \\section*, \\cvsection, \\resumesection …) with their spans
  * *editable segments* — the only text the tailoring model may touch:
      - summary  : prose of a Summary / Objective / Profile section
      - skills   : each line / item of a Skills section (reorder & regroup only)
      - bullet   : \\item text or one-argument \\…Item{…} macros in Experience / Projects sections
  * safety findings (shell escape, absolute or parent-directory file access)
  * dependencies (document class, packages, \\input files, graphics, fonts) and an engine hint

Spans are absolute character offsets into the original source, so edits can be spliced back
without ever regenerating the rest of the document.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

SegKind = Literal["summary", "skills", "bullet"]
_PREFIX = {"summary": "sum", "skills": "sk", "bullet": "b"}

SECTION_MACROS = ("section", "section*", "cvsection", "resumesection", "Section")
SUMMARY_RE = re.compile(r"summary|objective|profile|about", re.I)
SKILLS_RE = re.compile(r"skill|technolog|tools|competenc|stack|expertise", re.I)
BULLET_SECTION_RE = re.compile(r"experience|employment|work|project|research|leadership|intern|volunteer|activit|position", re.I)

STANDARD_CLASSES = {"article", "report", "book", "letter", "extarticle", "extreport", "memoir", "scrartcl",
                    "scrreprt", "moderncv", "res", "amsart", "minimal", "standalone"}
FONTSPEC_RE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\b(fontspec|polyglossia|unicode-math)\b|\\setmainfont|\\setsansfont|\\newfontfamily")
UNSAFE_RE = re.compile(r"\\(write18|immediate\s*\\write18|ShellEscape|directlua|luaexec|openout|pdfshellescape)\b")
FILE_CMD_RE = re.compile(r"\\(input|include|includegraphics|InputIfFileExists|lstinputlisting|verbatiminput|addbibresource|bibliography)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


# ── low-level helpers ────────────────────────────────────────────────────

def decode_tex(data: bytes) -> str:
    """Decode uploaded bytes: UTF-8 (with/without BOM), falling back to cp1252. Normalise newlines."""
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc); break
        except UnicodeDecodeError:
            continue
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def comment_mask(t: str) -> list[bool]:
    """True for every character that sits inside a % comment (unescaped % to end of line)."""
    mask = [False] * len(t); i = 0
    while i < len(t):
        c = t[i]
        if c == "\\": i += 2; continue
        if c == "%":
            j = t.find("\n", i); j = len(t) if j == -1 else j
            for k in range(i, j): mask[k] = True
            i = j; continue
        i += 1
    return mask


def strip_comments(t: str) -> str:
    m = comment_mask(t)
    return "".join(c for c, x in zip(t, m) if not x)


def match_brace(t: str, i: int) -> int:
    """Given t[i] == '{', return the index of its matching '}' (honouring escapes and comments). -1 if unbalanced."""
    assert t[i] == "{"
    depth = 0; j = i
    while j < len(t):
        c = t[j]
        if c == "\\": j += 2; continue
        if c == "%":
            nl = t.find("\n", j); j = len(t) if nl == -1 else nl; continue
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0: return j
        j += 1
    return -1


def balanced(t: str) -> bool:
    depth = 0; i = 0
    while i < len(t):
        c = t[i]
        if c == "\\": i += 2; continue
        if c == "%":
            nl = t.find("\n", i); i = len(t) if nl == -1 else nl; continue
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0: return False
        i += 1
    return depth == 0


def line_of(t: str, pos: int) -> int:
    return t.count("\n", 0, pos) + 1


_SPECIAL = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_"}


def escape_text(s: str) -> str:
    """Escape plain text for LaTeX (used by the PDF→.tex converter)."""
    s = s.replace("\\", "\x00")
    for a, b in _SPECIAL.items(): s = s.replace(a, b)
    s = s.replace("{", r"\{").replace("}", r"\}").replace("~", r"\textasciitilde{}").replace("^", r"\textasciicircum{}")
    return s.replace("\x00", r"\textbackslash{}")


def fix_bare_specials(s: str) -> str:
    """Escape &, %, #, _ that a model emitted unescaped in prose (outside math). Leaves commands intact."""
    out = []; i = 0; in_math = False
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(s[i:i + 2]); i += 2; continue
        if c == "$": in_math = not in_math
        if not in_math and c in "&%#_":
            out.append("\\" + c); i += 1; continue
        out.append(c); i += 1
    return "".join(out)


def plain_text(tex: str) -> str:
    """Readable text of the document body for scoring / prompts (commands dropped, arguments kept)."""
    body = tex.split(r"\begin{document}", 1)[-1].split(r"\end{document}", 1)[0]
    body = strip_comments(body)
    body = re.sub(r"\\(begin|end)\{[^}]*\}", " ", body)
    body = re.sub(r"\\(href)\{[^}]*\}", " ", body)
    body = re.sub(r"\\[a-zA-Z@]+\*?(\[[^\]]*\])?", " ", body)
    body = body.replace("\\\\", "\n").replace("{", " ").replace("}", " ")
    body = re.sub(r"\\([&%$#_])", r"\1", body)
    body = re.sub(r"[ \t]+", " ", body)
    return "\n".join(l.strip() for l in body.splitlines() if l.strip())


# ── structure ────────────────────────────────────────────────────────────

@dataclass
class Section:
    name: str
    start: int          # index of the section command
    body_start: int     # index right after the heading's closing brace
    end: int            # start of next section / \end{document}


@dataclass
class Segment:
    id: str
    kind: SegKind
    section: str
    start: int
    end: int
    text: str

    def as_prompt(self) -> dict:
        return {"id": self.id, "kind": self.kind, "section": self.section, "text": self.text}


@dataclass
class TexDoc:
    source: str
    preamble_end: int
    body_end: int
    sections: list[Section]
    segments: list[Segment]
    documentclass: str
    packages: list[str]
    files: list[tuple[str, str]]          # (command, path)
    bullet_macros: list[str]
    needs_unicode_engine: bool
    unsafe: list[str]
    warnings: list[str] = field(default_factory=list)

    def segment(self, sid: str) -> Segment | None:
        return next((s for s in self.segments if s.id == sid), None)

    def segment_at_line(self, line: int) -> Segment | None:
        for s in self.segments:
            if line_of(self.source, s.start) <= line <= line_of(self.source, s.end):
                return s
        return None


def _one_arg_macros(preamble: str) -> set[str]:
    """Names of macros defined with exactly one argument (\\newcommand{\\resumeItem}[1]{…})."""
    names = set()
    for m in re.finditer(r"\\(?:re)?newcommand\*?\s*\{?\\([A-Za-z@]+)\}?\s*\[(\d)\]", preamble):
        if m.group(2) == "1": names.add(m.group(1))
    for m in re.finditer(r"\\def\\([A-Za-z@]+)#1\{", preamble):
        names.add(m.group(1))
    return names


def _find_sections(t: str, mask: list[bool], start: int, end: int) -> list[Section]:
    pat = re.compile(r"\\(" + "|".join(re.escape(m) for m in SECTION_MACROS) + r")\s*(?=\{)")
    found: list[tuple[str, int, int]] = []
    for m in pat.finditer(t, start, end):
        if mask[m.start()]: continue
        b = m.end()
        if t[m.start():m.end()].endswith("section") and b < len(t) and t[b] == "*": b += 1
        while b < len(t) and t[b] in " \t": b += 1
        if b >= len(t) or t[b] != "{": continue
        close = match_brace(t, b)
        if close == -1: continue
        name = re.sub(r"\\[a-zA-Z]+\s*", "", t[b + 1:close]).replace("{", "").replace("}", "").strip()
        found.append((name, m.start(), close + 1))
    secs = []
    for i, (name, s0, b0) in enumerate(found):
        e = found[i + 1][1] if i + 1 < len(found) else end
        secs.append(Section(name=name, start=s0, body_start=b0, end=e))
    return secs


_ITEM_STOP = re.compile(r"\\item\b|\\end\{|\n[ \t]*\n|\n[ \t]*\\(?:resume\w*(?:Start|End)|resumeSubheading|resumeProjectHeading|cventry|hfill|vspace)\b")


def _trim_span(t: str, a: int, b: int) -> tuple[int, int]:
    while a < b and t[a] in " \t\n": a += 1
    while b > a and t[b - 1] in " \t\n": b -= 1
    # drop a trailing line break command and trailing comment-free whitespace
    if t[a:b].endswith("\\\\"): b -= 2
    while b > a and t[b - 1] in " \t\n": b -= 1
    return a, b


def parse(tex: str) -> TexDoc:
    t = tex
    mask = comment_mask(t)
    bd = t.find(r"\begin{document}")
    ed = t.rfind(r"\end{document}")
    preamble_end = bd if bd != -1 else 0
    body_start = bd + len(r"\begin{document}") if bd != -1 else 0
    body_end = ed if ed != -1 else len(t)
    clean = strip_comments(t)

    m = re.search(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", clean)
    docclass = m.group(1).strip() if m else ""
    packages = sorted({p.strip() for g in re.findall(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", clean) for p in g.split(",") if p.strip()})
    files = [(c, p.strip()) for c, p in FILE_CMD_RE.findall(clean)]
    unsafe = sorted({u for u in UNSAFE_RE.findall(clean)})
    for c, p in files:
        if p.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", p): unsafe.append(f"absolute path in \\{c}{{{p}}}")
        elif ".." in p.replace("\\", "/").split("/"): unsafe.append(f"parent-directory path in \\{c}{{{p}}}")

    bullet_macros = sorted(n for n in _one_arg_macros(t[:preamble_end]) if n.lower().endswith("item"))
    sections = _find_sections(t, mask, body_start, body_end)
    segs: list[Segment] = []
    counters = {"summary": 0, "skills": 0, "bullet": 0}

    def add(kind: SegKind, sec: Section, a: int, b: int):
        a, b = _trim_span(t, a, b)
        text = t[a:b]
        if not text or not re.search(r"[A-Za-z]{2}", re.sub(r"\\[a-zA-Z]+", "", text)): return
        if any(mask[a:b]): return                      # never edit spans containing comments
        if any(s.start < b and a < s.end for s in segs): return
        counters[kind] += 1
        segs.append(Segment(id=f"{_PREFIX[kind]}{counters[kind]}", kind=kind, section=sec.name, start=a, end=b, text=text))

    for sec in sections:
        name = sec.name
        is_sum, is_sk, is_bul = bool(SUMMARY_RE.search(name)), bool(SKILLS_RE.search(name)), bool(BULLET_SECTION_RE.search(name))
        if not (is_sum or is_sk or is_bul): continue
        kind: SegKind = "skills" if is_sk else "summary" if is_sum else "bullet"
        a, b = sec.body_start, sec.end
        # \item segments
        for im in re.finditer(r"\\item\b\s*(?:\[[^\]]*\])?", t[:b]):
            if im.start() < a or mask[im.start()]: continue
            stop = _ITEM_STOP.search(t, im.end(), b)
            add("skills" if is_sk else "bullet", sec, im.end(), stop.start() if stop else b)
        # \resumeItem{…}-style one-argument macros
        if bullet_macros:
            for mm in re.finditer(r"\\(" + "|".join(map(re.escape, bullet_macros)) + r")\s*(?=\{)", t[:b]):
                if mm.start() < a or mask[mm.start()]: continue
                close = match_brace(t, mm.end())
                if close != -1: add("skills" if is_sk else "bullet", sec, mm.end() + 1, close)
        if kind == "summary" and not any(s.section == name for s in segs):
            body = t[a:b]
            if "\\begin{" not in body: add("summary", sec, a, b)
        if kind == "skills" and not any(s.section == name for s in segs):
            pos = a
            for line in t[a:b].split("\n"):
                s0 = pos; pos += len(line) + 1
                st = line.strip()
                if not st or re.fullmatch(r"\\[a-zA-Z]+\*?(\{[^}]*\})?(\[[^\]]*\])?\s*", st): continue
                add("skills", sec, s0, s0 + len(line))

    segs.sort(key=lambda s: s.start)
    warnings = []
    if bd == -1: warnings.append("no \\begin{document} found")
    if not sections: warnings.append("no \\section headings found — tailoring needs Summary / Skills / Experience sections")
    elif not segs: warnings.append("found sections but no editable bullets, skills or summary text")
    if not balanced(t): warnings.append("unbalanced braces in source")
    return TexDoc(source=t, preamble_end=preamble_end, body_end=body_end, sections=sections, segments=segs,
                  documentclass=docclass, packages=packages, files=files, bullet_macros=bullet_macros,
                  needs_unicode_engine=bool(FONTSPEC_RE.search(clean)), unsafe=unsafe, warnings=warnings)


def apply_edits(doc: TexDoc, edits: dict[str, str]) -> str:
    """Splice new text into the given segment ids. Everything outside segments is byte-identical."""
    t = doc.source
    for seg in sorted((s for s in doc.segments if s.id in edits), key=lambda s: s.start, reverse=True):
        t = t[:seg.start] + edits[seg.id] + t[seg.end:]
    return t


def skills_items(text: str) -> list[str]:
    """Normalised skill items of a skills segment: 'Languages: Python, SQL' -> ['python', 'sql']."""
    s = re.sub(r"\\(textbf|textit|emph|texttt|textsc|small|footnotesize)\b", " ", text)
    s = s.replace("\\\\", "\n").replace("{", " ").replace("}", " ")
    s = re.sub(r"\\([&%$#_])", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+", " ", s)
    items: list[str] = []
    for line in s.split("\n"):                        # each line may carry its own 'Label:' prefix
        line = re.sub(r"^\s*[^,:;]{1,40}:", "", line)
        items += [i.strip().lower().rstrip(".") for i in re.split(r"[,;|·•]", line)]
    return sorted(i for i in items if i.strip(" .-:"))


def analyze(tex: str, assets: set[str] | None = None) -> dict:
    """JSON-friendly report for the UI."""
    d = parse(tex)
    assets = assets or set()
    missing = []
    if d.documentclass and d.documentclass not in STANDARD_CLASSES and f"{d.documentclass}.cls" not in assets:
        missing.append(f"{d.documentclass}.cls (custom document class — upload it, or a .zip of the project)")
    for c, p in d.files:
        if c in ("input", "include", "InputIfFileExists") and not any(a in (p, p + ".tex") for a in assets):
            missing.append(f"{p}{'' if p.endswith('.tex') else '.tex'} (\\{c})")
        if c == "includegraphics" and not any(a == p or a.rsplit(".", 1)[0] == p for a in assets):
            missing.append(f"{p} (image)")
    counts = {k: sum(1 for s in d.segments if s.kind == k) for k in ("summary", "skills", "bullet")}
    return {
        "documentclass": d.documentclass, "packages": d.packages,
        "engine_hint": "xelatex/tectonic (fontspec)" if d.needs_unicode_engine else "any (pdflatex / tectonic)",
        "sections": [{"name": s.name, "editable": sum(1 for g in d.segments if g.section == s.name)} for s in d.sections],
        "segments": counts, "editable_total": sum(counts.values()),
        "bullet_macros": d.bullet_macros, "unsafe": d.unsafe, "missing_assets": missing,
        "warnings": d.warnings, "lines": tex.count("\n") + 1,
    }
