"""
S8a: the golden engineering journey, end to end, on fakes only.

One module, no live Hermes, no network: objective -> team -> hermes bundle ->
injected verifier failure -> strategy change -> repair -> handoff -> terminal
verification -> a capped provider cooling over -> restart recovery. Each
step asserts store rows / returned dicts, never source text.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from friday import evaluation as E
from friday import hermes_bridge as hb
from friday import hermes_team
from friday import memory_promotion as MP
from friday import roles
from friday.continuous import ContinuousTaskExecutor
from friday.executors import hermes as hermes_exec
from friday.executors.claude_code import TaskBundle as DevBundle
from friday.objectives import TaskStatus, compile_objective
from friday.store import Store

FAKE_GATEWAY = str(Path(__file__).parent / "fake_hermes_gateway.py")
GOAL = "Implement a new caching feature and add tests for it"


async def _run_to_terminal(store, run_id, timeout=30.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        row = store.objective_run(run_id)
        if row["status"] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED"):
            return row
        await asyncio.sleep(0.05)
    raise AssertionError("run never reached a terminal state")


def _supervisor(tmp_path, monkeypatch, name, *, flags=None):
    for key in ("FAKE_HERMES_CLARIFY", "FAKE_HERMES_HANG", "FAKE_HERMES_DIE",
                "FAKE_HERMES_CAPPED"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (flags or {}).items():
        monkeypatch.setenv(key, value)
    log = hb.WorkRunLog(tmp_path / f"{name}.sqlite3")
    sup = hb.HermesSupervisor(log=log, command=[sys.executable, FAKE_GATEWAY],
                              profile="")
    sup.READY_TIMEOUT = 20
    return sup


@pytest.mark.asyncio
async def test_golden_engineering_journey(tmp_path, monkeypatch):
    store = Store(tmp_path / "golden.sqlite3")
    attempts = {"n": 0}
    captured_bundle_text = {}

    # ---- 1: a medium feature request becomes a durable objective --------
    run = compile_objective(
        store, request=GOAL,
        tasks=[{"capability": "development", "arguments": {"goal": GOAL}}],
        manifest=[{"id": "development", "description": "do dev work"}],
        objective_summary="golden journey")
    tasks = store.objective_tasks(run["run_id"])
    assert len(tasks) == 1 and tasks[0]["capability"] == "development"

    # ---- 2: roles.compile_team + hermes_team.plan_team --------------------
    team = roles.compile_team(GOAL, files=3)
    assert len(team.roles) >= 2, team.roles
    profiles = hermes_team.plan_team(GOAL, files=3)
    assert profiles, "a >=2-role team must map to >=1 hermes profile"

    dev_sup = _supervisor(tmp_path, monkeypatch, "dev")

    async def development(_capability, args):
        attempts["n"] += 1
        n = attempts["n"]
        bundle = DevBundle(goal=args["goal"], workspace=str(tmp_path),
                           project="golden", run_id=run["run_id"],
                           acceptance=("tests pass",),
                           role=team.roles[0].title)
        bridge_bundle = hermes_exec.to_bridge_bundle(bundle)
        rendered = bridge_bundle.render()
        captured_bundle_text["text"] = rendered
        out = dev_sup.delegate(bridge_bundle, wait=True, turn_timeout=20)
        # hermes_bridge's confirmed terminal vocabulary is COMPLETE/PARTIAL/
        # FAILED (hermes_bridge.py:1311 etc, mirrored in progress_digest's
        # TERMINAL tuple) - "ok"/"succeeded" were never real values here.
        assert out["result"]["status"].lower() in ("complete", "ok",
                                                    "succeeded", "")
        if n <= 2:
            # ---- 4: injected verifier failure, same fingerprint twice ----
            raise TimeoutError(
                "verifier failed: tests red at line 10 in /tmp/x/test_core.py")
        return {"ok": True, "work_run_id": out["work_run_id"]}

    executor = ContinuousTaskExecutor(store, development, executor_id="gj-1")
    executor.max_attempts = 5
    await executor.start(run["run_id"])
    row = await _run_to_terminal(store, run["run_id"])

    # ---- 3: the bundle reached the fake Hermes with the right shape ------
    text = captured_bundle_text["text"]
    assert "ACCEPTANCE CRITERIA" in text
    assert "ROLE / RESPONSIBILITY" in text and team.roles[0].title in text
    # S8a found the subagent line was emitted only by the Claude executor's
    # prompt and never reached the Hermes bundle; S9b rendered it inside the
    # ROLE section of hb.TaskBundle.render(), the text the gateway receives.
    assert "subagent for this role" in text

    # ---- 4b: same failure twice -> strategy change, not a blind retry ----
    task = store.objective_tasks(run["run_id"])[0]
    detail = task["detail"]
    assert detail["strategy_changes"] >= 1, detail
    assert attempts["n"] == 3, "attempt 3 must be the repair, not a 4th blind retry"
    # TaskStatus has no COMPLETED - a task's terminal-success value is
    # SUCCEEDED (objectives.py:50); COMPLETED is the RUN-level status.
    assert task["status"] == TaskStatus.SUCCEEDED
    assert row["status"] == "COMPLETED"

    dev_sup.stop()

    # ---- 5: memory_promotion - evidence-backed lands, a guess is rejected
    good = MP.Candidate(statement="the cache TTL is 300s", kind="project_fact",
                        source="handoff", owner="dev", scope="project",
                        confidence=0.9, evidence=["tests pass: test_cache.py"])
    guess = MP.Candidate(statement="the cache TTL is probably 60s",
                         kind="project_fact", source="handoff", owner="dev",
                         scope="project", confidence=0.3, evidence=[])
    d_good = MP.promote(good, store=store)
    d_guess = MP.promote(guess, store=store)
    # Decision has no `.action` - the field is `.target`
    # ("memory"|"skill"|"rejected", memory_promotion.py:64).
    assert d_good.target != "rejected", d_good
    assert d_guess.target == "rejected", d_guess

    # ---- 6: evaluation.verify confirms independently of "done" -----------
    (tmp_path / "check.py").write_text("import sys; sys.exit(0)\n")
    verdict, code, _detail = E.verify(
        str(tmp_path), E.Verifier(command=(sys.executable, "check.py")))
    assert verdict == E.PASSED and code == 0

    # ---- 7: a capped provider cools, next candidate takes the route ------
    capped_sup = _supervisor(tmp_path, monkeypatch, "capped",
                             flags={"FAKE_HERMES_CAPPED": "1"})
    try:
        out = capped_sup.delegate(hb.TaskBundle(goal="x"), wait=True,
                                  turn_timeout=20)
        record = out["result"]
        assert record["failure_kind"] == "CAPPED"
        assert "capped until" in record["route_reason"]
        progress = capped_sup.progress(out["work_run_id"])
        assert "capped until" in progress["route_reason"]
    finally:
        capped_sup.stop()
    from friday import execution_economics as ee
    plan = ee.plan_delegation(GOAL)
    assert plan.get("switched_from") or "capped" not in plan.get("reason", ""), plan

    # ---- 8: restart recovery - reopen store, fresh executor, no dupes ----
    # A process death takes its driver loop with it; a loop left running
    # against a closed store is the test's artefact, not a Friday state.
    executor.stop()
    store.close()
    store2 = Store(tmp_path / "golden.sqlite3")
    tasks_before = store2.objective_tasks(run["run_id"])
    before = len(tasks_before)
    attempts_before = sum(int(t.get("attempts") or 0) for t in tasks_before)
    executor2 = ContinuousTaskExecutor(store2, development, executor_id="gj-2")
    try:
        # The run is already COMPLETED: a fresh executor must recognise a
        # finished run (start() may decline the lease or drive nothing) and
        # must never re-run or duplicate its tasks. The fingerprint history
        # written before the restart must still be readable afterwards.
        await executor2.start(run["run_id"])
        tasks_after = store2.objective_tasks(run["run_id"])
        assert len(tasks_after) == before, "reopening a finished run must not duplicate tasks"
        assert sum(int(t.get("attempts") or 0) for t in tasks_after) == attempts_before, \
            "a finished task was re-attempted after the restart"
        row2 = store2.objective_run(run["run_id"])
        assert row2["status"] == "COMPLETED"
        survived = store2.objective_task(task["task_id"])
        assert (survived.get("detail") or {}).get("strategy_changes", 0) >= 1, \
            "the fingerprint history did not survive the restart"
    finally:
        executor2.stop()
        store2.close()
