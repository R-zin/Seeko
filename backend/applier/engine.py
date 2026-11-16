"""Playwright form filler. Fills, screenshots, and (only if allowed) submits."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from config.profile import CandidateProfile
from llm.client import LLMClient
from .detect import detect_page, detect_blockers
from .extract import extract_fields, reveal_form, Field, loc
from .mapper import map_fields

SUBMIT_SEL = ("button[type=submit], input[type=submit], button:has-text('Submit application'), "
              "button:has-text('Submit'), button:has-text('Apply'), [data-qa='btn-submit']")
NEXT_SEL = "button:has-text('Next'), button:has-text('Continue'), button:has-text('Save and Continue')"


@dataclass
class FillResult:
    ats: str = "generic"; filled: int = 0
    needs_human: list[str] = field(default_factory=list)   # field labels we could not answer
    blockers: list[str] = field(default_factory=list)      # captcha / 2fa / login
    screenshot: str = ""; submitted: bool = False; steps: int = 1

    @property
    def ready(self) -> bool: return not self.needs_human and not self.blockers


def file_role(f: Field, n_files: int) -> str:
    """Classify a file input as resume / cover_letter / other using label, name and DOM id."""
    text = f"{f.label} {f.name} {f.dom_id}".lower()
    if "cover" in text: return "cover_letter"
    if any(k in text for k in ("resume", "résumé", "cv")): return "resume"
    return "resume" if n_files == 1 else "other"


async def _fill_one(page, f: Field, value) -> bool:
    el = loc(page, f)
    try:
        if f.kind == "select":
            await el.select_option(label=str(value)); return True
        if f.kind == "radio":
            radios = page.frames[f.frame].locator(f'input[type=radio][name="{f.name}"]')
            for i in range(await radios.count()):
                r = radios.nth(i); txt = ((await r.evaluate("e => (e.labels && e.labels[0] ? e.labels[0].innerText : e.value)")) or "").strip()
                if txt.lower() == str(value).strip().lower(): await r.check(); return True
            return False
        if f.kind == "checkbox":
            await (el.check() if value in (True, "true", "True", "yes") else el.uncheck()); return True
        await el.fill(str(value)); return True
    except Exception: return False


async def fill_application(page, profile: CandidateProfile, resume_tex: str, pdf_path: str | Path, job: dict,
                           llm: LLMClient | None = None, screenshot_to: Path | None = None, max_steps: int = 6) -> FillResult:
    res = FillResult(ats=await detect_page(page))
    for step in range(1, max_steps + 1):
        res.steps = step
        res.blockers = await detect_blockers(page)
        if res.blockers: break
        fields = await reveal_form(page) if step == 1 else await extract_fields(page)
        file_fields = [f for f in fields if f.kind == "file"]
        for f in fields:
            if f.kind == "file":
                role = file_role(f, len(file_fields))
                if role != "resume":     # cover letter / other attachments: never put the resume there
                    if f.required: res.needs_human.append(f"{f.label or f.name} ({role} upload)")
                    continue
                try: await loc(page, f).set_input_files(str(pdf_path)); res.filled += 1
                except Exception: res.needs_human.append(f.label or f.name)
        fm = await map_fields(fields, profile, resume_tex, job["title"], job["company"], job.get("jd_text", ""), llm)
        by_id = {a.id: a for a in fm.answers}
        for f in fields:
            if f.kind == "file": continue
            a = by_id.get(f.id)
            if a is None or a.value is None or a.value == "" or not a.confident:
                if f.required: res.needs_human.append(f.label or f.name)
                continue
            if await _fill_one(page, f, a.value): res.filled += 1
            elif f.required: res.needs_human.append(f.label or f.name)
        nxt = page.locator(NEXT_SEL)
        if await nxt.count() and not await page.locator(SUBMIT_SEL).filter(has_text="Submit").count() and res.ready:
            await nxt.first.click(); await page.wait_for_load_state("domcontentloaded"); continue
        break
    if screenshot_to:
        screenshot_to.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_to), full_page=True); res.screenshot = str(screenshot_to)
    return res


async def submit(page, dry_run: bool = False) -> bool:
    """Click final submit. Never called in approval mode until the user approves."""
    if dry_run: return False
    btn = page.locator(SUBMIT_SEL).last
    await btn.click(); await page.wait_for_load_state("domcontentloaded"); return True
