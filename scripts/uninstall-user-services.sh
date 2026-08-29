#!/usr/bin/env bash
set -euo pipefail

unit_dir="$HOME/.config/systemd/user"

systemctl --user disable --now edia-health-watchdog.timer edia-frontend.service edia-backend.service >/dev/null 2>&1 || true
rm -f \
	"$unit_dir/edia-backend.service" \
	"$unit_dir/edia-frontend.service" \
	"$unit_dir/edia-health-watchdog.service" \
	"$unit_dir/edia-health-watchdog.timer"
systemctl --user daemon-reload

echo "Removed edia-backend.service, edia-frontend.service, and edia-health-watchdog.timer"