#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
frontend_root="$repo_root/frontend"
publish_root="/var/tmp/edia_static"

npm_bin="${NPM_BIN:-}"

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
  echo "npm was not found. Install Node.js/npm first." >&2
  exit 1
fi

node_bin_dir="$(dirname "$npm_bin")"
export PATH="$node_bin_dir:$PATH"

if ! command -v node >/dev/null 2>&1; then
  echo "node is not on PATH. Set NPM_BIN explicitly or export PATH first." >&2
  exit 1
fi

cd "$frontend_root"

if [[ ! -d node_modules ]]; then
  "$npm_bin" install
fi

node ./node_modules/typescript/bin/tsc -b
node ./node_modules/vite/bin/vite.js build

rm -rf "$publish_root"
mkdir -p "$publish_root"
cp -a "$frontend_root/dist/." "$publish_root/"

find "$publish_root" -type d -exec chmod 755 {} +
find "$publish_root" -type f -exec chmod 644 {} +

echo "Published frontend to $publish_root"