# Seeko

Automates job discovery (LangGraph, PostgreSQL) -> LaTeX resume tailoring -> application form filling -> Notion tracking (live,
two-way for the user's application tracking) -> optional backup PostgreSQL server.
All LLM calls use one OpenAI-compatible `/chat/completions` client (never `/responses`).

## Commands
- Dev (both servers): `./dev.sh`  -> UI :5173 (proxies /api), API :8000
- Backend tests: `cd backend && .venv/bin/python -m pytest -q` (PostgreSQL tests use `TEST_DATABASE_URL`, else the embedded server)
- Job search CLI: `cd backend && .venv/bin/python -m jobsearch --help` (run / runs / run-info / resume / jobs / show / db /
  add / doctor / backup / restore-backup)
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
- Migration 0005: `jobs.origin` (search | user), `jobs.waiting_on` (answer_bank keys), `jobs.attempts`/`retry_at`, tables
  `answer_bank` (`db.SavedAnswer`) and `app_state` (`db.get_state/set_state`). JSON columns store Python None as JSON null, so
  test "empty" JSON values in Python, not with `IS NULL`.
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
- `applier/` `extract.py` tags controls with `data-seeko-id` (`current` = what the control holds, as a person reads it);
  `answers.py` answer bank (`question_key`, `match_option`, `lookup` before the model, `record_unanswered`, `learn` from a
  paused form, `ready_to_refill`); `mapper.py` LLM maps the remaining fields (null = unknown; saved answers passed as facts);
  `engine.py` fills, never submits except `submit()`, returns `questions` for unanswered required fields; `gate.py` decides
  review/submit/paused; open pages for review live in `gate.pending`. The engine stays DB-free: the pipeline passes `bank`.
- `mcp_notion/` stdio client to `npx -y @notionhq/notion-mcp-server@2` (API 2025-09-03: database -> data source; the server sends
  HTTP errors as normal results, `client.call` turns them into `NotionError`). Tool names discovered at runtime (`find_tool`).
  `schema.py` = `PROPS` (the one list of Notion properties: type, job field, two-way), `check()` maps a database (unknown /
  wrong-type properties are never written), `setup_patch()` adds missing ones only, `description_blocks()` = JD page body.
  `sync.py` `NotionDB` adapter (data source or legacy database tools).
- `workers/pipeline.py` single background loop; `discover()` runs the job search graph; job status: scraped -> scored -> tailoring -> tailored -> applying -> needs_review -> applied/failed/skipped.
  Picks only `WORKABLE` jobs (open, and `application_status == "not_applied"`: the user's own status stops the bot), in
  `next_job_id` order (deadline ≤ 7 d, origin user, stage, score, relevance; skips `retry_at` in the future). `_failed`:
  `is_transient` errors retry from `RESUME_STAGE` with backoff up to `AUTO_RETRY`, else failed. `refill` / `refill_ready`
  re-queue paused jobs once their questions are answered; `approve` learns typed answers first; `approve_ready` = bulk.
- `jobsearch/intake.py` add one job: `fetch_posting` (Session.read_detail + extract_posting, robots on careers pages, stop on
  blocks, refuses while a search holds the browser profiles), `from_capture` (bookmarklet payload: page text + JSON-LD),
  `from_paste`; `Store.add_job` dedupes; mode applied -> status `tracked`. `BOOKMARKLET_JS` is served by
  `GET /api/jobs/bookmarklet` (tested by running it in Chromium).
- `notify.py` webhook (Slack / Discord / ntfy JSON publish / generic JSON), per-event toggles, 10-min repeat guard, `emit` is
  fire-and-forget. Emitted from pipeline (needs_review, applied in full-auto, failed) and `jobsearch.service.announce`.
- `agenda.py` next steps / follow-ups / apply-by / waiting counts, `digest_text`, `auto_ghost`, `tick` (once a day via
  `app_state`), loop started in `main.py`. `doctor.py` readiness checks (`/api/doctor`, CLI `doctor`, Setup checklist).
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
- `main.py` refuses cross-site browser writes to /api/* (Origin must be local or APP_URL). Keep any new state-changing
  endpoint under /api/ so it stays covered; never add CORS allow-all.
- The answer bank only ever holds the user's own answers (typed in the UI or into a paused form); never let the model write to
  it. Notifications and the digest must never block or fail a pipeline step.
