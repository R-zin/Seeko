"""The `python -m jobsearch` command line, against the test database."""
import csv
import json
import pytest
import db
from jobsearch import __main__ as cli
from jobsearch import service
from jobsearch.store import Store

JD = "Builds data pipelines with Airflow and SQL. " * 10


@pytest.fixture(scope="module")
def seeded():
    db.init_db()
    st = Store()
    st.start_run("cli-r1", {"role_titles": ["Data"]}, "cli")
    res = st.persist_run("cli-r1", [
        {"source": "lever", "url": "https://jobs.lever.co/clico/1", "title": "Data Engineer (CLI)", "company": "clico",
         "location": "Kochi", "jd_text": JD, "job_type": "job", "jd_source": "api"},
        {"source": "lever", "url": "https://jobs.lever.co/clico/2", "title": "Data Intern (CLI)", "company": "clico",
         "location": "Remote", "jd_text": JD + " Kafka.", "job_type": "internship", "jd_source": "api"}], [], [])
    st.finish_run("cli-r1", "done", {"leads": 2, "known": 0, "new": 2, "blocked": {"linkedin": "bot check"}})
    return res["new_job_ids"]


def test_listing_showing_and_runs(seeded, capsys):
    assert cli.main(["jobs", "--type", "internship", "--q", "kafka"]) == 0
    out = capsys.readouterr().out
    assert "Data Intern (CLI)" in out and "Data Engineer (CLI)" not in out
    assert cli.main(["jobs", "--json", "--q", "airflow", "--limit", "500"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {"Data Engineer (CLI)", "Data Intern (CLI)"} <= {r["title"] for r in rows} and all("jd_text" in r for r in rows)
    jid = next(r["id"] for r in rows if r["title"] == "Data Intern (CLI)")
    assert cli.main(["show", jid]) == 0
    assert "Kafka" in capsys.readouterr().out
    assert cli.main(["show", "nope"]) == 1
    assert cli.main(["runs"]) == 0 and "linkedin" in capsys.readouterr().out
    assert cli.main(["run-info", "cli-r1"]) == 0 and "Data Intern (CLI)" in capsys.readouterr().out
    assert cli.main(["db"]) == 0 and "search_runs" in capsys.readouterr().out


def test_export(seeded, tmp_path, capsys):
    out = tmp_path / "jobs.csv"
    assert cli.main(["export", "--q", "airflow", "-o", str(out)]) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert {"Data Engineer (CLI)", "Data Intern (CLI)"} <= {r["title"] for r in rows}
    assert cli.main(["export", "--format", "json", "--type", "internship", "--q", "kafka"]) == 0
    assert [r["title"] for r in json.loads(capsys.readouterr().out)] == ["Data Intern (CLI)"]


def test_run_paths(seeded, monkeypatch, capsys):
    calls = {}

    async def fake_run(spec, trigger):
        calls["spec"], calls["trigger"] = spec, trigger
        return {"run_id": "x1", "new_job_ids": [], "stats": {"leads": 5, "known": 5, "new": 0, "closed": 1,
                                                             "skipped_sources": {"indeed": "daily page budget used"},
                                                             "blocked": {"linkedin": "bot check"}}}
    monkeypatch.setattr(service, "run_search", fake_run)
    assert cli.main(["run", "--title", "Data Engineer", "--type", "internship", "--source", "internshala",
                     "--location", "Kochi", "--max-details", "5"]) == 0
    out = capsys.readouterr().out
    assert calls["trigger"] == "cli" and calls["spec"].sources == ["internshala"] and calls["spec"].max_details == 5
    assert "BLOCKED linkedin" in out and "SKIPPED indeed" in out and "closed" in out

    async def busy(spec, trigger): raise service.RunInProgress("a job search is already running")
    monkeypatch.setattr(service, "run_search", busy)
    assert cli.main(["run", "--title", "X"]) == 1
    with pytest.raises(SystemExit):
        cli.main(["run", "--source", "myspace"])                     # unknown source rejected by argparse
    assert cli.main(["resume", "no-such-run"]) == 1                  # no checkpoint
    assert cli.main(["import-sqlite", "/nonexistent/seeko.db"]) == 0


def test_add_and_doctor_commands(seeded, capsys, tmp_path):
    desc = tmp_path / "jd.txt"; desc.write_text("Own the payments ledger in Go and PostgreSQL. " * 6)
    assert cli.main(["add", "--title", "Payments Engineer (CLI)", "--company", "clico", "--text", f"@{desc}", "--applied"]) == 0
    out = capsys.readouterr().out
    assert "added:" in out and "Payments Engineer (CLI)" in out and "tracked as applied" in out
    assert cli.main(["add", "--title", "Payments Engineer (CLI)", "--company", "clico", "--text", "x", "--applied"]) == 0
    assert "already known" in capsys.readouterr().out
    assert cli.main(["add", "--title", "No Link (CLI)", "--company", "clico", "--text", "x"]) == 1      # applying needs a link
    assert "link" in capsys.readouterr().err
    code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert code in (0, 1) and "Language model" in out and "Chromium for Playwright" in out
