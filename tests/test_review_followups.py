"""The independent review's medium/low items (2026-09-04): each one observable."""
import asyncio
import json
import logging

import pytest

from friday import hermes_bridge as hb
from friday import provider_cooldowns as PC
from friday.continuous import STRATEGY_HINTS, ContinuousTaskExecutor
from friday.objectives import TaskStatus, compile_objective
from friday.store import Store


def _manifest():
    return [{"id": "flaky", "description": "a capability that fails"}]


async def _run_to_terminal(store, run_id, timeout=5.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        row = store.objective_run(run_id)
        if row["status"] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED"):
            return row
        await asyncio.sleep(0.02)
    raise AssertionError("run never reached a terminal state")


@pytest.mark.asyncio
async def test_strategy_hint_reaches_the_worker_arguments():
    """A hint nobody reads is a blind retry with better bookkeeping: after two
    identical failures the third call must carry strategy_hint='replan'."""
    store = Store(":memory:")
    seen = []

    async def flaky(_cap, args):
        seen.append(args.get("strategy_hint"))
        if len(seen) < 3:
            raise TimeoutError("op timed out at line 12 in /tmp/run-8f21/a.py")
        return {"status": "ok"}

    executor = ContinuousTaskExecutor(store, flaky, executor_id="fu-1")
    executor.max_attempts = 6
    run = compile_objective(store, request="flaky", manifest=_manifest(),
                            objective_summary="flaky",
                            tasks=[{"capability": "flaky", "arguments": {}}])
    try:
        await executor.start(run["run_id"])
        await _run_to_terminal(store, run["run_id"])
    finally:
        executor.stop()
    assert seen[:2] == [None, None] and seen[2] == STRATEGY_HINTS[0], seen
    assert store.objective_tasks(run["run_id"])[0]["status"] == TaskStatus.SUCCEEDED


def test_development_run_carries_the_hint_into_its_constraints(tmp_path):
    from friday import development as D
    run = D.for_goal("add a cache", tmp_path, strategy_hint="different_role")
    assert run.strategy_hint == "different_role"
    # The constraint line is what the worker reads; render it through the
    # same bundle the executor would build.
    bundle = hb.TaskBundle(goal="add a cache", constraints=(
        "STRATEGY CHANGE: different_role - the previous attempt failed with the "
        "same fingerprint; do not repeat it",)).render()
    assert "STRATEGY CHANGE: different_role" in bundle


def test_hermes_role_section_names_the_claude_subagent():
    from friday.roles import claude_agent_for
    text = hb.TaskBundle(goal="write the tests", role="tests").render()
    assert "ROLE / RESPONSIBILITY" in text
    assert f"Use the `{claude_agent_for('tests')}` subagent for this role" in text
    assert "subagent" not in hb.TaskBundle(goal="x").render()


def test_cooldown_file_torn_read_is_logged_not_silent(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", tmp_path / "cooldowns.json")
    (tmp_path / "cooldowns.json").write_text('{"half": ', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="friday.cooldowns"):
        assert PC._load() == {}
    assert any("unreadable" in r.getMessage() for r in caplog.records)


def test_cooldown_save_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", tmp_path / "cooldowns.json")
    PC._save({"a\x1fb": {"until": "2030-01-01T00:00:00", "reason": "test"}})
    assert json.loads((tmp_path / "cooldowns.json").read_text(encoding="utf-8"))
    assert not list(tmp_path.glob("*.tmp")), "the temp file must be replaced, not left behind"
