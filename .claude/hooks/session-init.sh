#!/usr/bin/env bash
# session-init.sh — print project context at session start.
#
# Wired in `.claude/settings.json` against the SessionStart event.
# Generic across teams: shows team name, git branch + last commit, pending
# plans/requests counts. Add team-specific service checks in the section
# marked `# >>> Team-specific checks (edit me)`.

set -u

cd "${CLAUDE_PROJECT_DIR:-.}"

TEAM_NAME="F.R.I.D.A.Y"

echo "=== ${TEAM_NAME} ==="
echo

# Git context
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
LAST_COMMIT=$(git log -1 --format='%h %s' 2>/dev/null || echo "no commits")
echo "Branch:      $BRANCH"
echo "Last commit: $LAST_COMMIT"

# Pending work counts (.plans/, .requests/, .meetings/ are conventional dirs
# emitted by skills like /meet and the plan-first rule)
PLANS=$(ls .plans/*.md      2>/dev/null | wc -l | tr -d ' ')
REQUESTS=$(ls .requests/*.md 2>/dev/null | wc -l | tr -d ' ')
MEETINGS=$(ls .meetings/*.md 2>/dev/null | wc -l | tr -d ' ')

if [[ "$PLANS" -gt 0 || "$REQUESTS" -gt 0 || "$MEETINGS" -gt 0 ]]; then
  echo
  echo "Pending: ${PLANS} plans, ${REQUESTS} requests, ${MEETINGS} meetings"
fi

# >>> Team-specific checks (edit me)
# Example: report which generated services exist
# [[ -d "app"    ]] && echo "  app/    (frontend)"
# [[ -d "bridge" ]] && echo "  bridge/ (api)"
# <<< End team-specific checks

echo
echo "Use /meet to hold a team meeting, /evaluate-agent to lint an agent."
