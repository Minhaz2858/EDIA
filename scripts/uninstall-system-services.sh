#!/usr/bin/env bash
set -euo pipefail

unit_dir="/etc/systemd/system"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this script as root."
  echo "Example: sudo bash ./scripts/uninstall-system-services.sh"
  exit 1
fi

systemctl disable --now edia-frontend.service edia-backend.service >/dev/null 2>&1 || true
rm -f "$unit_dir/edia-backend.service" "$unit_dir/edia-frontend.service"
systemctl daemon-reload

echo "Removed edia-backend.service and edia-frontend.service"