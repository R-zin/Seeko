"""Cover letter generation, grounded strictly in the resume and profile."""
from __future__ import annotations
from pydantic import BaseModel
from config.profile import load_profile, load_resume
from config.settings import ARTIFACTS_DIR
from llm.client import LLMClient
from llm.structured import complete_json

SYSTEM = """Write a concise cover letter (150-250 words, plain text, no placeholders) for the candidate.
Use ONLY facts present in the resume and candidate profile. Never invent employers, skills, metrics, or motivations beyond
what the job description and resume support. If something is unknown, omit it. Return JSON {"letter": "..."}."""


class Letter(BaseModel):
    letter: str


async def generate_cover_letter(job_id: str, title: str, company: str, jd_text: str,
                                llm: LLMClient | None = None, resume: str | None = None) -> str:
    llm = llm or LLMClient.from_settings(task="tailor")
    resume = resume if resume is not None else load_resume()
    user = f"JOB: {title} at {company}\n\nJD:\n{jd_text[:4000]}\n\nPROFILE:\n{load_profile().model_dump_json()}\n\nRESUME:\n{resume[:6000]}"
    out = await complete_json(llm, Letter, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
    d = ARTIFACTS_DIR / job_id; d.mkdir(parents=True, exist_ok=True)
    (d / "cover_letter.txt").write_text(out.letter)
    return out.letter
