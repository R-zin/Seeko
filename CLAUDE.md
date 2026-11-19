# Seeko

Automates job discovery (LangGraph, PostgreSQL) -> LaTeX resume tailoring -> application form filling -> Notion tracking (live,
two-way for the user's application tracking) -> optional backup PostgreSQL server.
All LLM calls use one OpenAI-compatible `/chat/completions` client (never `/responses`).

## Commands
- Dev (both servers): `./dev.sh`  -> UI :5173 (proxies /api), API :8000
- Backend tests: `cd backend && .venv/bin/python -m pytest -q` (PostgreSQL tests use `TEST_DATABASE_URL`, else the embedded server)
- Job search CLI: `cd backend && .venv/bin/python -m jobsearch --help` (run / runs / run-info / resume / jobs / show / db)
- Frontend: `cd frontend && npx tsc -b && npx vite build`
- Toolchain is user-local: `~/.local/bin` (uv, tectonic), `~/.local/node/bin` (node). Python 3.12 venv at `backend/.venv`.

## Architecture (backend/)
- `llm/` `LLMClient` (AsyncOpenAI wrapper; `for_task("scoring"|"tailor"|"applier")` overrides model) + `structured.complete_json`
  (json_schema -> json_object -> prompt+regex, 2 repair retries; tier cached per base_url+model). `api/llm.py` has `POST /api/llm/test`.
- `config/` env settings (runtime-editable via UI), `CandidateProfile`, `base_resume.tex` in `data/`. `database_url` is env-only
  (ignored in `data/settings.json`).
- `db.py` models + lazy engine. Empty `DATABASE_URL` = embedded PostgreSQL (`pgserver`, data in `data/postgres`); `postgresql://` URLs use
  psycopg 3; SQLite still works (tests). Schema = Alembic (`migrations/`), applied by `init_db()`; DBs from the old `create_all()` are
  stamped at `0001` first. On PostgreSQL an old `data/seeko.db` is imported once (only into an empty DB). New columns/tables need a migration
  (`tests/test_jobsearch_store.py::test_migrations_match_models` fails otherwise). `jobs.updated_at` / `search_runs.updated_at` have
  `onupdate` (ORM and Core updates); a `before_flush` event sets `notion_dirty` when a column in `db.NOTION_FIELDS` changes and
  `after_commit` calls `db.jobs_changed()` listeners. Core writers that change shown columns set `notion_dirty` and call
  `jobs_changed()` themselves (see `store.persist_run`). Tracking columns (0004): `application_status` (keys in
  `db.APPLICATION_STATUSES`, or a custom label from Notion), `next_step`, `next_step_date`, `notes`; edit via `db.update_application`.
- `backup.py` mirror to `BACKUP_DATABASE_URL` (env-only; `embedded` = second pgserver): `Mirror.full()` diffs (pk, version) and deletes
  extras, `incremental(since)` copies by version column (`TABLES`), `fix_sequences` keeps the backup promotable. `BackupService` loop
  started in `main.py` lifespan; API `api/backup.py`; CLI `backup` / `restore-backup`. A new table needs an entry in `backup.TABLES`.
- `jobsearch/` the job search as a LangGraph app: `graph.py` (plan → collect → dedupe → fetch → screen → [review] → persist → finish; collect/fetch
  fan out per source with `Send`; all I/O behind `Deps` so tests inject fakes), `browse.py` (Playwright `Session`: pacing, robots.txt, block
  detection, pagination/"Load more", iframe links), `extract.py` (JSON-LD JobPosting → board selector → page text; `detect_block`),
  `leads.py` (results-page links → leads), `classify.py` (job/internship, title match, location, freshness, `screen`), `store.py`
  (idempotent writes: ON CONFLICT inserts, sightings unique per run; closures; `jobs_query` = the one jobs query, full-text on
  PostgreSQL via `db.FTS_DOC`, which must match the GIN index in migration 0003), `service.py` (one run at a time in its own task so
  `cancel()` never hits the caller, streamed progress, Postgres checkpointer, resume),
  `__main__.py` CLI, `app.py` + `langgraph.json` for LangGraph Studio. API: `api/search.py`.
- `scraper/` `ats.py` (Greenhouse/Lever/Ashby/Workday JSON), `boards.py` (declarative LinkedIn/Indeed/Glassdoor/Internshala configs: search URL,
  posting-link regex, canonical URL, description selector; persistent Playwright profile in `browser_data/`),
  `dedupe.py` (normalized company|title + canonical URL, unique indexes), `scorer.py`, `textutil.py` (HTML → structured text).
- `resume/` base-résumé ingestion + compile pipeline:
  - `texparse.py` parses .tex into sections and *editable segments* (summary / skills lines / Experience+Project bullets,
    incl. one-arg `\…Item{}` macros). Spans are offsets, so `apply_edits` leaves everything else byte-identical.
    Also: unsafe-command detection (`\write18`, absolute/`..` paths), missing-asset report, `plain_text()` for prompts.
  - `pdfparse.py` PDF → structured résumé → clean LaTeX (deterministic, text-layer only; rejects scanned PDFs).
    Layout mode pads word gaps to 2–3 spaces, so only 6+ spaces count as a column break.
  - `ingest.py` .tex / .pdf / Overleaf .zip (safe extraction, `\input` flattening). Uploads are drafts in
    `data/resume_staging/`; `save_base()` promotes them. Never overwrite the base on upload.
  - `compiler.py` preflight → engine chain (tectonic → pdflatex → xelatex; falls through only on infra errors) →
    isolated temp dir, `-no-shell-escape`, process-group kill on timeout, content-addressed cache, parsed errors with line numbers.
- `tailor/` `tailor_resume`: model returns per-segment edits → each checked by `check_edit` (no new numbers / terms /
  commands, brace shape, skills set unchanged, ≤ +25 % length) → compile; errors revert only the segment on the error line;
  overflow → one condense pass, then revert the biggest-growth edits. `validate()` is the final whole-doc guard.
  Writes `artifacts/{id}/tailor_report.json` (accepted / rejected / reverted with reasons).
- `applier/` `extract.py` tags controls with `data-seeko-id`; `mapper.py` LLM maps fields (null = unknown); `engine.py` fills,
  never submits except `submit()`; `gate.py` decides review/submit/paused; open pages for review live in `gate.pending`.
- `mcp_notion/` stdio client to `npx -y @notionhq/notion-mcp-server@2` (API 2025-09-03: database -> data source; the server sends
  HTTP errors as normal results, `client.call` turns them into `NotionError`). Tool names discovered at runtime (`find_tool`).
  `schema.py` = `PROPS` (the one list of Notion properties: type, job field, two-way), `check()` maps a database (unknown /
  wrong-type properties are never written), `setup_patch()` adds missing ones only, `description_blocks()` = JD page body.
  `sync.py` `NotionDB` adapter (data source or legacy database tools).
- `workers/pipeline.py` single background loop; `discover()` runs the job search graph; job status: scraped -> scored -> tailoring -> tailored -> applying -> needs_review -> applied/failed/skipped.
  Picks only `WORKABLE` jobs (open, and `application_status == "not_applied"`: the user's own status stops the bot).
- `workers/notion_service.py` `NotionSync` (own task, woken by `jobs_changed`, polls too): push = dirty jobs, hash-skip, Seeko ID lookup
  before create, `_mark` clears dirty only if `updated_at` is unchanged; pull = query pages edited since last pull, 3-way `merge()`
  per two-way field against `notion_base` (Seeko wins conflicts). Transient errors back off globally, others per job
  (`notion_attempts`/`notion_retry_at`). Never block the pipeline on Notion.

## Conventions / invariants
- Approval gate is the default; full-auto is opt-in, capped daily, and must pause on CAPTCHA/2FA/unknown required fields.
- Never fabricate resume content: tailoring may only rephrase existing facts. Keep `tailor/tailor.py:check_edit` and `validate` strict;
  every new guard needs a test that fails without it (see `tests/test_tailor.py`, `tests/test_resume.py`).
- `mcp` is pinned `<2` (v2 renamed APIs). Tests with Playwright/tectonic need Chromium + tectonic installed; first tectonic run downloads its bundle.
- Board scraper selectors drift; keep them isolated in `scraper/boards.py`. Tests use fixtures/mocks, never live sites.
- Job search must stay careful: never re-read a known posting (check the DB before opening pages), keep per-source budgets and jittered delays,
  stop a source at the first CAPTCHA / login wall / 429 (never bypass), obey robots.txt on careers pages. Across runs `apply_limits`
  enforces the per-site daily page budget and the post-block cooldown; every browser `Session` gets the remaining budget. Graph state stays JSON-native
  (dicts, ISO date strings) so checkpoints serialize. Store writes must stay idempotent (nodes retry, runs resume).
- Tests never touch the user's DB: `tests/conftest.py` forces a temp SQLite `DATABASE_URL`; PostgreSQL tests use `TEST_DATABASE_URL` or the
  embedded server, each in its own schema (`migrated` fixture runs a test on both backends).
- Runtime data (`data/`, `artifacts/`, `browser_data/`, `.env`) is gitignored.
- Notion bookkeeping columns (`notion_*`, `updated_at`) must never be in `NOTION_FIELDS` or the page payload (sync would loop).
- `tests/fake_notion_server.py` mimics the real v2 tool names and error format; keep it in step when using a new tool.
