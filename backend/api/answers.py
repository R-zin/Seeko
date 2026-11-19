from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from applier import answers
from workers import pipeline

router = APIRouter(prefix="/api/answers")


class NewAnswer(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    answer: str = Field(max_length=5000)


class AnswerIn(BaseModel):
    answer: str | None = Field(None, max_length=5000)


@router.get("")
def listing():
    """Questions waiting for you (no answer, or an answer that does not fit the form's options) and saved answers."""
    return answers.listing()


@router.post("")
async def add(b: NewAnswer):
    """Save an answer before any form asks (e.g. "Notice period" -> "30 days")."""
    row = answers.save_answer("", b.answer, question=b.question)
    return {"ok": True, "key": row.key, "requeued": await pipeline.refill_ready()}


@router.put("/{key}")
async def answer(key: str, b: AnswerIn):
    """Answer (or clear) a question. Jobs that were only waiting on answered questions are filled again."""
    if not answers.questions_for([key]): raise HTTPException(404, "unknown question")
    answers.save_answer(key, b.answer)
    return {"ok": True, "requeued": await pipeline.refill_ready()}


@router.delete("/{key}")
def remove(key: str):
    if not answers.delete_answer(key): raise HTTPException(404, "unknown question")
    return {"ok": True}
