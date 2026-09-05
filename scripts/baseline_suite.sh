#!/usr/bin/env bash
# Thin wrapper: the canonical runner is scripts/baseline_suite.py (audit A-029).
# Usage: bash scripts/baseline_suite.sh [outdir] [extra args...]
set -u
OUT="${1:-data/baseline}"
shift || true
PY=".venv-verify/Scripts/python.exe"
[ -x "$PY" ] || PY=".venv-verify/bin/python"
[ -x "$PY" ] || PY="python"
exec "$PY" scripts/baseline_suite.py --out "$OUT" "$@"
