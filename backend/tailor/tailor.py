"""Segment-level résumé tailoring with a self-healing compile loop.

Pipeline
  1. parse the base .tex into editable segments (summary / skills / bullets); everything else is frozen
  2. the model returns edits for segment ids only — it never sees or rewrites the preamble or structure
  3. every edit is validated on its own; bad edits are dropped, good ones kept:
       - LaTeX: auto-escape bare & % # _, brace structure identical, no new \\commands
       - truth: no new numbers, no new proper nouns / technologies (vs. base résumé + profile)
       - skills: the set of skills is unchanged (re-ordering / re-grouping only)
       - length: at most +25 % per segment
  4. compile → if a LaTeX error points into a segment, revert just that segment and retry
  5. page overflow → one model "condense" pass on the edited segments, then deterministically revert
     the edits that grew the most until the page count matches the base
  6. final whole-document guard (validate) as defence in depth; only if *that* fails do we fall back to the base
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from config.profile import load_profile, load_resume
from config.settings import ARTIFACTS_DIR
from llm.client import LLMClient
from llm.structured import complete_json
from resume import texparse
from resume.compiler import CompileResult, compile_document
from resume.ingest import ASSETS_DIR
from .diff import unified

SYSTEM = r"""You tailor a LaTeX résumé to ONE job by rephrasing existing text segments.
You receive the job description and a JSON list of editable segments {id, kind, section, text}.
Return JSON {"edits": [{"id": "...", "text": "..."}], "summary": "2-3 sentences on what you changed"}.
Rules:
- Only include segments you actually change. Keep each segment's LaTeX commands exactly as they are (\textbf{..}, \href{..}{..}, \\ etc.).
- kind=summary: rewrite to foreground what the job needs, using only facts already in the résumé.
- kind=skills: you may ONLY reorder or regroup the existing skills (put the most relevant first). Never add or remove a skill.
- kind=bullet: rephrase to mirror the job's terminology. Keep every fact, metric and number exactly; add none.
- NEVER invent employers, titles, dates, numbers, tools or technologies that are not already in the résumé or profile.
- Escape LaTeX specials in prose: \%, \&, \#, \_. Keep each segment about the same length or shorter."""

CONDENSE = ("The tailored résumé is now {pages} page(s) but must fit in {base_pages}. Shorten these segments "
            "(keep every fact and number, drop filler words). Return the same JSON shape with shorter texts.")


class Edit(BaseModel):
    id: str
    text: str


class TailorEdits(BaseModel):
    edits: list[Edit] = Field(default_factory=list)
    summary: str = ""


class TailorResult(BaseModel):
    tex: str
    pdf_path: str
    page_count: int
    summary: str
    diff: str
    fell_back: bool = False
    note: str = ""
    report: dict = Field(default_factory=dict)


# ── guards ───────────────────────────────────────────────────────────────

def _cmds(t: str) -> set[str]:
    return set(re.findall(r"\\([A-Za-z]+)", t))


def _brace_shape(t: str) -> tuple[int, int]:
    depth = low = 0; i = 0
    while i < len(t):
        c = t[i]
        if c == "\\": i += 2; continue
        if c == "{": depth += 1
        elif c == "}": depth -= 1; low = min(low, depth)
        i += 1
    return depth, low


def _numbers(t: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", re.sub(r"\\[A-Za-z]+", " ", t)))


def _words(t: str) -> set[str]:
    body = re.sub(r"\\[a-zA-Z]+", " ", t)
    return {w.lower().strip(".-") for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]*", body)}


def _suspect_terms(new: str, allowed: set[str]) -> list[str]:
    raw = re.sub(r"\\[a-zA-Z]+", " ", new)
    cand = re.findall(r"\b[A-Z][A-Za-z0-9+#.\-]+|\b\w*[+#]\w*|\b[a-z]+[A-Z]\w*", raw)      # Proper, C++/C#, camelCase
    return sorted({w.strip(".-") for w in cand if w.lower().strip(".-") not in allowed and len(w.strip(".-")) > 2})


def check_edit(seg: texparse.Segment, new: str, base_numbers: set[str], allowed_words: set[str]) -> tuple[str, list[str]]:
    """Return (normalised_text, problems). Empty problems = accept."""
    new = texparse.fix_bare_specials(new.strip())
    problems: list[str] = []
    if not new: problems.append("empty")
    if _brace_shape(new) != _brace_shape(seg.text): problems.append("brace structure changed")
    extra_cmds = _cmds(new) - _cmds(seg.text) - {"textbf", "emph", "textit"}
    if extra_cmds: problems.append(f"new LaTeX commands {sorted(extra_cmds)}")
    new_nums = _numbers(new) - base_numbers
    if new_nums: problems.append(f"new numbers {sorted(new_nums)}")
    sus = _suspect_terms(new, allowed_words)
    if sus: problems.append(f"terms not in résumé/profile {sus[:6]}")
    if len(new) > len(seg.text) * 1.25 + 20: problems.append("too long (+25%)")
    if seg.kind == "skills" and texparse.skills_items(new) != texparse.skills_items(seg.text):
        problems.append("skills changed (only re-ordering allowed per line)")
    return new, problems


def _skills_set_ok(doc: texparse.TexDoc, edits: dict[str, str]) -> bool:
    before = sorted(i for s in doc.segments if s.kind == "skills" for i in texparse.skills_items(s.text))
    after = sorted(i for s in doc.segments if s.kind == "skills" for i in texparse.skills_items(edits.get(s.id, s.text)))
    return before == after


def validate(base: str, new: str, profile_text: str = "", jd_text: str = "") -> list[str]:
    """Whole-document guard (defence in depth)."""
    errs: list[str] = []
    pre = lambda t: t.split(r"\begin{document}")[0]
    secs = lambda t: [s.name for s in texparse.parse(t).sections]
    if pre(base) != pre(new): errs.append("preamble changed")
    if secs(base) != secs(new): errs.append("section set/order changed")
    if r"\end{document}" not in new: errs.append("missing \\end{document}")
    if not texparse.balanced(new) and texparse.balanced(base): errs.append("unbalanced braces")
    allowed = _words(base) | _words(profile_text)
    sus = _suspect_terms(new, allowed)
    if sus: errs.append(f"new proper-noun/tech terms not in base resume or profile: {sus[:8]}")
    for d in set(re.findall(r"(?:19|20)\d{2}", base)):
        if d not in new: errs.append(f"date {d} missing")
    return errs


# ── pipeline ─────────────────────────────────────────────────────────────

async def _compile(tex: str, pdf: Path) -> CompileResult:
    return await compile_document(tex, pdf, assets_dir=ASSETS_DIR if ASSETS_DIR.exists() else None)


async def _ask(llm: LLMClient, msgs: list[dict]) -> TailorEdits:
    return await complete_json(llm, TailorEdits, msgs)


def _accept(doc, proposal: TailorEdits, base_numbers, allowed, report) -> dict[str, str]:
    accepted: dict[str, str] = {}
    for e in proposal.edits:
        seg = doc.segment(e.id)
        if seg is None:
            report["rejected"].append({"id": e.id, "reason": "unknown segment id"}); continue
        text, problems = check_edit(seg, e.text, base_numbers, allowed)
        if problems:
            report["rejected"].append({"id": e.id, "section": seg.section, "reason": "; ".join(problems), "proposed": e.text[:300]})
        elif text != seg.text:
            accepted[e.id] = text
    if accepted and not _skills_set_ok(doc, accepted):
        for sid in [k for k in accepted if doc.segment(k).kind == "skills"]:
            report["rejected"].append({"id": sid, "reason": "skills set changed across lines"}); accepted.pop(sid)
    return accepted


async def tailor_resume(job_id: str, jd_text: str, title: str, company: str,
                        llm: LLMClient | None = None, base: str | None = None) -> TailorResult:
    llm = llm or LLMClient.from_settings(task="tailor")
    base = base if base is not None else load_resume()
    if not base.strip(): raise RuntimeError("no base résumé saved — add one under Setup")
    profile_text = load_profile().model_dump_json()
    out = ARTIFACTS_DIR / job_id; out.mkdir(parents=True, exist_ok=True)
    (out / "base_resume.tex").write_text(base)
    base_res = await _compile(base, out / "base_resume.pdf")
    if not base_res.ok: raise RuntimeError(f"base résumé does not compile: {base_res.summary()}")
    base_pages = base_res.pages

    doc = texparse.parse(base)
    report: dict = {"segments": len(doc.segments), "accepted": [], "rejected": [], "reverted": [], "passes": [],
                    "base_pages": base_pages}
    if not doc.segments:
        return _fallback(out, base, base_pages, "No editable Summary / Skills / Experience segments were found in your résumé.", report)

    base_numbers = _numbers(base) | _numbers(profile_text)
    allowed = _words(base) | _words(profile_text)
    segs = [s.as_prompt() for s in doc.segments]
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"JOB: {title} at {company}\n\nJOB DESCRIPTION:\n{jd_text[:6000]}\n\n"
                                        f"CANDIDATE PROFILE:\n{profile_text}\n\nSEGMENTS:\n{json.dumps(segs, ensure_ascii=False)}"}]
    try:
        proposal = await _ask(llm, msgs)
    except Exception as e:
        return _fallback(out, base, base_pages, f"model call failed: {e}", report)
    edits = _accept(doc, proposal, base_numbers, allowed, report)
    summary = proposal.summary.strip()
    report["passes"].append({"pass": "tailor", "proposed": len(proposal.edits), "accepted": len(edits)})

    pdf = out / "tailored_resume.pdf"
    condensed = False
    for _round in range(12):
        tex = texparse.apply_edits(doc, edits)
        res = await _compile(tex, pdf)
        if not res.ok:
            if not edits: break                     # base itself failed (transient) — give up below
            # Error line numbers refer to the *edited* source; re-parse it to map lines → segment ids.
            # Ids are stable because edits can't add structural commands (\item, \section …).
            edoc = texparse.parse(tex)
            hit = {s.id for e in res.errors if e.line for s in [edoc.segment_at_line(e.line)] if s} & edits.keys()
            for v in (hit or set(edits)):           # unknown location → revert all edits (the base compiles)
                report["reverted"].append({"id": v, "reason": f"compile error: {res.summary()}"}); edits.pop(v)
            continue
        if res.pages <= base_pages:
            break
        if not condensed and edits:
            condensed = True
            short_msgs = msgs + [{"role": "assistant", "content": TailorEdits(edits=[Edit(id=k, text=v) for k, v in edits.items()], summary=summary).model_dump_json()},
                                 {"role": "user", "content": CONDENSE.format(pages=res.pages, base_pages=base_pages)}]
            try:
                shorter = await _ask(llm, short_msgs)
                fixed = _accept(doc, shorter, base_numbers, allowed, report)
                edits.update({k: v for k, v in fixed.items() if len(v) <= len(edits.get(k, v))})
                report["passes"].append({"pass": "condense", "proposed": len(shorter.edits), "accepted": len(fixed)})
            except Exception as e:
                report["passes"].append({"pass": "condense", "error": str(e)[:200]})
            continue
        if not edits: break
        # deterministic: revert the edit that grew the most
        grow = max(edits, key=lambda k: len(edits[k]) - len(doc.segment(k).text))
        report["reverted"].append({"id": grow, "reason": f"page overflow ({res.pages} > {base_pages})"}); edits.pop(grow)

    tex = texparse.apply_edits(doc, edits)
    if not edits:
        return _fallback(out, base, base_pages, "No safe edits survived validation and compilation.", report)
    problems = validate(base, tex, profile_text)
    if problems:
        report["final_guard"] = problems
        return _fallback(out, base, base_pages, "Final document check failed: " + "; ".join(problems), report)
    res = await _compile(tex, pdf)
    if not res.ok or res.pages > base_pages:
        return _fallback(out, base, base_pages, f"final compile failed: {res.summary()}", report)

    report["accepted"] = [{"id": k, "section": doc.segment(k).section, "kind": doc.segment(k).kind,
                           "before": doc.segment(k).text, "after": v} for k, v in edits.items()]
    report.update(pages=res.pages, engine=res.engine, overfull=res.overfull)
    (out / "tailored_resume.tex").write_text(tex)
    (out / "tailor_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    note = f"{len(edits)} edit(s) applied, {len(report['rejected'])} rejected, {len(report['reverted'])} reverted"
    if not summary: summary = f"Rephrased {len(edits)} segment(s) to match the job description."
    return TailorResult(tex=tex, pdf_path=str(pdf), page_count=res.pages, summary=summary,
                        diff=_save_diff(out, base, tex), note=note, report=report)


def _fallback(out: Path, base: str, pages: int, why: str, report: dict) -> TailorResult:
    shutil.copy(out / "base_resume.pdf", out / "tailored_resume.pdf")
    (out / "tailored_resume.tex").write_text(base)
    report["fallback"] = why
    (out / "tailor_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return TailorResult(tex=base, pdf_path=str(out / "tailored_resume.pdf"), page_count=pages,
                        summary="Tailoring did not produce a safe edit; using your untailored base résumé.",
                        diff="", fell_back=True, note=why, report=report)


def _save_diff(out: Path, base: str, tex: str) -> str:
    d = unified(base, tex); (out / "resume.diff").write_text(d); return d
