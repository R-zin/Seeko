"""Pure parts of the job search: classification, extraction, block detection, link discovery, query planning."""
from datetime import datetime
import pytest
from jobsearch import classify as C
from jobsearch.extract import detect_block, extract_posting, jsonld_postings, parse_date
from jobsearch.graph import board_queries, build_tasks
from jobsearch.leads import board_leads, careers_leads
from jobsearch.spec import SearchSpec
from scraper.boards import BOARDS
from scraper.textutil import html_to_text

NOW = datetime(2026, 11, 18)
JD = "We build reliable APIs in Python and PostgreSQL. " * 10


@pytest.mark.parametrize("title,etype,url,want", [
    ("Software Engineering Intern", "", "", "internship"),
    ("Summer Internship - Data", "", "", "internship"),
    ("Graduate Trainee, Finance", "", "", "internship"),
    ("Backend Engineer", "INTERN", "", "internship"),
    ("Backend Engineer", "", "https://internshala.com/internship/detail/x", "internship"),
    ("International Sales Manager", "FULL_TIME", "", "job"),     # "intern" inside a word is not an internship
    ("Internal Tools Engineer", "", "", "job"),
    ("Senior Software Engineer", "", "", "job"),
])
def test_classify_job_type(title, etype, url, want):
    assert C.classify_job_type(title, etype, url) == want


@pytest.mark.parametrize("title,ok", [
    ("Senior Software Development Engineer II", True),
    ("Software Engineering Intern", True),
    ("Software Developer (Backend)", True),
    ("SDE Intern", True),
    ("SWE, Infrastructure", True),
    ("Sales Engineer", False),                 # one shared word is not enough for a two-word target
    ("Software Sales Representative", False),
    ("Data Engineer", False),
])
def test_title_matches_is_strict_but_knows_synonyms(title, ok):
    assert C.title_matches(title, ["Software Engineer"]) is ok


def test_title_matching_longer_targets_and_ml_synonyms():
    assert C.title_matches("Machine Learning Engineer", ["ML Engineer"])
    assert C.title_matches("Backend Python Engineer", ["Senior Backend Python Developer"])      # 3 of 3
    assert C.title_matches("Python Backend Developer, Payments", ["Backend Python Developer Payments"])  # 3 of 4
    assert not C.title_matches("Frontend Developer", ["Backend Python Developer Payments"])
    assert C.title_matches("Anything at all", [])                                            # no targets


def test_location_rules():
    assert C.location_ok("Bengaluru, Karnataka, India", False, ["Bangalore"], False)
    assert C.location_ok("Gurugram", False, ["Gurgaon", "Pune"], False)
    assert not C.location_ok("Berlin, Germany", False, ["Bangalore"], False)
    assert C.location_ok("", False, ["Bangalore"], False)                  # unknown is kept
    assert C.location_ok("Anywhere", True, ["Bangalore", "Remote"], False)
    assert not C.location_ok("Pune", False, ["Remote"], False)             # only remote wanted
    assert not C.location_ok("Bengaluru", False, [], True)                 # remote only
    assert C.location_ok("Remote - India", True, [], True)


def test_freshness():
    assert C.freshness_problem(None, datetime(2026, 11, 1), 30, NOW) == "expired"
    assert C.freshness_problem(datetime(2026, 9, 1), None, 30, NOW) == "too_old"
    assert C.freshness_problem(datetime(2026, 9, 1), None, 0, NOW) is None
    assert C.freshness_problem(datetime(2026, 11, 10), datetime(2026, 12, 31), 30, NOW) is None


def test_screen_reasons():
    spec = SearchSpec(role_titles=["Backend Engineer"], job_types=["job"], excluded_companies=["Acme Inc"]).model_dump()
    base = {"title": "Backend Engineer", "company": "Globex", "location": "", "url": "https://x.test/j/1", "jd_text": JD}
    assert C.screen(base, spec, NOW)[0]
    assert C.screen(base | {"jd_text": "short"}, spec, NOW)[1] == "no_description"
    assert C.screen(base | {"company": "ACME"}, spec, NOW)[1] == "excluded_company"
    assert C.screen(base | {"title": "Backend Engineer Intern"}, spec, NOW)[1] == "unwanted_internship"
    assert C.screen(base | {"title": "Office Manager"}, spec, NOW)[1] == "irrelevant_title"
    assert C.screen(base | {"valid_through": "2026-01-01"}, spec, NOW)[1] == "expired"
    ok, _, p = C.screen(base | {"location": "Remote"}, spec, NOW)
    assert ok and p["is_remote"] and p["job_type"] == "job" and 0 < p["relevance"] <= 1


LD_PAGE = """<html><head><title>x</title>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"Acme"},
{"@type":["JobPosting"],"title":"Data Science Intern","hiringOrganization":{"@type":"Organization","name":"Acme &amp; Co"},
"employmentType":["INTERN","FULL_TIME"],"datePosted":"2026-11-02T10:00:00+05:30","validThrough":"2026-12-31",
"jobLocation":[{"@type":"Place","address":{"addressLocality":"Kochi","addressRegion":"Kerala","addressCountry":{"name":"India"}}},
{"@type":"Place","address":{"addressLocality":"Bengaluru","addressCountry":"IN"}}],
"description":"&lt;p&gt;Work with &lt;b&gt;pandas&lt;/b&gt;.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"}]}
</script></head><body><h1>Data Science Intern</h1></body></html>"""


def test_jsonld_extraction():
    p = extract_posting(LD_PAGE, "https://acme.test/jobs/1")
    assert p["jd_source"] == "json-ld" and p["title"] == "Data Science Intern" and p["company"] == "Acme & Co"
    assert p["employment_type"] == "INTERN,FULL_TIME" and C.classify_job_type(p["title"], p["employment_type"]) == "internship"
    assert p["location"] == "Kochi, Kerala, India / Bengaluru, IN"
    assert p["posted_at"] == "2026-11-02T04:30:00" and p["valid_through"] == "2026-12-31T00:00:00"
    assert p["jd_text"] == "Work with pandas.\n\n• SQL\n• Python"


def test_jsonld_tolerates_raw_newlines_and_trailing_commas():
    html = '<script type="application/ld+json">{"@type":"JobPosting","title":"Dev",\n"description":"line one\nline two",}</script>'
    assert jsonld_postings(html)[0]["title"] == "Dev"


def test_page_fallback_and_nothing():
    info = {"h1": "Backend Engineer", "site": "Globex", "desc": JD, "how": "page"}
    p = extract_posting("<html></html>", "https://globex.test/careers/be", info, {"title": "Backend Eng"})
    assert p["title"] == "Backend Engineer" and p["company"] == "Globex" and p["jd_source"] == "page"
    assert extract_posting("<html></html>", "https://x.test", {"desc": ""}) is None


def test_detect_block():
    assert detect_block(429, "https://x.test/jobs") == "http 429"
    assert detect_block(200, "https://www.linkedin.com/authwall?trk=x") == "login required"
    assert detect_block(200, "https://x.test/j", "Just a moment...", "") == "bot check"
    assert detect_block(200, "https://x.test/j", "Jobs", "Please verify you are human to continue") == "bot check"
    long_jd = "Build our anti-abuse platform, including CAPTCHA and rate limiting. " * 40
    assert detect_block(200, "https://x.test/j", "Trust & Safety Engineer", long_jd) is None


def test_parse_date_and_html_to_text():
    assert parse_date("2026-11-02") == datetime(2026, 11, 2) and parse_date("soon") is None
    assert html_to_text("<p>a&nbsp;b</p><script>x()</script><br/>c") == "a b\n\nc"


def test_board_leads_linkedin_dedupes_card_links():
    anchors = [
        {"href": "https://in.linkedin.com/jobs/view/backend-engineer-at-acme-3987654321?refId=a&trackingId=b", "text": ""},
        {"href": "https://in.linkedin.com/jobs/view/backend-engineer-at-acme-3987654321?position=1", "text": "Backend Engineer\nAcme"},
        {"href": "https://www.linkedin.com/company/acme", "text": "Acme"},
        {"href": "https://www.linkedin.com/jobs/view/4000000001/", "text": "Data Engineer"},
    ]
    leads = board_leads(anchors, BOARDS["linkedin"])
    assert [(x["url"], x["title"]) for x in leads] == [
        ("https://www.linkedin.com/jobs/view/3987654321/", "Backend Engineer"),
        ("https://www.linkedin.com/jobs/view/4000000001/", "Data Engineer")]


def test_board_leads_indeed_and_internshala():
    ind = board_leads([{"href": "https://www.indeed.com/rc/clk?jk=0123456789abcdef&from=serp", "text": "SRE"},
                       {"href": "https://www.indeed.com/cmp/acme", "text": "Acme"}], BOARDS["indeed"])
    assert [x["url"] for x in ind] == ["https://www.indeed.com/viewjob?jk=0123456789abcdef"]
    ish = board_leads([{"href": "https://internshala.com/internship/detail/python-internship-in-kochi-at-acme1731"
                                "?referral=x", "text": "Python Development"}], BOARDS["internshala"])
    assert ish[0]["url"] == "https://internshala.com/internship/detail/python-internship-in-kochi-at-acme1731"


def test_careers_leads_heuristics():
    page = "https://careers.acme.test/careers"
    anchors = [
        {"href": "https://careers.acme.test/careers/backend-engineer-101", "text": "Backend Engineer"},
        {"href": "https://careers.acme.test/careers/backend-engineer-101#apply", "text": "Backend Engineer"},
        {"href": "https://careers.acme.test/careers", "text": "Careers"},
        {"href": "https://careers.acme.test/careers/teams", "text": "Our teams"},
        {"href": "https://acme.test/jobs/view-all", "text": "View all jobs"},
        {"href": "https://acme.test/privacy", "text": "Privacy policy"},
        {"href": "https://elsewhere.test/jobs/123", "text": "Partner job"},
        {"href": "https://job-boards.greenhouse.io/acme/jobs/555", "text": "Data Analyst Intern"},
        {"href": "https://job-boards.greenhouse.io/acme", "text": "All openings at Acme"},
        {"href": "mailto:jobs@acme.test", "text": "Email us"},
    ]
    assert [x["title"] for x in careers_leads(anchors, page)] == ["Backend Engineer", "Data Analyst Intern"]


def test_board_queries_interleave_and_respect_budget():
    spec = SearchSpec(role_titles=["Backend Engineer", "Data Analyst"], locations=["Kochi", "Remote"],
                      job_types=["job", "internship"], max_queries=4)
    qs, dropped = board_queries(spec, BOARDS["indeed"])
    assert dropped == 4 and [q["query"] for q in qs] == ["Backend Engineer", "Data Analyst",
                                                        "Backend Engineer intern", "Data Analyst intern"]
    assert all(q["location"] == "Kochi" for q in qs)
    li, _ = board_queries(spec, BOARDS["linkedin"])              # LinkedIn filters internships natively
    assert li[2] == {"query": "Backend Engineer", "location": "Kochi", "internship": True}
    assert "f_JT=I" in BOARDS["linkedin"].search_url("Backend Engineer", "Kochi", 1, True)


def test_internshala_urls_and_slug_locations():
    b = BOARDS["internshala"]
    assert b.search_url("Python  Development", "Kochi", 0, True) == "https://internshala.com/internships/keywords-python%20development/"
    assert b.search_url("Python Development", "", 1, True) == "https://internshala.com/internships/keywords-python%20development/page-2/"
    assert b.search_url("Data Analyst", "", 0, False) == "https://internshala.com/jobs/keywords-data%20analyst/"
    loc = b.lead_location
    assert loc("https://internshala.com/internship/detail/python-development-internship-in-nagercoil-at-uv-farms1794375277") == "Nagercoil"
    assert loc("https://internshala.com/internship/detail/data-science-in-health-internship-in-new-delhi-at-x1") == "New Delhi"
    assert loc("https://internshala.com/internship/detail/work-from-home-python-development-internship-at-evonexis1794977803") == "Remote"
    assert loc("https://internshala.com/job/detail/data-analyst-job-in-multiple-locations-at-honhr1794895660") == ""


def test_build_tasks_routes_watchlist_by_kind():
    spec = SearchSpec(role_titles=["Engineer"], sources=["watchlist", "linkedin"],
                      watchlist_urls=["https://jobs.lever.co/acme", "https://acme.test/careers"])
    kinds = {t["source"]: t["kind"] for t in build_tasks(spec)}
    assert kinds == {"ats": "api", "careers": "careers", "linkedin": "board"}
    assert build_tasks(SearchSpec(sources=["linkedin"])) == []   # boards need target roles
