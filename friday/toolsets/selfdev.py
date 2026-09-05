"""
Controlled self-development as capability-runtime implementations
(PRD v3.1 FR-047..051). MCP faces in `friday/tools/selfdev_control.py`.

`selfdev_run` drives one candidate through every gate up to BENCHMARKED
and stops there: promotion into the live checkout needs a person's yes,
which arrives through `selfdev_promote` with the exact-action approval.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from friday import contracts as c
from friday import selfdev as SD
from friday.policy import PolicyEngine, default_engine

_CANDIDATES: dict[str, SD.Candidate] = {}
_LOCK = threading.Lock()


def _repo() -> Path:
    from friday.config import PROJECT_ROOT
    return Path(PROJECT_ROOT)


def _loop() -> SD.SelfDevelopment:
    return SD.SelfDevelopment(_repo())


def selfdev_run(run: c.Run, candidate_id: str, weakness: str, evidence: dict,
                proposal: str, files: list[str], patch: str,
                tests: list[str], regression: list[str] | None = None, *,
                engine: PolicyEngine = default_engine, infer=None,
                benchmark: dict | None = None) -> c.ActionResult:
    """OBSERVE -> PROPOSE -> SANDBOX -> IMPLEMENT (apply `patch`, a unified
    diff, inside the sandbox) -> TEST -> REVIEW -> REGRESSION -> BENCHMARK.
    Returns the candidate at BENCHMARKED or REJECTED; never promotes."""
    started = c.started(run.run_id, "selfdev.run")
    sd = _loop()
    try:
        cand = sd.observe(candidate_id, weakness, evidence)
    except SD.GateRefused as exc:
        return run.record(c.failed(started, str(exc)))
    with _LOCK:
        _CANDIDATES[candidate_id] = cand
    sd.propose(cand, proposal, list(files), tests=list(tests), regression=list(regression or []))
    if cand.state != SD.REJECTED:
        sd.sandbox(cand)
    if cand.state != SD.REJECTED:
        sd.implement(cand, lambda root: _apply_patch(root, patch))
    if cand.state != SD.REJECTED:
        sd.test(cand)
    if cand.state != SD.REJECTED:
        sd.review(cand, infer=infer) if infer is not None else sd.review(cand)
    if cand.state != SD.REJECTED:
        sd.regression(cand)
    if cand.state != SD.REJECTED:
        # FR-050: a change with a performance claim is measured in its
        # sandbox against the live tree and rejected on a regression past
        # SD.BENCHMARK_TOLERANCE. A change that could not move any metric
        # (docs, tests) records "no performance claim" instead - measuring
        # it would prove nothing. `benchmark=` lets a test inject its own
        # measurement; production takes the real one.
        if benchmark is None:
            from friday import selfdev_benchmark as B
            if B.claims(cand.files):
                sd.benchmark(cand, before=B.baseline(_repo()), measure=B.measure,
                             lower_is_better=B.LOWER_IS_BETTER)
            else:
                sd.benchmark(cand)
        else:
            sd.benchmark(cand, **benchmark)
    if cand.state == SD.REJECTED:
        sd.cleanup(cand)          # keeps the sandbox: it is the evidence
        return run.record(c.failed(started, f"rejected at {cand.history[-1]['from']}: "
                                            f"{cand.rejected_because}"))
    return run.record(c.succeeded(
        started, output={"candidate": cand.to_dict(), "next": "selfdev_promote needs approval"},
        verification=c.Verification(
            method="selfdev_gates",
            evidence=(f"{candidate_id} reached {cand.state} in sandbox {cand.worktree}: "
                      f"tests {cand.test_log!r}; review {cand.review['verdict'] if cand.review else '-'}; "
                      f"regression {cand.regression_log or 'none named'}; live checkout untouched"))))


def selfdev_promote(run: c.Run, candidate_id: str, *, approved: bool,
                    health=None, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """PROMOTE (merge) -> MONITOR (health probe; unhealthy rolls back)."""
    started = c.started(run.run_id, "selfdev.promote")
    with _LOCK:
        cand = _CANDIDATES.get(candidate_id)
    if cand is None:
        return run.record(c.failed(started, f"no candidate {candidate_id!r} in this process"))
    sd = _loop()
    try:
        sd.promote(cand, approved=approved)
    except SD.GateRefused as exc:
        return run.record(c.failed(started, str(exc)))
    if cand.state == SD.REJECTED:
        return run.record(c.failed(started, cand.rejected_because))
    sd.monitor(cand, health or _default_health)
    if cand.state == SD.ROLLED_BACK:
        return run.record(c.failed(
            started, f"promoted then rolled back: {cand.history[-1].get('reason', '')}; "
                     f"restored {cand.promotion.get('base_commit', '')[:12]}"))
    sd.cleanup(cand)
    return run.record(c.succeeded(
        started, output={"candidate": cand.to_dict()},
        verification=c.Verification(
            method="selfdev_promotion",
            evidence=(f"merge {cand.promotion['merge_commit'][:12]} onto "
                      f"{cand.promotion['target']}; rollback target "
                      f"{cand.promotion['base_commit'][:12]}; health probe passed"))))


def selfdev_rollback(run: c.Run, candidate_id: str, reason: str, *,
                     engine: PolicyEngine = default_engine) -> c.ActionResult:
    started = c.started(run.run_id, "selfdev.rollback")
    with _LOCK:
        cand = _CANDIDATES.get(candidate_id)
    if cand is None:
        return run.record(c.failed(started, f"no candidate {candidate_id!r} in this process"))
    try:
        _loop().rollback(cand, reason=reason or "operator rollback")
    except (SD.GateRefused, Exception) as exc:  # noqa: BLE001
        return run.record(c.failed(started, str(exc)))
    return run.record(c.succeeded(
        started, output={"candidate": cand.to_dict()},
        verification=c.Verification(
            method="git_revert",
            evidence=f"reverted {cand.promotion.get('result_commit', '')[:12]}; "
                     f"restored {cand.promotion.get('base_commit', '')[:12]}")))


def selfdev_status(run: c.Run, candidate_id: str = "", *,
                   engine: PolicyEngine = default_engine) -> c.ActionResult:
    started = c.started(run.run_id, "selfdev.status")
    with _LOCK:
        if candidate_id:
            cand = _CANDIDATES.get(candidate_id)
            out = cand.to_dict() if cand else None
        else:
            out = {cid: {"state": cd.state, "weakness": cd.weakness,
                         "rejected_because": cd.rejected_because}
                   for cid, cd in _CANDIDATES.items()}
    if out is None:
        return run.record(c.failed(started, f"no candidate {candidate_id!r}"))
    return run.record(started.finish(status=c.OBSERVED, output=out))


def _apply_patch(root: Path, patch: str) -> None:
    """Apply a unified diff inside the sandbox with git; a patch that does
    not apply raises, which `implement()` turns into a rejection."""
    import subprocess
    if not (patch or "").strip():
        raise ValueError("empty patch")
    out = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=str(root),
                         input=patch, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"patch did not apply: {(out.stderr or '').strip()[:300]}")


def _default_health() -> bool:
    """The live process is healthy if its own registry still imports and
    the MCP server hash check passes; cheap and real."""
    try:
        from friday import capabilities as C
        return len(C.CAPABILITIES) > 100
    except Exception:  # noqa: BLE001
        return False


def candidates_json() -> str:
    with _LOCK:
        return json.dumps({k: v.to_dict() for k, v in _CANDIDATES.items()}, default=str)
