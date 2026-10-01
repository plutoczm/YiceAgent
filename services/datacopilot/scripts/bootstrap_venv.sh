#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/opt/miniconda3/bin/python3}"
VENV_DIR="${PROJECT_ROOT}/.venv"

mkdir -p \
  "${PROJECT_ROOT}/data/cache/pip" \
  "${PROJECT_ROOT}/data/temp"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

export PIP_CACHE_DIR="${PROJECT_ROOT}/data/cache/pip"
export TMPDIR="${PROJECT_ROOT}/data/temp"

"${VENV_DIR}/bin/python" -m pip install --upgrade pip pytest
"${VENV_DIR}/bin/python" -m pip install -r "${PROJECT_ROOT}/backend/requirements.txt"

echo "Virtual environment ready: ${VENV_DIR}"
