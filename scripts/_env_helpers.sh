#!/usr/bin/env bash

resolve_python_from_pyvenv() {
  local cfg_path="$1"

  if [[ ! -f "$cfg_path" ]]; then
    return 1
  fi

  local executable
  executable="$(sed -n 's/^executable = //p' "$cfg_path" | head -n 1)"

  if [[ -n "$executable" && -x "$executable" ]]; then
    printf '%s\n' "$executable"
    return 0
  fi

  return 1
}

resolve_python_bin() {
  local repo_root="$1"
  local candidate

  if [[ -n "${PYTHON_BIN:-}" && -x "${PYTHON_BIN}" ]]; then
    printf '%s\n' "${PYTHON_BIN}"
    return 0
  fi

  local direct_candidates=(
    "/home/ysk2025/.venv/bin/python"
    "$repo_root/.venv-linux/bin/python"
    "$repo_root/.venv/bin/python"
    "$repo_root/venv/bin/python"
    "$repo_root/edia_4/bin/python"
  )

  for candidate in "${direct_candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  local pyvenv_candidates=(
    "$repo_root/edia_4/pyvenv.cfg"
    "$repo_root/.venv/pyvenv.cfg"
    "$repo_root/.venv-linux/pyvenv.cfg"
    "$repo_root/venv/pyvenv.cfg"
  )

  for candidate in "${pyvenv_candidates[@]}"; do
    if resolve_python_from_pyvenv "$candidate"; then
      return 0
    fi
  done

  local shell_candidates=(
    "$(command -v python3 2>/dev/null || true)"
    "$(command -v python 2>/dev/null || true)"
  )

  for candidate in "${shell_candidates[@]}"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

default_chroma_dir() {
  local repo_root="$1"
  printf '%s\n' "$repo_root/chroma_db"
}
