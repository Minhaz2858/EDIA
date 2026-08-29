#!/usr/bin/env bash
# ── EDIA Production Backend Launcher ────────────────────────────
# Starts the EDIA backend without --reload, using multiple uvicorn
# workers for stability.  Writes logs to backend_9008.log in the
# repo root.
#
# Usage:
#   bash scripts/start-backend-production.sh
#   PORT=9008 WORKERS=4 bash scripts/start-backend-production.sh
#
# To stop:
#   lsof -t -i :9008 | xargs kill
# ══════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# ── Configuration ────────────────────────────────────────────────
# DATABASE_URL must come from .env (or the environment) — never hardcode
# credentials in source.
export DATABASE_URL="${DATABASE_URL:-}"

# Disable expensive startup tasks — the background watcher will
# detect changes and refresh on demand.
export MARKET_PRICES_REFRESH_ON_STARTUP="${MARKET_PRICES_REFRESH_ON_STARTUP:-false}"
export FAST_MODE_RUNTIME="${FAST_MODE_RUNTIME:-1}"

# Use multiple workers so ARIMA fits on one worker don't block
# health checks and simple API calls on another.
export UVICORN_WORKERS="${UVICORN_WORKERS:-4}"

PORT="${PORT:-9008}"
PYTHON_BIN="${PYTHON_BIN:-/opt/anaconda3/bin/python}"

# ── Stop existing backend on this port ───────────────────────────
EXISTING=$(lsof -t -i ":${PORT}" 2>/dev/null || true)
if [ -n "$EXISTING" ]; then
  echo "Stopping existing backend on port ${PORT} (PID: $EXISTING)..."
  kill $EXISTING 2>/dev/null || true
  echo -n "  Waiting for port ${PORT} to be freed"
  for _i in $(seq 1 30); do
    if ! lsof -t -i ":${PORT}" >/dev/null 2>&1; then
      echo " done."
      break
    fi
    echo -n "."
    sleep 2
  done
  # Force kill if still hanging
  STILL=$(lsof -t -i ":${PORT}" 2>/dev/null || true)
  if [ -n "$STILL" ]; then
    echo ""
    echo "  Force-killing remaining processes on port ${PORT}..."
    kill -9 $STILL 2>/dev/null || true
    sleep 2
  fi
fi

# ── Start ────────────────────────────────────────────────────────
echo "Starting EDIA backend on port ${PORT} with ${UVICORN_WORKERS} workers..."
echo "  DATABASE_URL: ${DATABASE_URL:0:20}..."
echo "  Log: $(pwd)/backend_9008.log"

nohup "$PYTHON_BIN" -m uvicorn api.index:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers "$UVICORN_WORKERS" \
  --log-level info \
  --timeout-keep-alive 65 \
  >> backend_9008.log 2>&1 &

BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID"

# ── Wait for ready ───────────────────────────────────────────────
echo -n "Waiting for backend to become ready"
for i in $(seq 1 40); do
  if curl -s -o /dev/null "http://127.0.0.1:${PORT}/health" 2>/dev/null; then
    echo ""
    echo "Backend is ready on http://127.0.0.1:${PORT}"
    echo "  Health: $(curl -s http://127.0.0.1:${PORT}/health)"
    exit 0
  fi
  echo -n "."
  sleep 3
done

echo ""
echo "Backend started but not responding yet (PID: $BACKEND_PID)"
echo "Check: tail -f backend_9008.log"
exit 1
