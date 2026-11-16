"""Safe LaTeX resume tailoring with validation guards, compile check, repair, fallback."""
from __future__ import annotations
import re, shutil
from pathlib import Path
from pydantic import BaseModel
from config.settings import ARTIFACTS_DIR
from config.profile import load_profile, load_resume
from llm.client import LLMClient
from llm.structured import complete_json
from .latex import compile_tex, page_count
from .diff import unified

SYSTEM = r"""You tailor a LaTeX resume for ONE job. Strict rules:
- Edit ONLY: the Summary/Objective text, ordering/grouping of existing Skills, and phrasing of EXISTING Experience/Project bullets, to mirror the job description's terminology.
- NEVER invent or alter companies, schools, degrees, dates, job titles, or add tools/technologies/claims not present in the base resume or candidate profile.
- Keep the preamble, every \section name, all macros/packages unchanged. Keep LaTeX escaping correct (\%, \&, \$, \#, \_).
- Keep the resume the same length or shorter.
Return JSON: {"tex": "<full tailored .tex>", "summary": "<2-3 sentence description of what you changed>"}"""


class TailorOut(BaseModel):
    tex: str
    summary: str = ""


class TailorResult(BaseModel):
    tex: str; pdf_path: str; page_count: int
    summary: str; diff: str; fell_back: bool = False; note: str = ""


def _preamble(t: str) -> str: return t.split(r"\begin{document}")[0]
def _sections(t: str) -> list[str]: return re.findall(r"\\section\*?\{([^}]*)\}", t)
def _tokens(t: str) -> set[str]:
    body = re.sub(r"\\[a-zA-Z]+", " ", t)
    return {w.lower().rstrip(".-") for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}", body)}
def _balanced(t: str) -> bool:
    d = 0; i = 0
    while i < len(t):
        c = t[i]
        if c == "\\": i += 2; continue   # skip escaped char (\\, \{, \})
        if c == "%":                      # comment to end of line
            while i < len(t) and t[i] != "\n": i += 1
            continue
        if c == "{": d += 1
        elif c == "}":
            d -= 1
            if d < 0: return False
        i += 1
    return d == 0


def validate(base: str, new: str, profile_text: str = "", jd_text: str = "") -> list[str]:
    """Return list of guard violations (empty = safe)."""
    errs: list[str] = []
    if _preamble(base) != _preamble(new): errs.append("preamble changed")
    if _sections(base) != _sections(new): errs.append("section set/order changed")
    if r"\end{document}" not in new: errs.append("missing \\end{document}")
    if not _balanced(new): errs.append("unbalanced braces")
    allowed = _tokens(base) | _tokens(profile_text)
    # any new token not in base/profile and that looks like a proper noun or tech term is suspect;
    # plain English words are fine. Flag capitalized/tech-looking tokens only.
    raw_new = re.sub(r"\\[a-zA-Z]+", " ", new)
    suspicious = sorted({w.rstrip(".-") for w in re.findall(r"\b[A-Z][A-Za-z0-9+#.\-]+\b|\b\w*[+#]\w*\b", raw_new)
                         if w.lower().rstrip(".-") not in allowed and len(w.rstrip(".-")) > 2})
    if suspicious: errs.append(f"new proper-noun/tech terms not in base resume or profile: {suspicious[:8]}")
    # company/date lines must survive
    for d in set(re.findall(r"(?:19|20)\d{2}", base)):
        if d not in new: errs.append(f"date {d} missing")
    return errs


async def tailor_resume(job_id: str, jd_text: str, title: str, company: str,
                        llm: LLMClient | None = None, base: str | None = None) -> TailorResult:
    llm = llm or LLMClient.from_settings(task="tailor")
    base = base if base is not None else load_resume()
    profile_text = load_profile().model_dump_json()
    out_dir = ARTIFACTS_DIR / job_id; out_dir.mkdir(parents=True, exist_ok=True)
    base_pdf = out_dir / "base_resume.pdf"; (out_dir / "base_resume.tex").write_text(base)
    ok, log = await compile_tex(base, base_pdf)
    if not ok: raise RuntimeError(f"base resume does not compile: {log[-500:]}")
    base_pages = page_count(base_pdf)

    user = f"JOB: {title} at {company}\n\nJOB DESCRIPTION:\n{jd_text[:6000]}\n\nCANDIDATE PROFILE:\n{profile_text}\n\nBASE RESUME .tex:\n{base}"
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    summary, tex, note = "", base, ""
    for attempt in range(3):  # initial + 2 repair passes
        try:
            res = await complete_json(llm, TailorOut, msgs)
        except Exception as e:
            note = f"llm failure: {e}"; break
        tex, summary = res.tex, res.summary
        problems = validate(base, tex, profile_text)
        feedback = None
        if problems:
            feedback = "Rejected by safety checks: " + "; ".join(problems) + ". Fix and return full JSON again."
        else:
            tex_path = out_dir / "tailored_resume.tex"; tex_path.write_text(tex)
            ok, log = await compile_tex(tex, out_dir / "tailored_resume.pdf")
            if not ok:
                feedback = "LaTeX failed to compile:\n" + log[-1500:] + "\nFix the LaTeX."
            else:
                pages = page_count(out_dir / "tailored_resume.pdf")
                if pages > base_pages:
                    feedback = f"Compiled to {pages} pages but original is {base_pages}. Condense: shorten bullets and summary, keep facts."
                else:
                    return TailorResult(tex=tex, pdf_path=str(out_dir / "tailored_resume.pdf"), page_count=pages,
                                        summary=summary, diff=_save_diff(out_dir, base, tex), note=note)
        note = feedback
        msgs = msgs + [{"role": "assistant", "content": res.model_dump_json()}, {"role": "user", "content": feedback}]
    # fallback: base resume
    shutil.copy(base_pdf, out_dir / "tailored_resume.pdf"); (out_dir / "tailored_resume.tex").write_text(base)
    return TailorResult(tex=base, pdf_path=str(out_dir / "tailored_resume.pdf"), page_count=base_pages,
                        summary="Tailoring failed validation; using the untailored base resume.", diff="", fell_back=True, note=note or "")


def _save_diff(out_dir: Path, base: str, tex: str) -> str:
    d = unified(base, tex); (out_dir / "resume.diff").write_text(d); return d
