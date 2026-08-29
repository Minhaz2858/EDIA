#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="${BACKEND_PID_FILE:-$repo_root/.run/backend-cloud.pid}"
port="${PORT:-3000}"
health_url="${BACKEND_HEALTH_URL:-http://127.0.0.1:${port}/health}"

describe_pid_command() {
  local pid="$1"
  ps -p "$pid" -o args= 2>/dev/null | sed 's/^ *//'
}

find_listener_pid() {
  local target_port="$1"
  ss -ltnp 2>/dev/null | awk -v port=":${target_port}" '
    index($4, port) {
      if (match($0, /pid=[0-9]+/)) {
        print substr($0, RSTART + 4, RLENGTH - 4)
        exit
      }
    }
  '
}

echo "Backend status"

if [[ -f "$pid_file" ]]; then
  pid="$(tr -d '[:space:]' < "$pid_file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "pid_file: $pid_file"
    echo "managed_pid: $pid"
    echo "managed_cmd: $(describe_pid_command "$pid")"
  else
    echo "pid_file: $pid_file (stale)"
  fi
else
  echo "pid_file: missing"
fi

listener_pid="$(find_listener_pid "$port")"
if [[ -n "$listener_pid" ]] && kill -0 "$listener_pid" 2>/dev/null; then
  echo "listener_port: $port"
  echo "listener_pid: $listener_pid"
  echo "listener_cmd: $(describe_pid_command "$listener_pid")"
else
  echo "listener_port: $port (not listening)"
fi

if curl -fsS -m 5 "$health_url" >/tmp/edia_backend_health.$$ 2>/dev/null; then
  printf 'health: %s\n' "$(cat /tmp/edia_backend_health.$$)"
else
  echo "health: unreachable"
fi

rm -f /tmp/edia_backend_health.$$