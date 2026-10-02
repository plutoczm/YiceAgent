#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="${PROJECT_ROOT}/data/temp"

stop_service() {
  local name="$1"
  local pid_file="$2"
  if [[ ! -f "${pid_file}" ]]; then
    echo "${name} is not running."
    return
  fi

  local pid
  pid="$(cat "${pid_file}")"
  if kill -0 "${pid}" >/dev/null 2>&1; then
    kill "${pid}" >/dev/null 2>&1 || true
    for _ in {1..20}; do
      if ! kill -0 "${pid}" >/dev/null 2>&1; then
        break
      fi
      sleep 0.2
    done
    if kill -0 "${pid}" >/dev/null 2>&1; then
      kill -9 "${pid}" >/dev/null 2>&1 || true
    fi
    echo "Stopped ${name} pid ${pid}."
  else
    echo "${name} pid ${pid} is not running."
  fi
  rm -f "${pid_file}"
}

stop_service "frontend" "${PID_DIR}/frontend-dev.pid"
stop_service "backend" "${PID_DIR}/backend-dev.pid"
