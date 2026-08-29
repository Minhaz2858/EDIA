#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="$HOME/.config/systemd/user"

mkdir -p "$unit_dir"

cat >"$unit_dir/edia-backend.service" <<EOF
[Unit]
Description=EDIA backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$repo_root
Environment=HOME=$HOME
Environment=HOST=0.0.0.0
Environment=PORT=3000
Environment=ENABLE_BACKGROUND_WORKERS=false
ExecStart=/usr/bin/bash "$repo_root/scripts/run-backend-cloud.sh"
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

cat >"$unit_dir/edia-frontend.service" <<EOF
[Unit]
Description=EDIA frontend
After=network-online.target edia-backend.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$repo_root
Environment=HOME=$HOME
Environment=HOST=0.0.0.0
Environment=PORT=5001
ExecStart=/usr/bin/bash "$repo_root/scripts/run-frontend-prod.sh"
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

cat >"$unit_dir/edia-health-watchdog.service" <<EOF
[Unit]
Description=EDIA health watchdog
After=edia-backend.service edia-frontend.service

[Service]
Type=oneshot
WorkingDirectory=$repo_root
Environment=HOME=$HOME
ExecStart=/usr/bin/bash "$repo_root/scripts/edia-health-watchdog.sh"
EOF

cat >"$unit_dir/edia-health-watchdog.timer" <<EOF
[Unit]
Description=Run EDIA health watchdog every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
Unit=edia-health-watchdog.service

[Install]
WantedBy=timers.target
EOF

loginctl enable-linger "$USER" >/dev/null 2>&1 || true
systemctl --user daemon-reload
systemctl --user enable --now edia-backend.service edia-frontend.service edia-health-watchdog.timer

echo "Installed and started user services:"
echo "  edia-backend.service"
echo "  edia-frontend.service"
echo "  edia-health-watchdog.timer"
echo
echo "Check status with: systemctl --user status edia-backend.service edia-frontend.service edia-health-watchdog.timer"
echo "View logs with: journalctl --user -u edia-backend.service -u edia-frontend.service -u edia-health-watchdog.service -f"