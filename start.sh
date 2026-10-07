#!/usr/bin/env bash
# DairyFlow (macOS / Linux): backend on 127.0.0.1:8600, frontend on :5600 with the Vite proxy (/api and /ws -> backend).
#   ./start.sh               dev: the backend reloads when backend/*.py changes
#   ./start.sh --no-reload   demo: the backend never restarts
# Ports: DAIRYFLOW_API_PORT / DAIRYFLOW_UI_PORT. Route cache and logs: .cache/. Ctrl+C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export DAIRYFLOW_API_PORT="${DAIRYFLOW_API_PORT:-8600}"
export DAIRYFLOW_UI_PORT="${DAIRYFLOW_UI_PORT:-5600}"
export DAIRYFLOW_API_HOST=127.0.0.1
RELOAD_FLAG=""
[[ "${1:-}" == "--no-reload" ]] && RELOAD_FLAG="--no-reload"

for port in "$DAIRYFLOW_API_PORT" "$DAIRYFLOW_UI_PORT"; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    echo "Port $port is already in use (an earlier DairyFlow still running?). Stop it or set DAIRYFLOW_API_PORT / DAIRYFLOW_UI_PORT." >&2
    exit 1
  fi
done

PY="$ROOT/backend/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Creating backend/venv ..."
  python3 -m venv "$ROOT/backend/venv"
  "$PY" -m pip install -r "$ROOT/backend/requirements-dev.txt"
fi
if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "Installing frontend packages ..."
  (cd "$ROOT/frontend" && npm install)
fi

echo "Starting backend on http://127.0.0.1:$DAIRYFLOW_API_PORT $RELOAD_FLAG"
(cd "$ROOT/backend" && exec "$PY" main.py $RELOAD_FLAG) &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT INT TERM

# Wait for the backend before starting the UI, so the proxy never sees "connection refused".
for _ in $(seq 1 60); do
  if curl -s -o /dev/null -m 2 "http://127.0.0.1:$DAIRYFLOW_API_PORT/health"; then
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "Backend exited - see .cache/logs/backend.log" >&2
    exit 1
  fi
  sleep 1
done
curl -s -o /dev/null -m 2 "http://127.0.0.1:$DAIRYFLOW_API_PORT/health" || { echo "Backend did not start within 60 s" >&2; exit 1; }
echo "Backend is up. App: http://localhost:$DAIRYFLOW_UI_PORT   API docs: http://127.0.0.1:$DAIRYFLOW_API_PORT/docs"

cd "$ROOT/frontend"
npm run dev
