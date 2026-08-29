#!/usr/bin/env bash
set -euo pipefail

backend_health_url="${BACKEND_HEALTH_URL:-http://127.0.0.1:3000/health}"
frontend_url="${FRONTEND_URL:-http://127.0.0.1:5001/}"

check_url() {
  local url="$1"
  curl -fsS --max-time 10 "$url" >/dev/null
}

backend_ok=1
frontend_ok=1

if ! check_url "$backend_health_url"; then
  backend_ok=0
fi

if ! check_url "$frontend_url"; then
  frontend_ok=0
fi

if [[ "$backend_ok" -eq 0 ]]; then
  echo "Backend health check failed: $backend_health_url; restarting edia-backend.service"
  systemctl --user restart edia-backend.service
fi

if [[ "$frontend_ok" -eq 0 ]]; then
  echo "Frontend health check failed: $frontend_url; restarting edia-frontend.service"
  systemctl --user restart edia-frontend.service
fi

if [[ "$backend_ok" -eq 1 && "$frontend_ok" -eq 1 ]]; then
  echo "EDIA health check OK"
fi