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


async def test_form_inside_iframe_behind_apply_button_and_cookie_banner(page, pdf, monkeypatch):
    await page.goto((FX / "iframe_host.html").as_uri())
    stub_mapper(monkeypatch, lambda fs: by_label(fs, {"First Name": "Jane", "Email": "jane@example.com", "authorized": "Yes",
                                                      "sponsorship": "No", "Why do you": "Because.", "privacy": True}))
    res = await fill_application(page, PROFILE, "", pdf, JOB)
    assert res.ready and res.filled >= 6
    inner = page.frames[1]
    assert await inner.input_value("#fn") == "Jane" and await inner.is_checked("input[name=sp][value=n]")
    assert await page.locator("#cookie").count() == 0                      # banner dismissed


def test_labels_are_cleaned():
    import asyncio
    async def go():
        async with async_playwright() as pw:
            b = await pw.chromium.launch(); p = await b.new_page()
            await p.set_content("<label>Full name\n<span>✱</span><input name=n required></label>")
            from applier.extract import extract_fields
            fs = await extract_fields(p); await b.close(); return fs
    assert asyncio.run(go())[0].label == "Full name"


def test_file_role_never_sends_resume_to_cover_letter():
    from applier.engine import file_role
    from applier.extract import Field
    mk = lambda **k: Field(id="0", kind="file", **k)
    assert file_role(mk(label="Attach", dom_id="resume"), 2) == "resume"
    assert file_role(mk(label="Attach", dom_id="cover_letter"), 2) == "cover_letter"
    assert file_role(mk(label="Attach"), 2) == "other"          # ambiguous with 2 inputs -> human
    assert file_role(mk(label="Upload"), 1) == "resume"         # sole file input


async def test_iframe_greenhouse_detected(page):
    await page.set_content('<iframe src="https://boards.greenhouse.io/embed/job_app?for=x"></iframe>')
    from applier.detect import detect_ats_from_url
    assert detect_ats_from_url("https://boards.greenhouse.io/embed/job_app?for=x") == "greenhouse"


async def test_checkbox_group_label_is_extracted(page):
    from applier.extract import extract_fields
    await page.set_content('''<ul><li class="application-question"><div class="application-label">Pronouns <span>✱</span></div>
      <label><input type=checkbox name=p value=a>He/him</label><label><input type=checkbox name=p value=b>They/them</label></li></ul>''')
    fs = await extract_fields(page)
    assert [f.group for f in fs] == ["Pronouns", "Pronouns"] and [f.label for f in fs] == ["He/him", "They/them"]
