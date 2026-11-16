from __future__ import annotations
from pydantic import BaseModel
from config.profile import CandidateProfile
from llm.client import LLMClient
from llm.structured import complete_json
from .extract import Field


class Answer(BaseModel):
    id: str
    value: str | bool | None = None   # null = do not know -> human needed
    confident: bool = True


class FieldMap(BaseModel):
    answers: list[Answer]


SYSTEM = """You fill a job application form for a candidate. For each field return an answer using ONLY facts in the
candidate profile and resume. Rules:
- Never invent facts, employers, skills, numbers, or personal data. If the answer is not supported, return value null.
- select/radio: value must be exactly one of the listed options. checkbox: true/false. file inputs: skip (handled separately).
- Open-ended questions ("why this company", project descriptions): 2-4 truthful sentences grounded in the resume and the job description.
- Legal/EEO/demographic questions: choose a 'decline to answer' style option if available, else null.
Return JSON {"answers": [{"id": "...", "value": ..., "confident": true|false}]}."""


async def map_fields(fields: list[Field], profile: CandidateProfile, resume_tex: str, job_title: str,
                     company: str, jd_text: str, llm: LLMClient | None = None) -> FieldMap:
    llm = llm or LLMClient.from_settings(task="applier")
    fs = [f.model_dump() for f in fields if f.kind != "file"]
    user = (f"JOB: {job_title} at {company}\nJD (truncated):\n{jd_text[:3000]}\n\nPROFILE:\n{profile.model_dump_json()}\n\n"
            f"RESUME:\n{resume_tex[:6000]}\n\nFIELDS:\n{fs}")
    return await complete_json(llm, FieldMap, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
