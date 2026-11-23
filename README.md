# Seeko

Find jobs and internships, tailor your LaTeX resume per role (without inventing anything), fill the application, and track everything in PostgreSQL and Notion.

## Setup
Requires Python 3.11+, Node 20+, a LaTeX compiler (`tectonic` recommended, or `pdflatex`).
PostgreSQL needs no install: with `DATABASE_URL` blank the app starts an embedded server (data in `data/postgres`).
To use your own server instead, set `DATABASE_URL=postgresql://user:pass@host:5432/seeko` (or `docker compose up -d db`).
```bash
cd backend && uv venv --python 3.12 .venv && uv pip install -e ".[dev]" && .venv/bin/playwright install chromium
cd ../frontend && npm install
cp .env.example .env     # optional; everything is also editable in the UI
./dev.sh                 # UI http://127.0.0.1:5173, API http://127.0.0.1:8000/docs
```
The first LaTeX compile with tectonic downloads its TeX bundle (a few minutes once).

## First run
1. **Setup tab → LLM endpoint**: base URL, key, model → *Test endpoint* (checks connectivity, model routing, JSON output; diagnoses 401 / wrong `/v1` path / unknown model).
   Works with OpenAI, OpenRouter, Ollama, vLLM, LM Studio, Groq, Together, LiteLLM. Optional cheaper/stronger per-task models.
2. Upload your résumé as `.tex`, `.pdf` or an Overleaf `.zip` (Setup → Résumé). PDFs are converted to clean LaTeX using only their
   own text (scanned PDFs without a text layer are rejected). Uploads are drafts: check the compile result and the
   "what can be tailored" panel, then **Save as base**. Fill in the profile and add company career URLs (Greenhouse, Lever, Ashby, Workday).
3. Optional: *Log in to LinkedIn/Indeed/Glassdoor* opens a visible browser; sign in once, close it. Sessions persist in `browser_data/`.
4. Optional: Notion (see [Notion tracker](#notion-tracker)) — create an internal integration, create a database and connect the
   integration to it, paste token + database ID under Setup → Notion, then **Set up database**.
5. **Pipeline tab → Run discovery** (or Tracker → Searches → *Search now*). New jobs and internships are saved with their full
   description, then scored, tailored, compiled, and the form is filled. In the default **approval gate** mode the browser stays on the filled form
   and you click **Approve & Submit** (or Skip) in the dashboard.

## Job search (LangGraph + PostgreSQL)
Discovery is a LangGraph `StateGraph` (`backend/jobsearch/graph.py`):

```
plan → collect (per source, in parallel) → dedupe → fetch (per source) → screen → [review] → persist → finish
```

- **Sources.** Career pages: Greenhouse / Lever / Ashby / Workday boards through their public APIs; *any other careers page is browsed*
  with Playwright (follows its pagination and "Load more", reads iframes). Boards (opt-in): LinkedIn, Indeed, Glassdoor, Internshala,
  browsed in your persistent, signed-in profile.
- **Already found = never re-read.** Every lead is checked against PostgreSQL by link and by company + title before its page is opened.
  Known jobs get a *sighting* (`times_seen`, `last_seen_at`); pages read and rejected (expired, gone, not a posting…) are not re-read for
  `SEARCH_REVISIT_DAYS`.
- **Careful screening.** Titles must match a target role (synonyms like SDE/SWE/developer are understood, "Sales Engineer" is not a
  "Software Engineer"), job vs internship comes from the posting (schema.org `employmentType`, the title, the URL), plus location / remote,
  posting age and expiry, excluded companies, and a real description. Pages with no structured data are double-checked by your model.
- **Polite browsing.** Jittered 3–8 s delays, page and detail budgets per source (the rest is picked up next run), robots.txt on careers
  pages, and the first CAPTCHA / login wall / HTTP 429 stops that source for the run. Nothing tries to get past a bot check.
  Across runs (e.g. scheduled discovery): each browsed site gets at most `SEARCH_DAILY_PAGES` page loads per 24 h, and a site that
  blocked a run is left alone for `SEARCH_BLOCK_COOLDOWN_H` hours.
- **Closed postings.** When a Greenhouse / Lever / Ashby board no longer lists a saved job, or its stated expiry date passes, the job is
  marked closed (`closed_at`) and the worker no longer tailors or applies to it; it reopens if it is seen again.
- **Find and export.** The Tracker search (and `GET /api/jobs?q=`) is full-text over title, company and the saved description, using a
  PostgreSQL GIN index with web-search syntax (`"exact phrase"`, `-exclude`, `OR`). *Export CSV / JSON* downloads the filtered jobs with
  their full descriptions (`/api/jobs/export`).
- **Control.** Tracker → Searches shows the step a run is on, *Stop search* ends it cleanly (it can be resumed), and every finished run
  records what it found, skipped, closed and why.
- **What is stored.** `jobs` (full description as text, `job_type` job|internship, posted/expiry dates, remote, source of the text,
  times seen), `search_runs` (spec + statistics per run), `job_sightings`, `seen_urls`, and LangGraph checkpoints — an interrupted run
  resumes where it stopped (Tracker → Searches → *Resume*, or the CLI). Schema changes are Alembic migrations, applied at startup;
  an existing `data/seeko.db` (SQLite) is imported once into an empty PostgreSQL.

CLI, from `backend/`:
```bash
.venv/bin/python -m jobsearch run                              # profile + settings
.venv/bin/python -m jobsearch run --title "Data Analyst" --type internship --source internshala --location Kochi
.venv/bin/python -m jobsearch runs | jobs --type internship --q "django -senior" | show <job_id> | resume <run_id> | db
.venv/bin/python -m jobsearch export --format csv -o jobs.csv   # all jobs with descriptions (Ctrl-C a run: resume it later)
```
LangGraph Studio: `pip install "langgraph-cli[inmem]"` and `langgraph dev` in `backend/` (graph `job_search`, see `langgraph.json`).

## Everyday automation
- **Checklist.** Setup → *00 Checklist* (or `python -m jobsearch doctor`) lists what still blocks the automation — model,
  base résumé, LaTeX compiler, Chromium, contact details, target roles, sources — each with its fix. The Pipeline page shows a
  banner while anything blocking is left.
- **Add a job you found.** Pipeline → *Add a job by link*: paste a posting link (read once, the same careful way as the search:
  robots.txt on careers pages, a CAPTCHA or login wall stops it), or paste title / company / description. Or drag the
  **＋ Seeko bookmarklet** to your bookmarks bar and click it on any job page — even LinkedIn while you are signed in: it sends
  the text of the tab you are looking at, so Seeko never opens that site itself. Choose *Tailor and fill it* (never skipped for
  a low match score: you picked it) or *I applied already* (tracked only). CLI: `python -m jobsearch add <url> [--applied]`.
- **Answer bank.** A required question your profile and résumé do not answer (notice period, relocation, "how did you hear
  about us"…) used to pause every form that asked it. Now it is asked once: answer it in the job's review, or type it into the
  paused form before you approve (Seeko learns it), and every later form reuses it — matched to that form's options ("Yes" fills
  "Yes, I am authorized…"). Jobs that were only waiting on those questions are filled again automatically. Setup → *Answers*
  lists everything forms have asked, with your answers.
- **Smarter queue.** The worker takes jobs closing within a week first, then jobs you added, then those furthest along, best
  score first. Timeouts, rate limits and 5xx errors are retried with a growing pause (`AUTO_RETRY`, default 3) from the step the
  job had reached, instead of marking it failed. *Approve all ready* submits every completely filled form in one go.
- **Notifications.** Setup → *Notifications*: a Slack or Discord incoming webhook, an ntfy topic (`https://ntfy.sh/<topic>`,
  push to your phone) or any URL taking JSON. You hear when a form is waiting for you, when full-auto submitted, when a job
  failed, when a search found jobs, and a daily digest.
- **Agenda & follow-ups.** Tracker → *01 Agenda*: next steps due (overdue first), applications with no news for
  `FOLLOWUP_AFTER_DAYS` (one click: no response / rejected / interviewing), postings closing within 5 days that you have not
  applied to, and what is waiting for you. The same list is the daily digest at `DIGEST_HOUR` (local). Optionally
  `AUTO_GHOST_DAYS` marks applications with no answer that long as "No response" (synced to Notion).

## Interface
A minimal black & white UI (prototyped in Google Stitch): white / near-black only, hairline borders, light and dark themes
(follows the system; `T` or the top-right button switches), short eased animations that switch off under
"reduce motion".
- **⌘K / Ctrl+K** (or `/`) opens the command palette: full-text job search plus every action — run discovery, pause/resume,
  approve all ready, add a job by link, send the digest, pull Notion edits, back up now, switch theme.
- **Keyboard:** `J`/`K` next/previous job, `A` approve, `S` skip, `O` open the posting, `1`–`5` queue filters, `G P` / `G S` /
  `G T` switch tabs, `?` lists everything.
- **Bulk edits** in Tracker → Register: tick jobs, then set their application status, skip them or retry failed ones at once.
- **Tracker** (Stitch-prototyped): a summary strip (applications, interviewing, offers, response rate, awaiting reply), the
  agenda, an **application board** — drag a card between Applied / Assessment / Interviewing / Offer / Closed (or use its
  "Move to…" menu) to change its application status, synced to Notion like any edit — then the register, compact
  controls, search history with live progress, and the log.

## Notion tracker
Seeko keeps a Notion database in step with its own, through Notion's MCP server (`npx -y @notionhq/notion-mcp-server@2`,
Notion API 2025-09-03).

- **What each page holds.** Role, Company, Status (Scraped → Scored → Tailoring → Tailored → Applying → Needs Review → Applied /
  Failed / Skipped), **Application Status** (Not applied, Applied, Assessment, Interviewing, Offer, Accepted, Rejected, Withdrawn,
  No response), Job Type, Location, Remote, Source, Job URL, Match Score, Employment Type, the dates — Discovered, Posted,
  Apply By, Applied Date, Closed, Next Step Date — Next Step, Notes, Tailored Summary and Seeko ID. The page body is the full job
  description.
- **Set up database** adds whichever of these properties your database lacks (it works on a brand-new empty database too) and
  never changes or removes existing ones. Until then, missing properties are simply not written; a `status`-type Status works if
  it has options with these names.
- **Live.** Any change to a job — a new posting, a status step, a closed posting, your tracking edits — marks it for Notion and wakes
  the syncer, so the page updates within seconds (Notion allows ~3 requests/s; a page that would not change is not sent).
  Failures never block the pipeline: outages back off, a bad job is retried on its own, and Setup → Notion shows pages, waiting,
  errors and the last error. A page deleted in Notion is not recreated unless you press *Resync all*.
- **Two-way for your tracking.** Application Status, Applied Date, Next Step, Next Step Date and Notes can be edited in Seeko
  (Tracker → Application column, or the job's *Your tracking* panel) or in Notion; Notion edits come back every
  `NOTION_PULL_INTERVAL_S` (60 s). If both sides changed the same field before a sync, Seeko's value wins (logged). A custom
  Application Status option you create in Notion is kept as-is.
- **Your status steers the bot.** A job you mark Applied / Interviewing / Rejected / … (anything but *Not applied*) is never tailored
  or applied to by Seeko. Marking a job applied without a date dates it today.

## Backup database
Set `BACKUP_DATABASE_URL` to a second PostgreSQL server and Seeko keeps a live copy of every table on it (jobs with descriptions
and tracking, search runs, sightings, rejected pages, logs):
```bash
docker compose up -d db-backup     # BACKUP_DATABASE_URL=postgresql://seeko:seeko@localhost:5433/seeko
```
Any hosted PostgreSQL works (a server on another machine also survives losing this one), or `BACKUP_DATABASE_URL=embedded` for a
second local server in `data/postgres-backup`. The backup gets the same schema (Alembic), then a full check at start and every 6 h
(copies what differs, deletes what the primary no longer has) and an incremental copy every `BACKUP_INTERVAL_S` (30 s).
Setup → Backup shows when it last copied and how many rows it holds.

- **Switch over:** point `DATABASE_URL` at the backup and restart — its id sequences are kept past the copied rows, so it accepts
  new writes straight away.
- **Copy back:** `python -m jobsearch restore-backup` copies the backup into an empty primary (`--force` overwrites a primary that
  has data, making it an exact copy). `python -m jobsearch backup` runs a full copy now; `db` shows the backup in use.
- LangGraph checkpoints are not copied; losing them only means an interrupted search cannot be resumed.

## Safety
- Approval gate is the default. Full-auto is opt-in, has a daily cap, and pauses on CAPTCHA / 2FA / unanswerable required fields.
- Tailoring only rephrases your summary, skills order and experience/project bullets — never headings, companies, dates or education.
  Each edit is checked on its own (no new numbers, tools or names; skills may only be re-ordered); bad edits are dropped, edits that
  break compilation or overflow the page are reverted individually, and the review panel lists every accepted/rejected/reverted edit.
- LaTeX is compiled with shell-escape disabled, in an isolated temp dir, with a hard timeout; `\write18` and absolute/`..` file paths are refused.
- Form answers come only from your resume/profile; unknown required fields are left for you.
- LinkedIn/Indeed/Glassdoor/Internshala restrict automation in their terms and may restrict your account. They are off by default; use low volumes at your own risk.
- Job search never solves or bypasses a CAPTCHA or login wall: the source stops for that run and the reason is shown under Tracker → Searches.
- If the server restarts while a job awaits approval, the live browser page is lost (approve returns 409); re-run that job.
- The API refuses writes that a browser sends from another website (Origin check), so a page you visit cannot submit
  applications or change settings through your local Seeko. Scripts and the CLI (no Origin header) are unaffected.
- The bookmarklet only reads the tab you click it on and hands it to Seeko's own page; adding the job still needs your click,
  and a job you add is filled like any other — never submitted without approval in gate mode.

## Tests
`cd backend && .venv/bin/python -m pytest -q` — mock OpenAI server (tier fallback, 401/404), LaTeX compile + overflow recovery + fabrication guards,
fake Notion MCP server over stdio (schema setup, live push, no duplicate pages, deleted pages, per-job backoff, two-way merge),
backup mirror (full / incremental / deletes / sequences / restore), job intake (real Chromium read, robots.txt, bot check,
the bookmarklet run in a browser), answer bank (saved answers fill a fixture form without the model, learning from a paused
form, re-queueing), queue order and transient-failure retries, notifications per destination, agenda/digest, readiness
check, dedupe/ATS parsing, Playwright dry-run on Greenhouse/Lever-style fixtures, and the job search graph end to end
(real headless Chromium on a fixture careers site and a CAPTCHA'd board, checkpoint resume). Storage tests run on SQLite and on a real
PostgreSQL: `TEST_DATABASE_URL` (CI uses a service container; tests only touch their own schemas) or else the embedded server.
`NOTION_TOKEN` enables a live Notion smoke test.
