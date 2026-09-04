#!/usr/bin/env bash
# post-commit-check.sh — stub for project-specific post-commit guards.
#
# Wired in `.claude/settings.json` against the PostCommit event (or as a
# Stop-hook trigger in setups without a native PostCommit). Examples of what
# you might add:
#   - reject commits that include secrets (use git-secrets / trufflehog)
#   - reject commits that touch CI files without a matching label
#   - reject commits that bypass a lint/typecheck pre-commit hook
#
# This template is intentionally empty so generated teams ship a wired hook
# they can fill in without modifying settings.json.

set -u

# Read hook payload (unused by default — uncomment if you need it).
# input=$(cat)

# >>> Add your checks here.
# Example skeleton:
#
#   message=$(git log -1 --pretty=%B)
#   if echo "$message" | grep -qiE '(BEGIN PRIVATE KEY|sk_live_|aws_access_key_id)'; then
#     echo "post-commit-check: secret-like content in last commit message" >&2
#     exit 1
#   fi
# <<<

exit 0
