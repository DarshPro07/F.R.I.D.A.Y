#!/usr/bin/env bash
# teammate-idle-gate.sh — block a teammate from going idle if they have an
# unfinished task (most recent TaskCreated has no matching TaskCompleted).
#
# Wired in `.claude/settings.json` against the TeammateIdle event.
# Exit 2 = block + feed message back to model. Any error exits 0 (never block
# on a tooling problem).
#
# Reads `.claude/agent-activity.log` (JSON-lines), which notify.sh and the
# log-only hooks populate. Format: one JSON object per line with `event`,
# `teammate`, `task`, `id`.

set -u

LOG="${CLAUDE_PROJECT_DIR:-.}/.claude/agent-activity.log"

input=$(cat)
teammate=$(echo "$input" | jq -r '.teammate_name // empty' 2>/dev/null)

if [[ -z "$teammate" || ! -f "$LOG" ]]; then
  exit 0
fi

last_created=$(grep '"event":"TaskCreated"' "$LOG" 2>/dev/null \
  | grep "\"teammate\":\"$teammate\"" \
  | tail -1)

if [[ -z "$last_created" ]]; then
  exit 0
fi

task_id=$(echo "$last_created"   | jq -r '.id   // empty' 2>/dev/null)
task_name=$(echo "$last_created" | jq -r '.task // empty' 2>/dev/null)

if [[ -z "$task_id" ]]; then
  exit 0
fi

completed=$(grep '"event":"TaskCompleted"' "$LOG" 2>/dev/null \
  | grep "\"id\":\"$task_id\"" \
  | tail -1)

if [[ -n "$completed" ]]; then
  exit 0
fi

echo "You still have task \"$task_name\" (id: $task_id) in progress — complete it before going idle." >&2
exit 2
