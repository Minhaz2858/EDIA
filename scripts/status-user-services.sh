#!/usr/bin/env bash
set -euo pipefail

systemctl --user --no-pager --full status edia-backend.service edia-frontend.service edia-health-watchdog.timer