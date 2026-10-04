"""
CLI-001: one work packet, two CLI adapters, one output contract.

The same development `TaskBundle` goes through the real `ClaudeCodeExecutor`
(only the CLI process is faked) and the real `HermesExecutor` over the real
bridge supervisor (only the gateway process is faked). Each terminal result
must read as the PRD v7 section 46 output: the same eight fields, filled from
what that adapter actually reported and nothing it did not.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from friday import contracts as c
from friday import development
from friday import hermes_bridge as hb
from friday.executors import claude_code as CC
from friday.executors import cli
from friday.executors import hermes as hermes_exec
from friday.executors.hermes import HermesExecutor
from friday.executors.work_packet import OUTPUT_FIELDS, RESULT_CHARS, output_of
from friday.store import Store

FAKE_GATEWAY = str(Path(__file__).parent / "fake_hermes_gateway.py")
SAID = "Added the cache and its tests."


@pytest.fixture
def bundle(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return CC.TaskBundle(
        goal="add a TTL cache to the uploader", workspace=str(workspace),
        project="uploader", acceptance=("tests pass", "no new dependency"),
        constraints=("keep the public API",), isolate=False)


class _FakeRun:
    """The claude CLI process, faked: it streams a short, successful run."""

    instances: list["_FakeRun"] = []

    def __init__(self, launch, sandbox=None):
        self.launch = launch
        self.process = None
        self.session_id = ""
        _FakeRun.instances.append(self)

    async def events(self, *, timeout: float = 1800.0):
        yield cli.Event("init", {"session_id": "s-1"})
        yield cli.Event("tool", {"type": "assistant"}, name="Edit")
        yield cli.Event("result", {"result": SAID, "is_error": False,
                                   "num_turns": 2})


def _direct(executor, bundle):
    """Drive an executor the way its own tests do: straight at `execute`."""
    return asyncio.run(executor.execute(bundle, timeout=20))


def _claude_result(tmp_path, bundle, monkeypatch, drive=_direct):
    _FakeRun.instances = []
    monkeypatch.setattr(cli, "available", lambda: True)
    monkeypatch.setattr(cli, "Run", _FakeRun)
    monkeypatch.setattr(CC.ClaudeCodeExecutor, "changed_files",
                        staticmethod(lambda workspace: ["friday/uploader.py"]))
    executor = CC.ClaudeCodeExecutor(Store(tmp_path / "ada.sqlite3"))
    return drive(executor, bundle)


def _hermes_result(tmp_path, bundle, monkeypatch, drive=_direct):
    for key in ("FAKE_HERMES_CLARIFY", "FAKE_HERMES_HANG", "FAKE_HERMES_DIE",
                "FAKE_HERMES_CAPPED"):
        monkeypatch.delenv(key, raising=False)
    supervisor = hb.HermesSupervisor(
        log=hb.WorkRunLog(tmp_path / "hermes.sqlite3"),
        command=[sys.executable, FAKE_GATEWAY], profile="")
    supervisor.READY_TIMEOUT = 20
    try:
        return drive(HermesExecutor(None, supervisor=supervisor), bundle)
    finally:
        supervisor.stop()


def test_the_same_packet_reaches_both_adapters_with_its_acceptance(
        tmp_path, bundle, monkeypatch):
    _claude_result(tmp_path, bundle, monkeypatch)
    (run,) = _FakeRun.instances
    sent_to_hermes = hermes_exec.to_bridge_bundle(bundle).render()
    for text in (run.launch.prompt, sent_to_hermes):
        assert bundle.goal in text
        for line in (*bundle.acceptance, *bundle.constraints):
            assert line in text, f"{line!r} did not reach one of the adapters"


def test_both_adapters_return_the_section_46_output(tmp_path, bundle, monkeypatch):
    claude = _claude_result(tmp_path, bundle, monkeypatch)
    hermes = _hermes_result(tmp_path, bundle, monkeypatch)
    assert claude.status == hermes.status == c.SUCCEEDED, (claude.error, hermes.error)

    packets = {"claude": output_of(claude), "hermes": output_of(hermes)}
    for name, packet in packets.items():
        assert tuple(packet) == OUTPUT_FIELDS, name
        assert packet["status"] == c.SUCCEEDED, name
        assert isinstance(packet["result"], str) and packet["result"], name
        for field in ("files_changed", "tests", "evidence", "artifacts",
                      "blockers", "remaining_risk"):
            assert isinstance(packet[field], list), (name, field)
        assert packet["evidence"], f"{name} reported success with no evidence"
        assert packet["blockers"] == [], name

    # What each adapter knew is carried; what it did not is admitted.
    assert packets["claude"]["result"] == SAID
    assert packets["claude"]["files_changed"] == ["friday/uploader.py"]
    assert packets["hermes"]["files_changed"] == []
    assert "did not report which files changed" in " ".join(
        packets["hermes"]["remaining_risk"])
    assert "did not report which files changed" not in " ".join(
        packets["claude"]["remaining_risk"])


def test_the_manager_report_carries_the_packet_from_either_adapter(
        tmp_path, bundle, monkeypatch):
    # The team path needs a kanban board; this is the single-executor path.
    monkeypatch.setattr(development.hermes_team, "plan_team", lambda *a, **k: ())
    runs = {}

    def through_a_run(executor, packet_bundle):
        run = development.DevelopmentRun(
            goal=packet_bundle.goal, project=packet_bundle.project,
            root=packet_bundle.workspace)
        result = asyncio.run(run.execute(executor, timeout=20))
        runs[len(runs)] = (run, result)
        return result

    _claude_result(tmp_path, bundle, monkeypatch, drive=through_a_run)
    _hermes_result(tmp_path, bundle, monkeypatch, drive=through_a_run)

    for run, result in runs.values():
        reported = run.report()["work_packet"]
        assert reported == output_of(result)
        assert tuple(reported) == OUTPUT_FIELDS
        assert reported["status"] == c.SUCCEEDED
    claude, hermes = (run.report()["work_packet"] for run, _ in runs.values())
    assert claude["files_changed"] == ["friday/uploader.py"]
    assert hermes["files_changed"] == []


def test_a_run_that_never_executed_reports_no_packet(tmp_path):
    run = development.DevelopmentRun(goal="x", project="p", root=str(tmp_path))
    assert run.report()["work_packet"] is None


def test_a_failed_run_still_returns_every_field(tmp_path, bundle, monkeypatch):
    monkeypatch.setattr(cli, "available", lambda: False)
    executor = CC.ClaudeCodeExecutor(Store(tmp_path / "ada.sqlite3"))
    failed = asyncio.run(executor.execute(bundle))
    assert failed.status == c.FAILED

    packet = output_of(failed)
    assert tuple(packet) == OUTPUT_FIELDS
    assert packet["status"] == c.FAILED
    assert packet["blockers"] == [failed.error]
    assert packet["evidence"] == []
    assert "no verification was recorded for this run" in packet["remaining_risk"]


def test_a_long_narrative_is_cut_and_the_cut_is_admitted():
    started = c.started("R-1", "executor.test")
    long = c.succeeded(
        started, output={"result": "x" * (RESULT_CHARS + 50)},
        verification=c.Verification(method="m", evidence="e"))
    packet = output_of(long)
    assert len(packet["result"]) == RESULT_CHARS
    assert f"the result was cut to {RESULT_CHARS} characters" in packet["remaining_risk"]
