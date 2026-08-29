#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this script as root."
  echo "Example: sudo bash ./scripts/status-system-services.sh"
  exit 1
fi

systemctl --no-pager --full status edia-backend.service edia-frontend.service