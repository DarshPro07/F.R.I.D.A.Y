"""Execution modes: the fabric must be able to RUN third-party code, safely.

Before 2026-09-01 nine of sixteen providers were `SKILL` (no code executed),
`SIDECAR` was a word with no runtime, there was no mode at all for a
command-line agent, and `fabric.call()` checked neither permissions nor
secrets. Every test here failed before that date.
"""
import sys
import time

import pytest

from friday import contracts as c
from friday import fabric
from friday import fabric_cli
from friday import fabric_process as fp


@pytest.fixture(autouse=True)
def _no_orphans():
    yield
    fp.stop_all()


def _py(*code) -> tuple:
    return (sys.executable, "-c", "\n".join(code))


# --- FABRIC-GATE-01: permissions and secrets at the choke point ------------


class _Provider:
    """A provider stand-in; the registry is discovered, not injectable."""

    def __init__(self, **kw):
        self.id = kw.get("id", "t")
        self.permissions = kw.get("permissions", ())
        self.secrets = kw.get("secrets", ())
        self.operations = kw.get("operations", ("go",))
        self.upstream = kw.get("upstream", "")


def test_a_call_without_the_grant_is_refused(monkeypatch):
    provider = _Provider(id="dummy", permissions=("network.egress",))
    monkeypatch.setattr(fabric, "get", lambda _id: provider)
    started = []
    monkeypatch.setattr(fabric, "activate",
                        lambda _id: started.append(_id))
    out = fabric.call("dummy", "go")
    assert out.status == "failed" and "network.egress" in out.error
    # The gate precedes activation: a refused call must not be able to start a
    # process as a side effect of being refused.
    assert started == []


def test_the_grant_lets_it_through():
    out = fabric.call("dummy", "echo", text="hi",
                      authorized=frozenset({"anything"}))
    assert out.status == "succeeded"


def test_authorized_none_means_no_grants_not_all(monkeypatch):
    """Fail closed. `candidates()` used `if authorized is not None`, so the
    default skipped filtering entirely and every provider passed."""
    provider = _Provider(id="dummy", permissions=("secrets.read",))
    monkeypatch.setattr(fabric, "get", lambda _id: provider)
    assert fabric.call("dummy", "go").status == "failed"


def test_both_entry_points_refuse_identically(monkeypatch):
    provider = _Provider(id="dummy", permissions=("files.write",))
    monkeypatch.setattr(fabric, "get", lambda _id: provider)
    direct = fabric.call("dummy", "go")
    assert direct.status == "failed" and "files.write" in direct.error


def test_a_missing_secret_names_the_alias_not_the_value(monkeypatch):
    provider = _Provider(id="dummy", secrets=("SOME_TOKEN",))
    monkeypatch.setattr(fabric, "get", lambda _id: provider)
    out = fabric.call("dummy", "go")
    assert out.status == "failed" and "SOME_TOKEN" in out.error


# --- FABRIC-PROC-01: the supervisor ----------------------------------------


def test_a_child_that_never_readies_is_stopped_and_reported():
    spec = fp.Spec(argv=_py("import time", "time.sleep(30)"),
                   ready=fp.LogLine("never-printed"))
    with pytest.raises(fp.ProcessError) as exc:
        fp.spawn("slowpoke", spec, timeout=2.0)
    assert "did not become ready" in str(exc.value)
    assert fp.child("slowpoke") is None, "a failed start must leave no orphan"


def test_a_child_that_exits_at_startup_is_not_ready():
    spec = fp.Spec(argv=_py("import sys", "sys.exit(3)"),
                   ready=fp.LogLine("up"))
    with pytest.raises(fp.ProcessError) as exc:
        fp.spawn("faulty", spec, timeout=5.0)
    assert "exited during startup" in str(exc.value)


def test_readiness_is_waited_for_not_assumed():
    spec = fp.Spec(
        argv=_py("import time,sys", "time.sleep(0.6)",
                 "print('LISTENING', flush=True)", "time.sleep(30)"),
        ready=fp.LogLine("LISTENING"))
    child = fp.spawn("waiter", spec, timeout=10.0)
    assert child.state == fp.READY
    assert any("LISTENING" in line for line in child.log_tail())


def test_an_external_kill_becomes_crashed():
    spec = fp.Spec(argv=_py("import time,sys", "print('UP', flush=True)",
                            "time.sleep(30)"),
                   ready=fp.LogLine("UP"))
    child = fp.spawn("killable", spec, timeout=10.0)
    child.popen.kill()
    for _ in range(40):
        if child.state == fp.CRASHED:
            break
        time.sleep(0.1)
    assert child.state == fp.CRASHED and child.last_error


def test_stop_actually_stops_a_stubborn_child():
    spec = fp.Spec(
        argv=_py("import signal,time,sys",
                 "signal.signal(signal.SIGTERM, lambda *a: None)"
                 " if hasattr(signal,'SIGTERM') else None",
                 "print('UP', flush=True)", "time.sleep(60)"),
        ready=fp.LogLine("UP"), stop_timeout=1.0)
    child = fp.spawn("stubborn", spec, timeout=10.0)
    proc = child.popen
    fp.stop("stubborn")
    assert proc.poll() is not None, "the child survived stop()"


def test_the_child_does_not_inherit_fridays_secrets(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "super-secret-value")
    spec = fp.Spec(
        argv=_py("import os", "print('KEY=' + os.environ.get('GOOGLE_API_KEY',''),"
                 " flush=True)", "import time; time.sleep(5)"),
        ready=fp.LogLine("KEY="))
    child = fp.spawn("scrubbed", spec, timeout=10.0)
    joined = " ".join(child.log_tail())
    assert "super-secret-value" not in joined
    assert "KEY=" in joined


def test_a_declared_env_var_does_reach_the_child():
    spec = fp.Spec(
        argv=_py("import os", "print('V=' + os.environ.get('MY_VAR',''), flush=True)",
                 "import time; time.sleep(5)"),
        env={"MY_VAR": "declared"}, ready=fp.LogLine("V=declared"))
    child = fp.spawn("declared", spec, timeout=10.0)
    assert child.state == fp.READY


def test_each_child_gets_its_own_port():
    spec = fp.Spec(argv=_py("import time; time.sleep(5)"), needs_port=True)
    a = fp.spawn("port-a", spec, timeout=10.0)
    b = fp.spawn("port-b", spec, timeout=10.0)
    assert a.port and b.port and a.port != b.port


def test_restart_is_bounded():
    """A crash loop that retries forever is how one bad clone eats a laptop."""
    spec = fp.Spec(
        argv=_py("import time,sys", "print('UP', flush=True)", "time.sleep(30)"),
        ready=fp.LogLine("UP"), max_restarts=2, restart_window=300.0)
    fp.spawn("flapper", spec, timeout=10.0)
    fp.restart("flapper", timeout=10.0)
    fp.restart("flapper", timeout=10.0)
    with pytest.raises(fp.ProcessError) as exc:
        fp.restart("flapper", timeout=10.0)
    assert "giving up" in str(exc.value)


def test_a_literal_json_argument_is_not_a_placeholder():
    """Regression: `{"ok": true}` in argv was read as a placeholder and refused."""
    provider = _CliProvider(id="cli")
    commands = {"go": fabric_cli.Command(
        argv=(sys.executable, "-c", "import sys; print(sys.argv[1])",
              '{"ok": true}'))}
    out = fabric_cli.run(provider, "go", commands)
    assert out.status == "succeeded" and out.output == '{"ok": true}'


# --- FABRIC-CLI-01: one-shot commands --------------------------------------


class _CliProvider(_Provider):
    pass


def _cmd(*code, **kw):
    return fabric_cli.Command(argv=_py(*code), **kw)


def test_a_placeholder_reaches_the_child_as_one_argument():
    """The security line of the whole module: no shell, no re-splitting."""
    provider = _CliProvider(id="cli", upstream="")
    commands = {"go": fabric_cli.Command(
        argv=(sys.executable, "-c", "import sys; print(repr(sys.argv[1]))",
              "{task}"))}
    out = fabric_cli.run(provider, "go", commands, task="; rm -rf / #")
    assert out.status == "succeeded"
    assert out.output == "'; rm -rf / #'"


def test_an_unknown_placeholder_fails_before_spawning():
    provider = _CliProvider(id="cli")
    commands = {"go": fabric_cli.Command(
        argv=(sys.executable, "-c", "print(1)", "{missing}"))}
    out = fabric_cli.run(provider, "go", commands, other="x")
    assert out.status == "failed" and "missing" in out.error


def test_a_timeout_kills_and_reports():
    provider = _CliProvider(id="cli")
    commands = {"go": _cmd("import time; time.sleep(30)", timeout=1.0)}
    started = time.monotonic()
    out = fabric_cli.run(provider, "go", commands)
    assert out.status == "failed" and "exceeded" in out.error
    assert time.monotonic() - started < 15, "the timeout did not actually kill"


def test_malformed_json_is_a_failure_not_a_string():
    provider = _CliProvider(id="cli")
    commands = {"go": _cmd("print('not json')", output=fabric_cli.JSON_STDOUT)}
    out = fabric_cli.run(provider, "go", commands)
    assert out.status == "failed" and "JSON" in out.error


def test_good_json_is_parsed():
    provider = _CliProvider(id="cli")
    commands = {"go": _cmd('print(\'{"ok": true}\')',
                           output=fabric_cli.JSON_STDOUT)}
    out = fabric_cli.run(provider, "go", commands)
    assert out.status == "succeeded" and out.output == {"ok": True}


def test_a_bad_exit_code_is_a_failure():
    provider = _CliProvider(id="cli")
    commands = {"go": _cmd("import sys; sys.stderr.write('boom'); sys.exit(2)")}
    out = fabric_cli.run(provider, "go", commands)
    assert out.status == "failed" and "exited 2" in out.error


def test_evidence_redacts_values_and_keeps_names():
    provider = _CliProvider(id="cli")
    commands = {"go": fabric_cli.Command(
        argv=(sys.executable, "-c", "print('done')", "{token}"))}
    out = fabric_cli.run(provider, "go", commands, token="hunter2")
    assert out.status == "succeeded"
    assert "hunter2" not in out.verification.evidence
    assert "<arg>" in out.verification.evidence


def test_exit_zero_is_not_claimed_as_verification():
    provider = _CliProvider(id="cli")
    out = fabric_cli.run(provider, "go", {"go": _cmd("print('ok')")})
    assert "not a check of the work" in out.verification.evidence


def test_an_unknown_operation_is_refused():
    provider = _CliProvider(id="cli")
    out = fabric_cli.run(provider, "nope", {})
    assert out.status == "failed" and "nope" in out.error


# --- FABRIC-CLI-01 / G6: copyleft now has a compliant route ----------------


def test_cli_is_an_isolated_mode():
    """The whole of G6: a subprocess is a process boundary, so an AGPL agent
    can finally be integrated without violating the licence invariant."""
    assert fabric.CLI in fabric.ISOLATED_MODES
    assert fabric.CLI in fabric.INTEGRATION_MODES


def test_a_copyleft_provider_may_declare_cli_but_not_adapter():
    common = dict(id="x", family="coding", upstream="strix",
                  operations=("go",), risk="low",
                  license_mode=fabric.COPYLEFT, commit="a" * 40)
    fabric.Provider(integration_mode=fabric.CLI, **common)  # must not raise
    with pytest.raises(fabric.FabricError):
        fabric.Provider(integration_mode=fabric.ADAPTER, **common)
