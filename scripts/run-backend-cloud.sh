#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

run_dir="$repo_root/.run"
mkdir -p "$run_dir"

backend_pid_file="${BACKEND_PID_FILE:-$run_dir/backend-cloud.pid}"

# shellcheck disable=SC1091
source "$repo_root/scripts/_env_helpers.sh"

inherited_disable_rag="${DISABLE_RAG-}"
inherited_hf_hub_offline="${HF_HUB_OFFLINE-}"
inherited_transformers_offline="${TRANSFORMERS_OFFLINE-}"
inherited_rag_embedding_backend="${RAG_EMBEDDING_BACKEND-}"
inherited_enable_news_ingestion_agent="${ENABLE_NEWS_INGESTION_AGENT-}"
inherited_web_search_mode="${WEB_SEARCH_MODE-}"

if [[ -f "$repo_root/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$repo_root/.env"
  set +a
fi

configured_enable_news_ingestion_agent="${ENABLE_NEWS_INGESTION_AGENT-}"
configured_web_search_mode="${WEB_SEARCH_MODE-}"

export LLM_ENABLE_EXTERNAL_REASONING="1"
export CHAT_EXTERNAL_REASONING_DEFAULT="1"
export LLM_LOCAL_NO_THINK="1"
export LLM_EXTERNAL_NO_THINK="${LLM_EXTERNAL_NO_THINK:-0}"
export DISABLE_LOCAL_LLM="1"
export LLM_EXTERNAL_ALLOW_UNSANITIZED="1"
export STRICT_KIMI_ONLY="1"
export LIVE_DATA_ONLY="1"

python_bin="${PYTHON_BIN:-}"

if [[ -z "$python_bin" ]]; then
  python_bin="$(resolve_python_bin "$repo_root" || true)"
fi

if [[ -z "$python_bin" ]]; then
  cat <<'EOF'
No usable Python environment was found.

Create one with:
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/python -m pip install -r requirements.txt

Or set PYTHON_BIN to an existing interpreter.
EOF
  exit 1
fi

export CHROMA_PERSIST_DIRECTORY="${CHROMA_PERSIST_DIRECTORY:-$(default_chroma_dir "$repo_root")}" 

host="${HOST:-0.0.0.0}"
port="${PORT:-3000}"

cleanup_pid_file() {
  rm -f "$backend_pid_file"
}

describe_pid_command() {
  local pid="$1"
  ps -p "$pid" -o args= 2>/dev/null | sed 's/^ *//'
}

find_listener_pid() {
  local target_port="$1"
  ss -ltnp 2>/dev/null | awk -v port=":${target_port}" '
    index($4, port) {
      if (match($0, /pid=[0-9]+/)) {
        print substr($0, RSTART + 4, RLENGTH - 4)
        exit
      }
    }
  '
}

terminate_pid() {
  local pid="$1"
  local signal="${2:-TERM}"
  kill "-$signal" "$pid" 2>/dev/null || return 1

  local attempts=20
  while (( attempts > 0 )); do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 0.25
    attempts=$((attempts - 1))
  done

  return 1
}

if [[ -f "$backend_pid_file" ]]; then
  existing_pid="$(tr -d '[:space:]' < "$backend_pid_file")"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    existing_cmd="$(describe_pid_command "$existing_pid")"
    if [[ "$existing_cmd" == *"uvicorn api.index:app"* ]]; then
      if [[ "${RESTART_BACKEND:-0}" == "1" ]]; then
        terminate_pid "$existing_pid" TERM || terminate_pid "$existing_pid" KILL || true
      else
        cat <<EOF
Backend already appears to be running.
  pid: $existing_pid
  cmd: $existing_cmd

Use RESTART_BACKEND=1 to replace the running backend, or stop it first.
EOF
        exit 1
      fi
    else
      rm -f "$backend_pid_file"
    fi
  else
    rm -f "$backend_pid_file"
  fi
fi

listener_pid="$(find_listener_pid "$port")"
if [[ -n "$listener_pid" ]] && kill -0 "$listener_pid" 2>/dev/null; then
  listener_cmd="$(describe_pid_command "$listener_pid")"
  if [[ "$listener_cmd" == *"uvicorn api.index:app"* ]] && [[ "${RESTART_BACKEND:-0}" == "1" ]]; then
    terminate_pid "$listener_pid" TERM || terminate_pid "$listener_pid" KILL || true
  else
    cat <<EOF
Port $port is already in use.
  pid: $listener_pid
  cmd: $listener_cmd

Stop the existing process or rerun with RESTART_BACKEND=1 if it is the old EDIA backend.
EOF
    exit 1
  fi
fi

export DISABLE_RAG="${inherited_disable_rag:-0}"
export HF_HUB_OFFLINE="${inherited_hf_hub_offline:-1}"
export TRANSFORMERS_OFFLINE="${inherited_transformers_offline:-1}"
export RAG_EMBEDDING_BACKEND="${inherited_rag_embedding_backend:-hash}"

fast_mode_runtime="${FAST_MODE_RUNTIME:-0}"
if [[ "$fast_mode_runtime" == "1" ]]; then
  export ENABLE_NEWS_INGESTION_AGENT="0"
  export WEB_SEARCH_MODE="off"
else
  export ENABLE_NEWS_INGESTION_AGENT="${inherited_enable_news_ingestion_agent:-${configured_enable_news_ingestion_agent:-0}}"
  export WEB_SEARCH_MODE="${inherited_web_search_mode:-${configured_web_search_mode:-off}}"
fi

if ! "$python_bin" -c "import uvicorn" >/dev/null 2>&1; then
  cat <<EOF
The selected Python environment exists, but the backend dependencies are not installed.

Install them with:
  python3 -m venv .venv
  $python_bin -m pip install --upgrade pip
  $python_bin -m pip install -r requirements.txt
EOF
  exit 1
fi

trap cleanup_pid_file EXIT
printf '%s\n' "$$" > "$backend_pid_file"

exec "$python_bin" -m uvicorn api.index:app --host "$host" --port "$port"
