#!/usr/bin/env bash
set -euo pipefail

systemctl --user start edia-backend.service edia-frontend.service edia-health-watchdog.timer
echo "Started edia-backend.service, edia-frontend.service, and edia-health-watchdog.timer"