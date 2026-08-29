#!/usr/bin/env bash
set -euo pipefail

systemctl --user stop edia-health-watchdog.timer edia-frontend.service edia-backend.service
echo "Stopped edia-health-watchdog.timer, edia-frontend.service, and edia-backend.service"