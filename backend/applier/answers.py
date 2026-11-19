"""Answer bank: application-form questions and your answers, so each question is answered once and then reused.

A question is keyed by its normalized text ("Are you legally authorized to work in India? *" and "are you legally
authorized to work in india" are the same key). When a form is filled, saved answers are used first (matched to the
field's options), the rest go to the model together with your saved answers as facts about you. Required questions
nobody could answer are recorded with no answer; the job lists them in `jobs.waiting_on` and waits in review. Answer
them in the app (or type them into the paused form before approving: they are learned) and every job that was only
waiting on them is filled again.
"""
from __future__ import annotations
import re
import unicodedata
from sqlalchemy import func, or_, select
import db
from db import Job, SavedAnswer, utcnow
from .extract import Field

CHOICE_KINDS = ("select", "radio")
_YES = {"yes", "y", "true", "1", "checked", "on", "agree", "i agree", "accept"}
_NO = {"no", "n", "false", "0", "unchecked", "off"}
MAX_FACTS = 60


def _norm(s) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip()


def question_key(label: str) -> str:
    return _norm(re.sub(r"\((required|optional)\)", " ", label or "", flags=re.I))[:300]


def field_key(f: Field) -> str:
    """Bank key for a form field, or "" when it is not a question on its own (file uploads, one box of a group)."""
    if f.kind == "file" or (f.kind == "checkbox" and f.group): return ""
    return question_key(f.label or f.name)


def match_option(answer: str, options: list[str]) -> str | None:
    """The option a saved answer stands for: the same text (ignoring case and punctuation), else the single option
    that begins with it or that it begins with ("Yes" <-> "Yes, I am authorized to work here")."""
    a = _norm(answer)
    if not a or not options: return None
    for o in options:
        if _norm(o) == a: return o
    near = [o for o in options if _norm(o).startswith(a + " ") or a.startswith(_norm(o) + " ")]
    return near[0] if len(near) == 1 else None


def value_for(f: Field, answer: str):
    """A saved answer as the value to put in this field, or None when it does not fit (e.g. not among the options)."""
    if answer is None or not str(answer).strip(): return None
    if f.kind in CHOICE_KINDS: return match_option(answer, f.options)
    if f.kind == "checkbox":
        n = _norm(answer)
        return True if n in _YES else False if n in _NO else None
    if f.kind == "file": return None
    return str(answer)


def fits(kind: str, options: list | None, answer: str | None) -> bool:
    if answer is None or not str(answer).strip(): return False
    if kind in CHOICE_KINDS and options: return match_option(answer, options) is not None
    if kind == "checkbox": return _norm(answer) in _YES | _NO
    return True


# ── storage ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _sf(session_factory):
    return session_factory or db.SessionLocal


def load_bank(session_factory=None) -> dict[str, str]:
    """key -> answer, for every answered question."""
    with _sf(session_factory)() as s:
        return dict(s.execute(select(SavedAnswer.key, SavedAnswer.answer).where(SavedAnswer.answer.is_not(None))).all())


def facts(bank: dict[str, str], session_factory=None, limit: int = MAX_FACTS) -> list[dict]:
    """Saved answers as {question, answer} for the model (most used first)."""
    if not bank: return []
    with _sf(session_factory)() as s:
        rows = s.execute(select(SavedAnswer.question, SavedAnswer.answer).where(SavedAnswer.answer.is_not(None))
                         .order_by(SavedAnswer.times_seen.desc(), SavedAnswer.key).limit(limit)).all()
    return [{"question": q, "answer": a} for q, a in rows]


def lookup(fields: list[Field], bank: dict[str, str]) -> dict[str, object]:
    """field id -> value for every field the bank answers and whose answer fits the field."""
    out = {}
    for f in fields:
        k = field_key(f)
        if k and k in bank:
            v = value_for(f, bank[k])
            if v is not None: out[f.id] = v
    return out


def record_unanswered(questions: list[dict], session_factory=None) -> list[str]:
    """Remember required questions a form asked that nobody could answer. Returns their keys (for jobs.waiting_on).
    Each question: {key, label, kind, options}."""
    keys: list[str] = []
    now = utcnow()
    with _sf(session_factory)() as s:
        for q in questions:
            k = q.get("key") or question_key(q.get("label", ""))
            if not k or k in keys: continue
            keys.append(k)
            row = s.get(SavedAnswer, k)
            opts = list(q.get("options") or []) or None
            if row is None:
                s.add(SavedAnswer(key=k, question=(q.get("label") or k)[:1000], kind=q.get("kind") or "text",
                                  options=opts, answer=None, times_seen=1, last_seen_at=now))
            else:
                row.question, row.kind, row.last_seen_at = (q.get("label") or row.question)[:1000], q.get("kind") or row.kind, now
                row.times_seen = (row.times_seen or 0) + 1
                if opts: row.options = opts
        s.commit()
    return keys


def save_answer(key: str, answer: str | None, source: str = "you", question: str | None = None,
                session_factory=None) -> SavedAnswer:
    key = key if question is None else (key or question_key(question))
    if not key: raise ValueError("empty question")
    with _sf(session_factory)() as s:
        row = s.get(SavedAnswer, key)
        if row is None:
            row = SavedAnswer(key=key, question=(question or key)[:1000], kind="text", times_seen=0)
            s.add(row)
        row.answer = None if answer is None or not str(answer).strip() else str(answer).strip()[:5000]
        row.source = source if row.answer is not None else ""
        if question: row.question = question[:1000]
        s.commit()
        return row


def delete_answer(key: str, session_factory=None) -> bool:
    with _sf(session_factory)() as s:
        row = s.get(SavedAnswer, key)
        if row is None: return False
        s.delete(row); s.commit(); return True


def as_dict(r: SavedAnswer) -> dict:
    return {"key": r.key, "question": r.question, "kind": r.kind, "options": r.options or [], "answer": r.answer,
            "source": r.source, "times_seen": r.times_seen, "fits": fits(r.kind, r.options, r.answer),
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None}


def listing(session_factory=None) -> dict:
    with _sf(session_factory)() as s:
        rows = [as_dict(r) for r in s.scalars(select(SavedAnswer).order_by(SavedAnswer.times_seen.desc(), SavedAnswer.key))]
        waiting = {}
        for keys in s.scalars(select(Job.waiting_on).where(Job.status == "needs_review", Job.waiting_on.is_not(None))):
            for k in keys or []: waiting[k] = waiting.get(k, 0) + 1
    for r in rows: r["jobs_waiting"] = waiting.get(r["key"], 0)
    return {"pending": [r for r in rows if not r["fits"]], "saved": [r for r in rows if r["fits"]]}


def pending_count(session_factory=None) -> int:
    try:
        with _sf(session_factory)() as s:
            rows = s.execute(select(SavedAnswer.kind, SavedAnswer.options, SavedAnswer.answer).where(or_(
                SavedAnswer.answer.is_(None), SavedAnswer.kind.in_(CHOICE_KINDS + ("checkbox",))))).all()
    except Exception:   # e.g. tables not created yet
        return 0
    return sum(not fits(k, o, a) for k, o, a in rows)


def questions_for(keys: list[str] | None, session_factory=None) -> list[dict]:
    if not keys: return []
    with _sf(session_factory)() as s:
        rows = {r.key: r for r in s.scalars(select(SavedAnswer).where(SavedAnswer.key.in_(keys)))}
    return [as_dict(rows[k]) for k in keys if k in rows]


def ready_to_refill(session_factory=None) -> list[str]:
    """Jobs paused in review only because of questions that now all have fitting answers."""
    with _sf(session_factory)() as s:
        jobs = s.execute(select(Job.id, Job.waiting_on).where(Job.status == "needs_review", Job.waiting_on.is_not(None))).all()
        keys = {k for _, w in jobs for k in (w or [])}
        rows = {r.key: r for r in s.scalars(select(SavedAnswer).where(SavedAnswer.key.in_(keys)))} if keys else {}
    ok = {k for k, r in rows.items() if fits(r.kind, r.options, r.answer)}
    return [jid for jid, w in jobs if w and all(k in ok for k in w)]


def learn(fields: list[Field], keys: set[str], session_factory=None) -> int:
    """Save what you typed into a paused form for the questions it was waiting on (called just before submitting).
    Returns the number of answers learned."""
    n = 0
    for f in fields:
        k = field_key(f)
        v = (f.current or "").strip()
        if not k or k not in keys or not v: continue
        if f.kind == "checkbox": v = "Yes" if v.lower() in _YES else "No"
        save_answer(k, v, source="learned", session_factory=session_factory)
        n += 1
    return n


def count(session_factory=None) -> int:
    with _sf(session_factory)() as s:
        return s.scalar(select(func.count()).select_from(SavedAnswer)) or 0
