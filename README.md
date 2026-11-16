# Seeko

Find jobs, tailor your LaTeX resume per role (without inventing anything), fill the application, and track everything in Notion.

## Setup
Requires Python 3.11+, Node 20+, a LaTeX compiler (`tectonic` recommended, or `pdflatex`).
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
2. Paste or upload `base_resume.tex` (it must compile), fill in the profile, add company career URLs (Greenhouse, Lever, Ashby, Workday).
3. Optional: *Log in to LinkedIn/Indeed/Glassdoor* opens a visible browser; sign in once, close it. Sessions persist in `browser_data/`.
4. Optional: Notion — create an internal integration, share your tracker database with it, paste token + database ID, *Test Notion MCP*.
   Required properties: Role (title), Company, Source, Job URL (url), Match Score (number), Status (select/status), Tailored Summary, Applied Date (date).
5. **Pipeline tab → Run discovery.** Jobs are scored, tailored, compiled, and the form is filled. In the default **approval gate** mode the browser stays on the filled form
   and you click **Approve & Submit** (or Skip) in the dashboard.

## Safety
- Approval gate is the default. Full-auto is opt-in, has a daily cap, and pauses on CAPTCHA / 2FA / unanswerable required fields.
- Tailoring is validated: preamble, section names, dates and companies must be unchanged and no new tools/proper nouns may appear; otherwise your untailored resume is used.
- Form answers come only from your resume/profile; unknown required fields are left for you.
- LinkedIn/Indeed/Glassdoor restrict automation in their terms and may restrict your account. They are off by default; use low volumes at your own risk.
- If the server restarts while a job awaits approval, the live browser page is lost (approve returns 409); re-run that job.

## Tests
`cd backend && .venv/bin/python -m pytest -q` — mock OpenAI server (tier fallback, 401/404), LaTeX compile + overflow recovery + fabrication guards,
fake Notion MCP server over stdio, dedupe/ATS parsing, Playwright dry-run on Greenhouse/Lever-style fixtures. `NOTION_TOKEN` enables a live Notion smoke test.
