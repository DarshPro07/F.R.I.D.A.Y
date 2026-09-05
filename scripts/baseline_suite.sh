#!/usr/bin/env bash
# Phase 0 baseline: run the deterministic suite in 4 chunks against the exact
# tree, writing one log per chunk plus a summary. Usage: bash scripts/baseline_suite.sh [outdir]
set -u
OUT="${1:-data/baseline}"
mkdir -p "$OUT"
PY=".venv-verify/Scripts/python.exe"
mapfile -t FILES < <(ls tests/test_*.py | sort)
TOTAL=${#FILES[@]}
CHUNK=$(( (TOTAL + 3) / 4 ))
echo "commit=$(git rev-parse --short HEAD) files=$TOTAL chunk=$CHUNK date=$(date -Iseconds)" > "$OUT/summary.txt"
i=0
while [ $i -lt 4 ]; do
  START=$(( i * CHUNK ))
  SLICE=("${FILES[@]:$START:$CHUNK}")
  if [ ${#SLICE[@]} -gt 0 ]; then
    "$PY" -m pytest "${SLICE[@]}" -m "not live and not slow" -q -p no:cacheprovider > "$OUT/chunk$i.log" 2>&1
    echo "chunk$i exit=$? $(tail -n 1 "$OUT/chunk$i.log")" >> "$OUT/summary.txt"
  fi
  i=$(( i + 1 ))
done
cat "$OUT/summary.txt"
