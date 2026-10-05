# EVAL-004: Arenas for research, skills, compression and capability paths

Status: Built 2026-10-05 for model route, skill and compression; research and
capability path wait on the open question below. Risk MEDIUM. Services: objective-engine
(skill ladder), mcp-tools (route benchmarks, compression). No service reaches into
another's internals: every consumer calls the new `friday.arena` seam.

## Problem

EVAL-003 made one empirical promotion gate, `friday/route_benchmarks.py`, and
hard-wired it to one kind of candidate (key `provider/model`, input a
`golden.Report`, one consumer `model_gateway.benchmark_verdict`). The other
arena kinds have no gate:

- Skills: `SkillLadder.validate(name, passed=, validation=)` takes a caller's
  boolean and free text, so a candidate becomes VALIDATED with nothing behind it
  (`friday/skill_ladder.py:93-106`).
- Compression: `CompressionCoordinator.register()` accepts any callable and
  `transform()` uses it by name; nothing compares alternatives and there is no
  notion of a promoted default (`friday/compression.py:252-255,332-348`).
- Research and capability path (CAPB-001): not read, see "Open question".

## Design

`friday/arena.py` is the one owner of arena logic; `route_benchmarks` becomes its
`model_route` adapter.

- `ArenaKind`: `model_route`, `skill`, `compression`, `research`, `capability_path`.
- `Score(gates, cases, metrics, score)`: what a per-kind scorer returns. `gates`
  has the same shape as `golden` `summary()["gates"]`; pass/fail is derived from
  it, never from a flag. An empty gate table is refused (no failed gates over zero
  gates proves nothing).
- `compare(kind, ids, scorer)` scores every candidate and returns an
  `ArenaReport`; `build_report` does the same from scores already in hand (the
  route adapter has one golden report per route). The winner is the highest
  scoring candidate that passed every gate; ties go to the earlier candidate (the
  incumbent is listed first); no passing candidate means no winner.
- `ArenaReport`: `report_id` (a content hash, re-verified on load), kind, commit,
  date, per-candidate result (passed, failed gates, cases, score, metrics), winner.
- `ArenaStore`: JSON, atomic replace, re-read on every call. Reports are kept by
  id; `latest` maps `(kind, candidate_id)` to the newest report, so the latest
  report replaces an earlier one for promotion while an earlier promotion's cited
  report stays retrievable. A file that exists but cannot be read raises
  `ArenaStoreError`; it never reads as "no evidence".
- `promote(kind, candidate_id, report_id, store, exclusive=False)` returns a
  `Promotion` or raises `PromotionRefused` unless the report exists, is the same
  kind, lists the candidate, the candidate passed every gate in it, and it is
  still the candidate's latest report. `exclusive=True` (one default, as for
  compression) also requires the candidate to be that report's winner. A
  declaration, a flag, or free text cannot reach this function's yes.
- `replay_scorer(cases, check, ...)`: the shared scorer shape for replayable
  kinds (success-rate and minimum-case gates), the same pattern as
  `context_lab.run()/summarize()`.

## Wiring

| Point | Change |
|---|---|
| `route_benchmarks.py` | Public API kept (`BenchmarkStore` with `get`/`record_report`/`path`, `Measurement` plus a `report_id`, `store()`, `BenchmarkStoreError`); storage and gate reduction delegate to `arena` as `kind=model_route`. `measurement_from_report` and `Measurement.to_dict/from_dict` are gone: nothing called them and the file format they served is replaced. `tests/test_route_benchmarks.py` stays green unchanged. |
| `skill_ladder.py` `validate` | `passed=True` reaches VALIDATED only with an `arena_report` id that `arena.promote` accepts for candidate `name@vN`; otherwise status `refused`, state unchanged. `passed=False` still REJECTs (the safe direction needs no proof). |
| `compression.py` | `promote_backend(name, report_id)` (exclusive) sets the promoted default; `transform(backend="")` resolves to it, re-checked against the store each call, so a later failing report (or an unreadable store) demotes it until it is promoted again. Unmeasured backends stay registered and usable by name, never promoted. `compare_backends(corpus)` scores registered backends on critical-fact retention and size. |

## Assumptions (revisit if wrong)

1. The latest report per `(kind, candidate)` replaces earlier ones; no expiry.
2. A candidate below target stays usable but unpromoted (as EVAL-003); only
   `skill` validation is refused outright, because a skill with no proof was never
   in use.
3. Nothing is migrated: no `route_benchmarks.json` exists on this machine, so the
   route store moving to the arena file format loses no evidence.
4. A scorer that raises is recorded as a failed `scored` gate with the exception
   type, not propagated and not hidden: one broken third-party candidate must not
   stop the comparison, and it can never win.

## Open question (not built)

For research (`friday/research_cache.py`) and capability path
(`friday/capability_router.py`), does "promotion" mean a change of default
selection or admission to a registry? Their promotion points were never read and
the answer changes the design, so only the kinds exist in `ArenaKind` (they can be
compared and reported) and no promotion point is wired. Ask the owner before
building those two. AskUserQuestion is unavailable in this unattended build run,
so this is recorded as a gap, not guessed.

## Proof

Failing-first: `tests/test_arena.py` (new), `tests/test_skill_ladder.py`,
`tests/test_compression.py` extended; `tests/test_route_benchmarks.py`,
`tests/test_context_lab.py`, `tests/test_golden_suite.py`, `tests/test_reachability.py`
unchanged and green. Run in `.venv-build` with pytest's own exit code.
