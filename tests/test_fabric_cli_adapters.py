"""The CLI-mode adapters: the upstreams the owner listed, reachable through
the fabric without any of them appearing in a UI."""
from __future__ import annotations

import pytest

from friday import fabric

CLI_PROVIDERS = ("strix_pentest", "openworker_cli", "agenticseek_cli")


@pytest.mark.parametrize("pid", CLI_PROVIDERS)
def test_each_is_registered_pinned_and_isolated(pid):
    p = fabric.registry()[pid]
    assert p.integration_mode == fabric.CLI
    assert len(p.commit) == 40
    assert "version" in p.operations and "version" in p.open_operations


def test_the_gpl_worker_is_isolated_not_imported():
    p = fabric.registry()["agenticseek_cli"]
    assert p.license_mode == fabric.COPYLEFT
    assert not p.imported


def test_every_mutating_operation_is_gated():
    reg = fabric.registry()
    for pid in CLI_PROVIDERS:
        p = reg[pid]
        gated = set(p.operations) - set(p.open_operations)
        assert gated, pid
        assert p.permissions, f"{pid} has gated ops but declares no permission"


def test_a_gated_call_refuses_before_spawning(monkeypatch):
    import subprocess
    spawned = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(AssertionError))
    r = fabric.call("strix_pentest", "test", target="x", instruction="y")
    assert r.status == "failed" and "authorized_scope" in r.error
    assert spawned == []


def test_a_hostile_instruction_is_one_argv_element():
    from friday import fabric_cli
    from friday.fabric_adapters import strix_pentest as s
    argv = fabric_cli._fill(s.COMMANDS["test"].argv,
                            {"target": "t", "instruction": "x; rm -rf /"})
    assert "x; rm -rf /" in argv
    assert argv.count("--instruction") == 1


def test_unbuilt_upstream_is_unavailable_with_the_install_hint(monkeypatch):
    from friday import fabric_cli
    from friday.fabric_adapters import openworker_cli as m
    monkeypatch.setattr(fabric_cli.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no node")))
    out = fabric_cli.health(m.DESCRIPTOR, m.BOOTSTRAP)
    assert out["state"] == fabric.UNAVAILABLE
    assert "not built" in out["detail"]


def test_secrets_reach_the_child_as_environment_not_argv(monkeypatch):
    from friday import fabric_cli
    from friday.fabric_adapters import openworker_cli as m
    seen = {}

    def fake_run(provider, operation, commands, *, run_id="", **arguments):
        seen["env"] = commands[operation].env
        seen["args"] = arguments
        return "ok"

    monkeypatch.setattr(fabric_cli, "run", fake_run)
    m.call("version", secrets={"LLM_API_KEY": "sekrit"})
    assert seen["env"]["LLM_API_KEY"] == "sekrit"
    assert "secrets" not in seen["args"]


def test_the_matrix_has_no_unclassified_clone():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "m", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "integration_matrix.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.survey()["unclassified"] == []
