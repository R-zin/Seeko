from pathlib import Path
import pytest
from playwright.async_api import async_playwright
from applier.engine import fill_application, submit
from applier.detect import detect_page, detect_blockers, detect_ats_from_url
from applier.mapper import FieldMap, Answer
from applier import engine as E
from config.profile import CandidateProfile

FX = Path(__file__).parent / "fixtures"
PROFILE = CandidateProfile.model_validate({"personal": {"name": "Jane Doe", "email": "jane@example.com", "phone": "555"}})
JOB = {"title": "Engineer", "company": "Acme", "jd_text": "python"}


@pytest.fixture
async def page(tmp_path):
    async with async_playwright() as pw:
        b = await pw.chromium.launch(); p = await b.new_page()
        yield p
        await b.close()


@pytest.fixture
def pdf(tmp_path):
    f = tmp_path / "r.pdf"; f.write_bytes(b"%PDF-1.4 test"); return f


def stub_mapper(monkeypatch, answers):
    async def fake(fields, *a, **k): return FieldMap(answers=answers(fields))
    monkeypatch.setattr(E, "map_fields", fake)


def by_label(fields, table):
    out = []
    for f in fields:
        for key, val in table.items():
            if key.lower() in f.label.lower(): out.append(Answer(id=f.id, value=val))
    return out


def test_url_detection():
    assert detect_ats_from_url("https://boards.greenhouse.io/x/jobs/1") == "greenhouse"
    assert detect_ats_from_url("https://x.myworkdayjobs.com/a") == "workday"


async def test_greenhouse_fill_does_not_submit(page, pdf, monkeypatch, tmp_path):
    await page.goto((FX / "greenhouse_form.html").as_uri())
    stub_mapper(monkeypatch, lambda fs: by_label(fs, {"First Name": "Jane", "Email": "jane@example.com", "authorized": "Yes",
                                                      "sponsorship": "No", "Why do you": "I like building APIs.", "privacy": True}))
    res = await fill_application(page, PROFILE, "", pdf, JOB, screenshot_to=tmp_path / "s.png")
    assert res.ats == "greenhouse" and res.ready and res.filled >= 6
    assert await page.input_value("#fn") == "Jane" and await page.input_value("#auth") == "Yes"
    assert await page.is_checked("input[name=sp][value=n]") and await page.is_checked("input[name=agree]")
    assert Path(res.screenshot).exists() and await page.title() != "SUBMITTED"   # never submitted
    assert await submit(page, dry_run=True) is False and await page.title() != "SUBMITTED"
    assert await submit(page) is True and await page.title() == "SUBMITTED"       # only on explicit approval


async def test_lever_flags_captcha_and_unknown_required(page, pdf, monkeypatch):
    await page.goto((FX / "lever_form.html").as_uri())
    stub_mapper(monkeypatch, lambda fs: by_label(fs, {"Full name": "Jane Doe", "Email": "jane@example.com"}))
    res = await fill_application(page, PROFILE, "", pdf, JOB)
    assert "captcha" in res.blockers and not res.ready
