"""Answer bank: saved answers fill forms without the model, unanswered questions are recorded, answers typed into a
paused form are learned, and answering re-queues the jobs that were waiting."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import db
from applier import answers as A
from applier import engine as E
from applier.extract import Field, extract_fields
from applier.mapper import Answer, FieldMap
from db import Job, SavedAnswer
from tests.test_applier import FX, JOB, PROFILE


@pytest.fixture
def SL(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}"); db.migrate(eng)
    yield sessionmaker(eng, expire_on_commit=False)
    eng.dispose()


@pytest.fixture
async def page():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(); p = await b.new_page()
        yield p
        await b.close()


def F(label, kind="text", options=(), **kw):
    return Field(id=kw.pop("id", "1"), kind=kind, label=label, options=list(options), **kw)


def test_keys_and_option_matching():
    assert A.question_key("Are you legally authorized to work in India? *") == A.question_key("are you legally authorized to work in india")
    assert A.question_key("Notice period (required)") == "notice period"
    assert A.match_option("yes", ["Yes", "No"]) == "Yes"
    assert A.match_option("Yes", ["Yes, I am authorized", "No, I am not"]) == "Yes, I am authorized"
    assert A.match_option("Yes, I am authorized to work", ["Yes", "No"]) == "Yes"
    assert A.match_option("Maybe", ["Yes", "No"]) is None
    assert A.match_option("Yes", ["Yes - full time", "Yes - part time"]) is None     # ambiguous: leave it to you
    assert A.value_for(F("Relocate?", "checkbox"), "No") is False and A.value_for(F("x", "checkbox"), "perhaps") is None
    assert A.value_for(F("Notice", "text"), "30 days") == "30 days" and A.value_for(F("CV", "file"), "x") is None
    assert A.field_key(F("Pronouns", "checkbox", group="Pronouns")) == ""            # one box of a group is not a question


def test_record_answer_and_refill_readiness(SL):
    keys = A.record_unanswered([{"key": "notice period", "label": "Notice period*", "kind": "text"},
                                {"key": "authorized", "label": "Authorized?", "kind": "select", "options": ["Yes", "No"]}], SL)
    A.record_unanswered([{"key": "notice period", "label": "Notice period", "kind": "text"}], SL)
    with SL() as s:
        s.add(Job(id="j1", source="x", company="c", title="t", url="u1", canonical_url="u1", dedupe_key="c|t",
                  status="needs_review", waiting_on=keys)); s.commit()
    lst = A.listing(SL)
    assert [q["key"] for q in lst["pending"]] == ["notice period", "authorized"] and lst["pending"][0]["times_seen"] == 2
    assert lst["pending"][0]["jobs_waiting"] == 1 and A.pending_count(SL) == 2
    A.save_answer("notice period", "30 days", session_factory=SL)
    A.save_answer("authorized", "Maybe", session_factory=SL)          # does not fit the options: still pending
    assert A.ready_to_refill(SL) == [] and A.pending_count(SL) == 1
    A.save_answer("authorized", "yes", session_factory=SL)
    assert A.ready_to_refill(SL) == ["j1"] and A.pending_count(SL) == 0
    assert A.load_bank(SL) == {"notice period": "30 days", "authorized": "yes"}
    assert [f["question"] for f in A.facts(A.load_bank(SL), SL)] == ["Notice period", "Authorized?"]


async def test_saved_answers_fill_the_form_and_the_rest_is_recorded(page, monkeypatch, tmp_path):
    await page.goto((FX / "greenhouse_form.html").as_uri())
    seen = {}

    async def mapper(fields, *a, facts=None, **k):
        seen["labels"], seen["facts"] = [f.label for f in fields], facts
        return FieldMap(answers=[Answer(id=f.id, value=v) for f in fields
                                 for key, v in {"First Name": "Jane", "Email": "jane@example.com"}.items() if key in f.label])
    monkeypatch.setattr(E, "map_fields", mapper)
    pdf = tmp_path / "r.pdf"; pdf.write_bytes(b"%PDF-1.4 test")
    bank = {A.question_key("Are you legally authorized to work in the US?"): "Yes, I am",
            A.question_key("Will you require sponsorship?"): "no"}
    facts = [{"question": "Notice period", "answer": "30 days"}]
    res = await E.fill_application(page, PROFILE, "", pdf, JOB, bank=bank, bank_facts=facts)
    assert res.from_bank == 2 and await page.input_value("#auth") == "Yes" and await page.is_checked("input[name=sp][value=n]")
    assert not any("authorized" in x or "sponsorship" in x for x in seen["labels"]) and seen["facts"] == facts
    assert [q["key"] for q in res.questions] == ["why do you want to work here"] and res.questions[0]["kind"] == "textarea"
    assert not res.ready

    # What a control holds is read back the way a person sees it (used to learn your answers)
    await page.fill("#why", "I build the APIs your customers use.")
    cur = {f.label: f.current for f in await extract_fields(page)}
    assert cur["Are you legally authorized to work in the US?"] == "Yes" and cur["Will you require sponsorship?"] == "No"
    assert cur["Why do you want to work here?"].startswith("I build") and cur["I agree to the privacy policy"] == ""


async def test_pipeline_learns_typed_answers_and_requeues_waiting_jobs(page, SL, monkeypatch):
    from workers import pipeline as P
    monkeypatch.setattr(P, "SessionLocal", SL); monkeypatch.setattr(P, "log", lambda *a, **k: None)
    await page.goto((FX / "greenhouse_form.html").as_uri())
    keys = A.record_unanswered([{"key": "why do you want to work here", "label": "Why do you want to work here?", "kind": "textarea"},
                                {"key": "will you require sponsorship", "label": "Will you require sponsorship?", "kind": "radio",
                                 "options": ["Yes", "No"]}], SL)
    with SL() as s:
        s.add(Job(id="a", source="x", company="c", title="a", url="ua", canonical_url="ua", dedupe_key="c|a",
                  status="needs_review", waiting_on=keys))
        s.add(Job(id="b", source="x", company="c", title="b", url="ub", canonical_url="ub", dedupe_key="c|b",
                  status="needs_review", waiting_on=["why do you want to work here"])); s.commit()
    await page.fill("#why", "Because of the product."); await page.check("input[name=sp][value=n]")
    assert await P.learn_from_page("a", page) == 2
    with SL() as s:
        row = s.get(SavedAnswer, "will you require sponsorship")
        assert (row.answer, row.source) == ("No", "learned")
    assert sorted(await P.refill_ready()) == ["a", "b"]           # both forms can now be filled without you
    with SL() as s:
        assert {j.status for j in s.query(Job)} == {"tailored"} and s.get(Job, "a").waiting_on is None


def test_answers_api():
    from main import app
    with TestClient(app) as c:
        r = c.post("/api/answers", json={"question": "What is your notice period?", "answer": "30 days"})
        assert r.status_code == 200 and r.json()["key"] == "what is your notice period"
        lst = c.get("/api/answers").json()
        assert any(x["key"] == "what is your notice period" and x["answer"] == "30 days" for x in lst["saved"])
        assert c.put("/api/answers/what is your notice period", json={"answer": "60 days"}).json()["ok"]
        assert c.put("/api/answers/nope", json={"answer": "x"}).status_code == 404
        assert c.delete("/api/answers/what is your notice period").json()["ok"]
        assert c.post("/api/pipeline/jobs/nope/refill").status_code == 409
