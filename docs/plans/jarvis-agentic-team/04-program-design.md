# 04 — Program design

The four builder prompts (S1–S4) are the executable form of this document; each
builder appends its actual design to `05-slices.md` when it reports.

## Files
| Slice | Create | Modify |
|---|---|---|
| S1 | `.claude/team.json`, `.claude/agents/friday-*.md`, `.claude/rules/*.md`, `.claude/hooks/*` (staged), `docs/plans/jarvis-agentic-team/team.md`; `~/.claude/agents/<voltagent>.md` | `.claude/settings.json` (merge only) |
| S2 | `tests/test_hermes_memory_writeback.py` | `friday/hermes_bridge.py`, possibly `friday/memory_stack.py`, `friday/store.py` |
| S3 | tests in `tests/test_continuity.py` / `tests/test_ui_server.py` | `friday/continuity.py`, `friday/continuity_livekit.py`, `friday/ui_server.py`, `ui/index.html` |
| S4 | `friday/fabric_adapters/claude_subagents.py`, `friday/fabric_adapters/agents_team_pack.py`, `tests/test_fabric_agent_packs.py` | `friday/org.py`, `scripts/upstream_lock.py`, `scripts/integration_matrix.py`, `third_party/UPSTREAM_LOCK.json`, `docs/integrations/INTEGRATION_STATUS.md`, `THIRD_PARTY_NOTICES.md` |
| S5 | `docs/MASTER_VALIDATION_PROMPT.md` (v2, supersedes v1 in place) | — |

## Types and contracts
- `hermes_bridge.on_terminal(record) -> None` (idempotent per work_run_id; sanitized; never raises).
- `continuity.RunSnapshot.budget_exhausted: str`; `ContinuityManager.remaining_budget(claim) -> dict`.
- `ui_server._metrics(conn, run_id: str | None) -> {model_tokens, open_tasks, runs_by_state, avg_run_secs, all_time: {model_tokens, open_tasks}}`.
- Fabric `Provider` descriptors for the two packs: `family="roles"`, `mode=SKILL`, `commit` pinned, `open_operations` declared.
- `org.divisions()` returns agency-agents divisions first, then VoltAgent categories (`source` lists both).

## Call flows
1. Gateway event → `WorkRunLog.update(status=terminal)` → `on_terminal` → store/vault → delivery broker (unchanged).
2. `on_usage_updated` → `record_model_tokens` → exhausted? → `checkpoint(reason)` + `budget_exhausted` event → `_deactivate`.
3. `/api/state` → `build_state` → `_objective` → `_metrics(conn, run_id)`.
4. `use_capability(roles, recipe, {path})` → `fabric.call_with_fallback` → `_skillpack.read`.

## State transitions
Portion: claimed → running → (budget) exhausted → checkpointed; run: running → partial (`budget_exhausted:<what>`).
Work run: WORKING → COMPLETE|PARTIAL|FAILED → (new) memory_written=1.

## Error model
Memory write errors are logged and swallowed inside the bridge. Budget errors are
events, not exceptions. Adapter errors surface as `FabricError` → UNAVAILABLE.

## Test design (each with its known failure mode)
- `test_terminal_run_is_written_to_shared_memory` — fails today: nothing writes.
- `test_outcome_visible_to_next_hermes_bundle` — fails today: `with_memory()` cannot see it.
- `test_outcome_with_secret_shape_is_refused` — guards NON_NEGOTIABLE 4.
- `test_portion_token_cap_marks_claim_exhausted` — fails today: 33k on a 32k portion passes silently.
- `test_run_budget_finishes_immediately_not_at_next_claim` — fails today.
- `test_metrics_are_scoped_to_the_current_objective` — fails today: all-time sums.
- `test_agent_pack_descriptors_match_lock`, `test_skillpack_read_refuses_traversal`, `test_org_includes_voltagent_divisions`.
- `lint.py` grade ≥ B for every generated agent (self-eval gate).

## Playwright journeys (S6)
`happy-path.spec.ts` envelope still passes with the new `all_time` key; a new
assertion that the header shows the objective-scoped numbers; existing 36 specs green.
Claude-in-Chrome pass on a `--bypass-face` instance (:8781, `ADA_DB=data/e2e-ada.sqlite3`):
clock/now, files write→list→delete, desktop plan (no step), roles/executives,
roles/recipe for a VoltAgent brief, commerce/products honest-unreachable, hermes/status.

## Least-confident decisions
1. Which memory tier the Hermes outcome belongs to (must be visible with `include_episodes=False`).
2. Whether the LiveKit turn can be steered mid-portion or only stopped before the next action.
3. Whether `claude plugin install` works non-interactively on this Windows host.

## Rollback
`git checkout -- <modified files>`; delete the new files; remove the two clones from
`third_party/upstream/` and their lock rows; delete `.claude/agents/friday-*.md`,
`.claude/rules/`, `.claude/hooks/`, restore `.claude/settings.json` from its `.bak`;
delete the VoltAgent files from `~/.claude/agents/` (they carry a `# source:` marker line).
