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
4. Optional: Notion — create an internal integration, share your tracker database with it, paste token + database ID, *Test Notion MCP*.
   Required properties: Role (title), Company, Source, Job URL (url), Match Score (number), Status (select/status), Tailored Summary, Applied Date (date).
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
- **What is stored.** `jobs` (full description as text, `job_type` job|internship, posted/expiry dates, remote, source of the text,
  times seen), `search_runs` (spec + statistics per run), `job_sightings`, `seen_urls`, and LangGraph checkpoints — an interrupted run
  resumes where it stopped (Tracker → Searches → *Resume*, or the CLI). Schema changes are Alembic migrations, applied at startup;
  an existing `data/seeko.db` (SQLite) is imported once into an empty PostgreSQL.

CLI, from `backend/`:
```bash
.venv/bin/python -m jobsearch run                              # profile + settings
.venv/bin/python -m jobsearch run --title "Data Analyst" --type internship --source internshala --location Kochi
.venv/bin/python -m jobsearch runs | jobs --type internship | show <job_id> | resume <run_id> | db
```
LangGraph Studio: `pip install "langgraph-cli[inmem]"` and `langgraph dev` in `backend/` (graph `job_search`, see `langgraph.json`).

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

## Tests
`cd backend && .venv/bin/python -m pytest -q` — mock OpenAI server (tier fallback, 401/404), LaTeX compile + overflow recovery + fabrication guards,
fake Notion MCP server over stdio, dedupe/ATS parsing, Playwright dry-run on Greenhouse/Lever-style fixtures, and the job search graph end to end
(real headless Chromium on a fixture careers site and a CAPTCHA'd board, checkpoint resume). Storage tests run on SQLite and on a real
PostgreSQL: `TEST_DATABASE_URL` (CI uses a service container; tests only touch their own schemas) or else the embedded server.
`NOTION_TOKEN` enables a live Notion smoke test.
