#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
frontend_root="$repo_root/frontend"
dist_root="$frontend_root/dist"

# shellcheck disable=SC1091
source "$repo_root/scripts/_env_helpers.sh"

npm_bin="${NPM_BIN:-}"
python_bin="${PYTHON_BIN:-}"

if [[ -z "$npm_bin" ]]; then
  candidates=(
    "$(command -v npm 2>/dev/null || true)"
    "$HOME/.local/node-v22/bin/npm"
    "$HOME/.local/bin/npm"
  )

  for candidate in "${candidates[@]}"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      npm_bin="$candidate"
      break
    fi
  done
fi

if [[ -z "$npm_bin" ]]; then
  cat <<'EOF'
npm was not found on this server.

Install Node.js and npm first. On Ubuntu or Debian:
  sudo apt update
  sudo apt install -y nodejs npm
EOF
  exit 1
fi

node_bin_dir="$(dirname "$npm_bin")"
export PATH="$node_bin_dir:$PATH"

if ! command -v node >/dev/null 2>&1; then
  cat <<EOF
The selected npm wrapper exists, but node is still not on PATH.

Set NPM_BIN explicitly or export PATH first, for example:
  export PATH="$node_bin_dir:\$PATH"
  bash ./scripts/run-frontend-prod.sh
EOF
  exit 1
fi

if [[ -z "$python_bin" ]]; then
  python_bin="$(resolve_python_bin "$repo_root" || true)"
fi

if [[ -z "$python_bin" ]]; then
  cat <<'EOF'
python3 was not found on this server.

Install Python first. On Ubuntu or Debian:
  sudo apt update
  sudo apt install -y python3
EOF
  exit 1
fi

needs_build=0

if [[ ! -f "$dist_root/index.html" ]]; then
  needs_build=1
elif find "$frontend_root/src" "$frontend_root/public" \
  "$frontend_root/index.html" "$frontend_root/package.json" \
  "$frontend_root/package-lock.json" "$frontend_root/vite.config.ts" \
  -type f -newer "$dist_root/index.html" -print -quit | grep -q .; then
  needs_build=1
fi

cd "$frontend_root"

if [[ ! -d node_modules ]]; then
  "$npm_bin" install
fi

if [[ "$needs_build" -eq 1 ]]; then
  "$npm_bin" run build
fi

host="${HOST:-0.0.0.0}"
port="${PORT:-5001}"

exec "$python_bin" "$repo_root/scripts/static_frontend_server.py" --host "$host" --port "$port" --dir "$dist_root"
