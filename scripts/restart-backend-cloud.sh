#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec env RESTART_BACKEND=1 "$repo_root/scripts/run-backend-cloud.sh"