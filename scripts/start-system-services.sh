#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this script as root."
  echo "Example: sudo bash ./scripts/start-system-services.sh"
  exit 1
fi

systemctl start edia-backend.service edia-frontend.service
echo "Started edia-backend.service and edia-frontend.service"