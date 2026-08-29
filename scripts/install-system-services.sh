#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_user="${SERVICE_USER:-ysk2025}"
service_group="${SERVICE_GROUP:-$service_user}"
user_home="${SERVICE_HOME:-/home/$service_user}"
unit_dir="/etc/systemd/system"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this script as root."
  echo "Example: sudo bash ./scripts/install-system-services.sh"
  exit 1
fi

cat >"$unit_dir/edia-backend.service" <<EOF
[Unit]
Description=EDIA backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$service_user
Group=$service_group
WorkingDirectory=$repo_root
Environment=HOME=$user_home
Environment=HOST=0.0.0.0
Environment=PORT=3000
Environment=ENABLE_BACKGROUND_WORKERS=false
ExecStart=/usr/bin/bash "$repo_root/scripts/run-backend-cloud.sh"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat >"$unit_dir/edia-frontend.service" <<EOF
[Unit]
Description=EDIA frontend
After=network-online.target edia-backend.service
Wants=network-online.target

[Service]
Type=simple
User=$service_user
Group=$service_group
WorkingDirectory=$repo_root
Environment=HOME=$user_home
Environment=HOST=0.0.0.0
Environment=PORT=5001
ExecStart=/usr/bin/bash "$repo_root/scripts/run-frontend-prod.sh"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now edia-backend.service edia-frontend.service

echo "Installed and started system services:"
echo "  edia-backend.service"
echo "  edia-frontend.service"
echo
echo "Check status with: systemctl status edia-backend.service edia-frontend.service"
echo "View logs with: journalctl -u edia-backend.service -u edia-frontend.service -f"