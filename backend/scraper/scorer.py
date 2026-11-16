from __future__ import annotations
from config.profile import load_resume
from resume.texparse import plain_text
from llm.client import LLMClient
from llm.structured import complete_json
from schemas import Score

SYSTEM = ("You score how well a candidate's resume matches a job description. Return JSON: score (0-100 integer), "
          "matched_keywords, missing_keywords (important JD skills absent from the resume), reasoning (1-2 sentences). "
          "Be strict and honest; do not credit skills not on the resume.")


async def score_job(jd_text: str, title: str, company: str, resume: str | None = None, llm: LLMClient | None = None) -> Score:
    llm = llm or LLMClient.from_settings(task="scoring")
    resume = resume if resume is not None else load_resume()
    return await complete_json(llm, Score, [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"JOB: {title} at {company}\n\nJD:\n{jd_text[:6000]}\n\nRESUME:\n{plain_text(resume)[:8000]}"}])
