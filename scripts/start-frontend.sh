#!/usr/bin/env bash
# Start EDIA frontend on port 9090
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../frontend"

PORT="${PORT:-9090}"

# Kill existing frontend on this port if any
EXISTING=$(lsof -t -i :$PORT 2>/dev/null || true)
if [ -n "$EXISTING" ]; then
  echo "Stopping existing frontend on port $PORT (PID: $EXISTING)..."
  kill $EXISTING 2>/dev/null || true
  sleep 2
fi

echo "Starting frontend on port $PORT..."
nohup npx vite --host 127.0.0.1 --port "$PORT" > ../frontend_server.log 2>&1 &
echo "Frontend PID: $!"

sleep 3
if curl -s -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
  echo "Frontend is ready on http://127.0.0.1:$PORT"
else
  echo "Frontend started but not responding yet. Check frontend_server.log"
fi
