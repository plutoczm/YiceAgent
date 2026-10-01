#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-8502}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
LOG_DIR="${PROJECT_ROOT}/data/logs"
PID_DIR="${PROJECT_ROOT}/data/temp"
BACKEND_PID_FILE="${PID_DIR}/backend-dev.pid"
FRONTEND_PID_FILE="${PID_DIR}/frontend-dev.pid"
BACKEND_PID=""
FRONTEND_PID=""
STOP_REQUESTED=0

mkdir -p "${LOG_DIR}" "${PID_DIR}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "Missing virtual environment at ${VENV_DIR}. Run scripts/bootstrap_venv.sh first." >&2
  exit 1
fi

is_running() {
  local pid_file="$1"
  [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" >/dev/null 2>&1
}

port_is_busy() {
  local port="$1"
  ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${port}$"
}

stop_pid() {
  local name="$1"
  local pid="$2"
  if [[ -z "${pid}" ]] || ! kill -0 "${pid}" >/dev/null 2>&1; then
    return
  fi

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
}

cleanup() {
  stop_pid "frontend" "${FRONTEND_PID}"
  stop_pid "backend" "${BACKEND_PID}"
  rm -f "${FRONTEND_PID_FILE}" "${BACKEND_PID_FILE}"
}

handle_stop() {
  STOP_REQUESTED=1
  echo
  echo "Stopping DataPilot-AI dev services..."
  cleanup
}

start_backend() {
  if is_running "${BACKEND_PID_FILE}"; then
    BACKEND_PID="$(cat "${BACKEND_PID_FILE}")"
    echo "Backend already running on pid ${BACKEND_PID}"
    return
  fi
  if port_is_busy "${BACKEND_PORT}"; then
    echo "Backend port ${BACKEND_PORT} is already in use." >&2
    exit 1
  fi
  (
    cd "${PROJECT_ROOT}"
    exec "${VENV_DIR}/bin/uvicorn" backend.app.main:app \
      --host "${BACKEND_HOST}" \
      --port "${BACKEND_PORT}"
  ) >"${LOG_DIR}/backend-dev.log" 2>&1 &
  BACKEND_PID="$!"
  echo "${BACKEND_PID}" >"${BACKEND_PID_FILE}"
}

start_frontend() {
  if is_running "${FRONTEND_PID_FILE}"; then
    FRONTEND_PID="$(cat "${FRONTEND_PID_FILE}")"
    echo "Frontend already running on pid ${FRONTEND_PID}"
    return
  fi
  if port_is_busy "${FRONTEND_PORT}"; then
    echo "Frontend port ${FRONTEND_PORT} is already in use." >&2
    exit 1
  fi
  (
    cd "${PROJECT_ROOT}"
    export BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
    exec "${VENV_DIR}/bin/streamlit" run frontend/app.py \
      --server.address "${FRONTEND_HOST}" \
      --server.port "${FRONTEND_PORT}" \
      --server.headless true \
      --browser.gatherUsageStats false
  ) >"${LOG_DIR}/frontend-dev.log" 2>&1 &
  FRONTEND_PID="$!"
  echo "${FRONTEND_PID}" >"${FRONTEND_PID_FILE}"
}

wait_for_exit() {
  while [[ "${STOP_REQUESTED}" -eq 0 ]]; do
    if [[ -n "${BACKEND_PID}" ]] && ! kill -0 "${BACKEND_PID}" >/dev/null 2>&1; then
      echo "Backend exited. See ${LOG_DIR}/backend-dev.log" >&2
      return 1
    fi
    if [[ -n "${FRONTEND_PID}" ]] && ! kill -0 "${FRONTEND_PID}" >/dev/null 2>&1; then
      echo "Frontend exited. See ${LOG_DIR}/frontend-dev.log" >&2
      return 1
    fi
    sleep 1
  done
}

trap handle_stop INT TERM
trap cleanup EXIT

start_backend
start_frontend

echo "DataPilot-AI dev services started."
echo "Backend:  http://127.0.0.1:${BACKEND_PORT}"
echo "Frontend: http://127.0.0.1:${FRONTEND_PORT}"
echo "Logs:     ${LOG_DIR}/backend-dev.log and ${LOG_DIR}/frontend-dev.log"
echo "按 Ctrl+C 停止服务。"

wait_for_exit
