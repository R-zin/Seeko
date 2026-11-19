"""Command line for the job search. Run from backend/:

    .venv/bin/python -m jobsearch run                      # search with the saved profile + settings
    .venv/bin/python -m jobsearch run --title "Data Analyst" --type internship --source internshala --show-browser
    .venv/bin/python -m jobsearch runs                     # recent runs and what they found
    .venv/bin/python -m jobsearch run-info <run_id>        # jobs a run found (new vs already known)
    .venv/bin/python -m jobsearch resume <run_id>          # continue an interrupted run from its checkpoint
    .venv/bin/python -m jobsearch jobs --type internship --q "django -senior"   # stored jobs, full-text search
    .venv/bin/python -m jobsearch export --format csv -o jobs.csv              # everything, with descriptions
    .venv/bin/python -m jobsearch show <job_id>            # one job with its full description
    .venv/bin/python -m jobsearch db                       # which database is in use, row counts, backup
    .venv/bin/python -m jobsearch backup                   # full copy to BACKUP_DATABASE_URL now
    .venv/bin/python -m jobsearch restore-backup [--force] # copy the backup server back into the primary
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from jobsearch.spec import ALL_SOURCES


def _table(rows: list[list], headers: list[str]) -> str:
    rows = [[("" if c is None else str(c)) for c in r] for r in rows]
    w = [min(max([len(h)] + [len(r[i]) for r in rows]), 60) for i, h in enumerate(headers)]
    line = lambda r: "  ".join(c[:w[i]].ljust(w[i]) for i, c in enumerate(r))
    return "\n".join([line(headers), line(["-" * x for x in w])] + [line(r) for r in rows])


def _print_result(res: dict) -> None:
    st = res["stats"]
    print(f"run {res['run_id']}: {st.get('leads', 0)} found, {st.get('known', 0)} already known, {st.get('new', 0)} new "
          f"({st.get('new_jobs', 0)} jobs, {st.get('new_internships', 0)} internships)")
    if st.get("rejected"): print("rejected: " + ", ".join(f"{k}={v}" for k, v in sorted(st["rejected"].items())))
    if st.get("deferred"): print(f"left for the next run (budget): {st['deferred']}")
    if st.get("closed"): print(f"postings closed (gone from their board or expired): {st['closed']}")
    for src, why in (st.get("skipped_sources") or {}).items(): print(f"SKIPPED {src}: {why}")
    for src, why in (st.get("blocked") or {}).items(): print(f"BLOCKED {src}: {why} - stopped that source; nothing was bypassed")
    for src, s in (st.get("sources") or {}).items():
        if s.get("blocked_targets"): print(f"blocked careers pages: {'; '.join(s['blocked_targets'])}")
        if s.get("error"): print(f"ERROR {src}: {s['error']}")


async def _run(a) -> int:
    from jobsearch.service import RunCancelled, RunInProgress, run_search
    from jobsearch.spec import SearchSpec
    o = {"role_titles": a.title, "locations": a.location, "job_types": a.type, "sources": a.source,
         "max_details": a.max_details, "watchlist_urls": a.url}
    if a.show_browser: o["headless"] = False
    spec = SearchSpec.from_profile(**{k: v for k, v in o.items() if v})
    if not spec.role_titles and not spec.watchlist_urls:
        print("Nothing to search: set target roles / career pages in Setup, or pass --title / --url.", file=sys.stderr)
        return 2
    print(f"searching {', '.join(spec.sources)} for {', '.join(spec.role_titles) or 'all roles'} "
          f"({', '.join(spec.job_types)}) in {', '.join(spec.locations) or 'any location'} ...", flush=True)
    try:
        res = await run_search(spec, trigger="cli")
    except RunInProgress as e:
        print(e, file=sys.stderr); return 1
    except RunCancelled as e:
        print(e, file=sys.stderr); return 1
    _print_result(res)
    return 0


async def _resume(a) -> int:
    from jobsearch.service import resume_run
    try:
        res = await resume_run(a.run_id)
    except LookupError as e:
        print(e, file=sys.stderr); return 1
    _print_result(res)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m jobsearch", description="Seeko job search (LangGraph + PostgreSQL)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="search now (profile + settings, overridable)")
    r.add_argument("--title", action="append", help="target role (repeatable)")
    r.add_argument("--location", action="append", help="location (repeatable); 'Remote' for remote")
    r.add_argument("--type", action="append", choices=["job", "internship"], help="job and/or internship")
    r.add_argument("--source", action="append", choices=list(ALL_SOURCES), help="where to search (repeatable)")
    r.add_argument("--url", action="append", help="career page / ATS board to watch (repeatable)")
    r.add_argument("--max-details", type=int, help="posting pages to open per source")
    r.add_argument("--show-browser", action="store_true", help="run the browser visibly")
    s = sub.add_parser("resume", help="continue an interrupted run"); s.add_argument("run_id")
    s = sub.add_parser("runs", help="recent runs"); s.add_argument("--limit", type=int, default=15)
    s = sub.add_parser("run-info", help="jobs found by one run"); s.add_argument("run_id")
    j = sub.add_parser("jobs", help="stored jobs")
    j.add_argument("--type", choices=["job", "internship"]); j.add_argument("--status")
    j.add_argument("--days", type=int, help="first found within N days"); j.add_argument("--limit", type=int, default=50)
    j.add_argument("--json", action="store_true")
    j.add_argument("--q", help="full-text search in title, company and description")
    j.add_argument("--state", choices=["open", "closed"])
    e = sub.add_parser("export", help="jobs with full descriptions as CSV or JSON")
    e.add_argument("--format", choices=["csv", "json"], default="csv"); e.add_argument("-o", "--output", help="file (default stdout)")
    e.add_argument("--type", choices=["job", "internship"]); e.add_argument("--state", choices=["open", "closed"])
    e.add_argument("--q")
    s = sub.add_parser("show", help="one job with its full description"); s.add_argument("job_id")
    s = sub.add_parser("import-sqlite", help="copy jobs from an old SQLite database (only into an empty one)")
    s.add_argument("path", nargs="?")
    sub.add_parser("db", help="database in use and row counts")
    sub.add_parser("backup", help="copy every table to BACKUP_DATABASE_URL now (full check)")
    s = sub.add_parser("restore-backup", help="copy the backup server into the primary (only an empty one, unless --force)")
    s.add_argument("--force", action="store_true", help="overwrite a primary that has data: it becomes an exact copy of the backup")
    a = ap.parse_args(argv)

    import db
    db.init_db()
    from sqlalchemy import func, select
    from db import Job, JobSighting, SearchRun, SeenUrl, SessionLocal, utcnow
    from jobsearch.store import Store

    if a.cmd in ("run", "resume"):
        try:
            return asyncio.run(_run(a) if a.cmd == "run" else _resume(a))
        except KeyboardInterrupt:
            print("\nstopped; the run is marked interrupted - continue it with `python -m jobsearch resume <run_id>`",
                  file=sys.stderr)
            return 130
    if a.cmd == "runs":
        rows = [[r["id"], r["status"], r["trigger"], (r["started_at"] or "")[:16], r["stats"].get("leads", ""),
                 r["stats"].get("known", ""), r["stats"].get("new", ""), ",".join(r["stats"].get("blocked", {}) or [])]
                for r in Store().list_runs(a.limit)]
        print(_table(rows, ["run", "status", "trigger", "started (UTC)", "found", "known", "new", "blocked"]))
        return 0
    if a.cmd == "run-info":
        r = Store().get_run(a.run_id)
        if r is None: print("no such run", file=sys.stderr); return 1
        print(json.dumps({k: r[k] for k in ("id", "status", "started_at", "finished_at", "error")}, indent=2))
        print(_table([["new" if x["is_new"] else "seen", x["job_type"], x["title"], x["company"], x["location"], x["id"]]
                      for x in r["jobs"]], ["", "type", "title", "company", "location", "id"]))
        return 0
    if a.cmd == "jobs":
        from jobsearch.store import jobs_query
        with SessionLocal() as ses:
            since = utcnow() - timedelta(days=a.days) if a.days else None
            jobs = list(ses.scalars(jobs_query(ses, job_type=a.type, status=a.status, state=a.state, q=a.q, since=since,
                                               limit=a.limit)))
        if a.json:
            from api.jobs import ser
            print(json.dumps([ser(x) | {"jd_text": x.jd_text} for x in jobs], indent=2)); return 0
        print(_table([[x.id, x.job_type, x.title, x.company, x.location, x.source, x.status, x.times_seen,
                       x.discovered_at.date(), "closed" if x.closed_at else ""] for x in jobs],
                     ["id", "type", "title", "company", "location", "source", "status", "seen", "found", ""]))
        return 0
    if a.cmd == "export":
        import csv
        from api.jobs import EXPORT_COLUMNS, export_rows
        from jobsearch.store import jobs_query
        with SessionLocal() as ses:
            rows = export_rows(list(ses.scalars(jobs_query(ses, job_type=a.type, state=a.state, q=a.q, limit=None))))
        out = open(a.output, "w", newline="", encoding="utf-8") if a.output else sys.stdout
        try:
            if a.format == "json":
                json.dump(rows, out, ensure_ascii=False, indent=1); out.write("\n")
            else:
                w = csv.DictWriter(out, fieldnames=EXPORT_COLUMNS); w.writeheader(); w.writerows(rows)
        finally:
            if a.output: out.close()
        if a.output: print(f"wrote {len(rows)} job(s) to {a.output}", file=sys.stderr)
        return 0
    if a.cmd == "show":
        with SessionLocal() as ses:
            x = ses.get(Job, a.job_id)
        if x is None: print("no such job", file=sys.stderr); return 1
        posted = f" posted={x.posted_at:%Y-%m-%d}" if x.posted_at else ""
        print(f"{x.title} - {x.company} ({x.job_type}{', remote' if x.is_remote else ''})\n{x.location}\n{x.url}\n"
              f"source={x.source} status={x.status} found={x.discovered_at:%Y-%m-%d} seen={x.times_seen}x{posted}")
        print("\n" + (x.jd_text or "(no description)"))
        return 0
    if a.cmd == "import-sqlite":
        path = Path(a.path) if a.path else db.LEGACY_SQLITE
        n = db.import_legacy_sqlite(path)
        print(f"imported {n} job(s) from {path}" if n else "nothing imported (missing file, no jobs, or this database already has jobs)")
        return 0
    if a.cmd == "db":
        url = db.get_engine().url
        print(f"database: {url.render_as_string(hide_password=True)}")
        with SessionLocal() as ses:
            for m in (Job, SearchRun, JobSighting, SeenUrl):
                print(f"{m.__tablename__:14} {ses.scalar(select(func.count()).select_from(m))}")
            for t, n in ses.execute(select(Job.job_type, func.count()).group_by(Job.job_type)):
                print(f"  {t:12} {n}")
        from backup import service as backup
        print(f"backup:   {backup.status()['target'] if backup.configured() else 'off (set BACKUP_DATABASE_URL)'}")
        return 0
    if a.cmd in ("backup", "restore-backup"):
        from backup import counts, redact, service as backup
        if not backup.configured():
            print("BACKUP_DATABASE_URL is not set (a postgresql:// URL, or 'embedded')", file=sys.stderr); return 2
        try:
            stats = backup.sync(full=True) if a.cmd == "backup" else backup.restore(force=a.force)
        except Exception as e:
            print(f"{a.cmd} failed: {e}", file=sys.stderr); return 1
        print(_table([[t, v["copied"], v.get("deleted", 0)] for t, v in stats.items()], ["table", "copied", "deleted"]))
        where = backup.target() if a.cmd == "backup" else db.get_engine()
        print(f"{'backup' if a.cmd == 'backup' else 'primary'} now holds: "
              + ", ".join(f"{t} {n}" for t, n in counts(where).items()) + f"  ({redact(str(where.url))})")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
