#!/usr/bin/env bash
# Starts FastAPI (:8000) and the Vite dev server (:5173). Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.local/node/bin:$PATH"

[ -d backend/.venv ] || { echo "Missing backend/.venv — see README (Setup)"; exit 1; }
[ -d frontend/node_modules ] || (cd frontend && npm install)
mkdir -p data artifacts browser_data

trap 'kill 0' EXIT INT TERM
(cd backend && .venv/bin/uvicorn main:app --reload --port 8000 --host 127.0.0.1) &
(cd frontend && npm run dev -- --host 127.0.0.1) &
echo "Dashboard: http://127.0.0.1:5173   API: http://127.0.0.1:8000/docs"
wait
