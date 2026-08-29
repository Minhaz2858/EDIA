#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this script as root."
  echo "Example: sudo bash ./scripts/stop-system-services.sh"
  exit 1
fi

systemctl stop edia-frontend.service edia-backend.service
echo "Stopped edia-frontend.service and edia-backend.service"