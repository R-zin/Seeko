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
- `mcp_notion/` stdio client to `npx -y @notionhq/notion-mcp-server`; tool names are discovered at runtime (`find_tool`).
- `workers/pipeline.py` single background loop; job status: scraped -> scored -> tailoring -> tailored -> applying -> needs_review -> applied/failed/skipped.
  Notion sync (`notion_service.py`) is best-effort: failures leave `notion_dirty=True` and are retried.

## Conventions / invariants
- Approval gate is the default; full-auto is opt-in, capped daily, and must pause on CAPTCHA/2FA/unknown required fields.
- Never fabricate resume content: tailoring may only rephrase existing facts. Keep `tailor/tailor.py:check_edit` and `validate` strict;
  every new guard needs a test that fails without it (see `tests/test_tailor.py`, `tests/test_resume.py`).
- `mcp` is pinned `<2` (v2 renamed APIs). Tests with Playwright/tectonic need Chromium + tectonic installed; first tectonic run downloads its bundle.
- Board scraper selectors drift; keep them isolated in `scraper/boards.py`. Tests use fixtures/mocks, never live sites.
- Runtime data (`data/`, `artifacts/`, `browser_data/`, `.env`) is gitignored.
