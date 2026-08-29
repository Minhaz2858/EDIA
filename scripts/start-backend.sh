#!/usr/bin/env bash
# Start EDIA backend on port 9009
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PORT="${PORT:-9009}"
PYTHON_BIN="${PYTHON_BIN:-/opt/anaconda3/bin/python}"

# Kill existing backend on this port if any
EXISTING=$(lsof -t -i :$PORT 2>/dev/null || true)
if [ -n "$EXISTING" ]; then
  echo "Stopping existing backend on port $PORT (PID: $EXISTING)..."
  kill $EXISTING 2>/dev/null || true
  sleep 2
fi

echo "Starting backend on port $PORT..."
nohup "$PYTHON_BIN" -m uvicorn api.index:app --host 0.0.0.0 --port "$PORT" > backend.log 2>&1 &
echo "Backend PID: $!"

# Wait for it to be ready
for i in {1..30}; do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/health" 2>/dev/null; then
    echo "Backend is ready on http://127.0.0.1:$PORT"
    exit 0
  fi
  sleep 2
done
echo "Backend started but not responding yet. Check backend.log"
