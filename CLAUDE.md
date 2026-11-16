# Seeko

Automates job discovery -> LaTeX resume tailoring -> application form filling -> Notion tracking.
All LLM calls use one OpenAI-compatible `/chat/completions` client (never `/responses`).

## Commands
- Dev (both servers): `./dev.sh`  -> UI :5173 (proxies /api), API :8000
- Backend tests: `cd backend && .venv/bin/python -m pytest -q`
- Frontend: `cd frontend && npx tsc -b && npx vite build`
- Toolchain is user-local: `~/.local/bin` (uv, tectonic), `~/.local/node/bin` (node). Python 3.12 venv at `backend/.venv`.

## Architecture (backend/)
- `llm/` `LLMClient` (AsyncOpenAI wrapper; `for_task("scoring"|"tailor"|"applier")` overrides model) + `structured.complete_json`
  (json_schema -> json_object -> prompt+regex, 2 repair retries; tier cached per base_url+model). `api/llm.py` has `POST /api/llm/test`.
- `config/` env settings (runtime-editable via UI), `CandidateProfile`, `base_resume.tex` in `data/`.
- `scraper/` `ats.py` (Greenhouse/Lever/Ashby/Workday JSON), `boards.py` (LinkedIn/Indeed/Glassdoor, persistent Playwright profile in `browser_data/`),
  `dedupe.py` (normalized company|title + canonical URL, unique indexes), `scorer.py`.
- `tailor/` `tailor_resume`: LLM edit -> `validate()` guards (preamble/sections/dates/new proper nouns) -> tectonic/pdflatex compile ->
  pypdf page count -> up to 2 repair passes -> fallback to base PDF (`fell_back`).
- `applier/` `extract.py` tags controls with `data-seeko-id`; `mapper.py` LLM maps fields (null = unknown); `engine.py` fills,
  never submits except `submit()`; `gate.py` decides review/submit/paused; open pages for review live in `gate.pending`.
- `mcp_notion/` stdio client to `npx -y @notionhq/notion-mcp-server`; tool names are discovered at runtime (`find_tool`).
- `workers/pipeline.py` single background loop; job status: scraped -> scored -> tailoring -> tailored -> applying -> needs_review -> applied/failed/skipped.
  Notion sync (`notion_service.py`) is best-effort: failures leave `notion_dirty=True` and are retried.

## Conventions / invariants
- Approval gate is the default; full-auto is opt-in, capped daily, and must pause on CAPTCHA/2FA/unknown required fields.
- Never fabricate resume content: tailoring may only rephrase existing facts. Keep the guards in `tailor/tailor.py:validate` strict.
- `mcp` is pinned `<2` (v2 renamed APIs). Tests with Playwright/tectonic need Chromium + tectonic installed; first tectonic run downloads its bundle.
- Board scraper selectors drift; keep them isolated in `scraper/boards.py`. Tests use fixtures/mocks, never live sites.
- Runtime data (`data/`, `artifacts/`, `browser_data/`, `.env`) is gitignored.
