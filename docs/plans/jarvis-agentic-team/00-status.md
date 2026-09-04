# jarvis-agentic-team — status

**Feature:** Friday/Jarvis production-grade upgrade, built by a model-routed
Claude Code team (Fable orchestrating; Opus / Sonnet / Haiku specialists drawn
from `fadymondy/agents-team` and `VoltAgent/awesome-claude-code-subagents`).
The same two packs become Friday runtime roles through the capability fabric,
so the owner's team and Friday's team are the same roster ("for you and Hermes both").

**Started:** 2026-09-03 09:56 IST. **Verdict:** PARTIALLY_VERIFIED (`07-final.md`) — code,
gate (3,406 passed) and Playwright (43 passed) verified; LiveKit phases, a live Hermes
delegation, and the owner's :8770/:8000 restart remain.

| Gate | State |
|---|---|
| 0 Reality | done — `01-reality.md` |
| 1 Product | done — `02-product.md` |
| 2 Architecture | done — `03-architecture.md` |
| 3 Program design | done — `04-program-design.md` |
| 4 Slices | in progress — `05-slices.md` |
| 5 Verification | pending — `06-verification.md` |

## Slices

| # | Slice | Owner (model) | State |
|---|---|---|---|
| S1 | Packs added to the owner's Claude Code (global agents + agents-team plugin) and a linted project team in `.claude/` | Sonnet | done (49 agents, plugin, 9 agents A/100) |
| S2 | Hermes result → shared memory write-back | Sonnet | done (6 tests, 64 green) |
| S3 | Portion-level token cap + honest control-room metrics | Opus | done (4 tests, 73 green) |
| S4 | Both packs pinned + fabric SKILL providers + org divisions (Friday runtime) | Sonnet | done (46 clones pinned, 95 green; ops renamed by S6a) |
| S5 | Master validation prompt v2 | Fable | done (14 phases; corrected from live evidence) |
| S6 | Live validation: Playwright suite + Claude-in-Chrome pass | Fable | done: passes A/B/C; gate 3,406 passed; Playwright 43 passed on the final tree |
| S7 | Gate 5 independent verification | Fable (authored none of S1–S4; no verifier agent per the token rule) | diff-level challenge done, see 06 |
| S8 | Token discipline for all future agents (research + rules + frontmatter caps + selective roster) | Fable → S1 applies | protocol written (`token-discipline.md`); roster measured 12.3k tokens; install capped |

| S9 | Full autonomy mode (no "okay"), self-check from the master prompt, status-hijack guard, spoken-okay approval fix | Fable | done (10 new tests; affected suites 111 passed) |
| S10 | All five web-app upstreams as remote HTTP helpers (postiz, anythingllm, open-notebook, maxun, openmontage), `/api/helpers` + Helpers panel + `helpers/list`, spoken writes under full autonomy | 3 builders (Sonnet) + Fable | done (30/46 integrated; fabric suites 152 passed; helpers spec 2/2) |
| S11 | Opus read-only review of the whole session diff; gate + Playwright on the final tree; restart | critic (Opus) + Fable | done (9 findings, 7 fixed; gate 3,455 passed; Playwright 45 passed; live stack restarted 15:38) |

| S12 | Full autonomy is the default (no switch phrase); any phrasing toggles it | Fable | done (103 passed on the four suites; live stack restarted via the launcher 18:20) |

## Next action (owner)
Nothing to switch: Friday starts in full autonomy. Run the LiveKit-only phases of the master
prompt (1, 2.3b, 3.3–3.4, 4, 12.2–12.4, 13, 15.x with real helper instances); say
"full autonomy off" if you ever want the yes/no gates back; decide on commit, secrets
rotation, rate limits and the `_PLANS` lock (2026-09-02 proposals).

## Constraints in force
- Never touch `data/ada.sqlite3`; test runs use `data/e2e-ada.sqlite3` or tmp DBs.
- No commits, no secrets rotation, no live-process restarts without the owner
  (a restart is needed before Python edits reach :8000 / :8770).
- `/omc-teams` and `/setup` cannot run here: no `tmux`, no `omc` CLI. The native
  Agent tool is the team runtime; external CLI workers are out of scope.
- Real-test rule: every critical fix ships with a test proven to fail on the old code.
