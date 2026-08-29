#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/ysk2025/EDIA_5.1.1"
PYTHON_BIN="${PYTHON_BIN:-/home/ysk2025/.venv/bin/python}"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

cd "$ROOT"
export PYTHONPATH="$ROOT"
export DISABLE_RAG="${DISABLE_RAG:-true}"
export ENABLE_XGBOOST_FORECAST="${ENABLE_XGBOOST_FORECAST:-false}"
export ENABLE_LSTM_FORECAST="${ENABLE_LSTM_FORECAST:-false}"

"$PYTHON_BIN" "$ROOT/scripts/daily_forecast_event_cycle.py" "$@" 2>&1 | tee -a "$LOG_DIR/daily-forecast-event-cycle.log"
