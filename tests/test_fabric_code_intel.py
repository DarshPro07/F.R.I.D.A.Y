"""
The code-intelligence providers, as adapters rather than as upstreams.

These assert Friday's side of the contract - descriptor shape, licence mode,
argument translation, error handling - which holds whether or not the binary is
installed on this machine. The tests that need the real binary are marked
`live` and skip cleanly, so CI on a fresh checkout is honest rather than green
by omission.
"""

from __future__ import annotations

import pytest

from friday import contracts as c
from friday import fabric
from friday.fabric_adapters import codebase_memory as cbm
from friday.fabric_adapters import graft

binary_present = pytest.mark.skipif(
    not cbm.BINARY.exists(),
    reason=f"codebase-memory-mcp binary not downloaded at {cbm.BINARY}")


@pytest.fixture(autouse=True)
def clean():
    fabric.reload()
    yield
    fabric.reload()


# --- descriptor ------------------------------------------------------------


def test_it_is_registered_in_the_code_intelligence_family():
    assert fabric.get("codebase_memory").family == "code_intelligence"
    assert "code_intelligence" in fabric.families()


def test_it_is_pinned_to_the_audited_commit():
    """The lock file and the descriptor must not drift apart."""
    import json
    import pathlib

    lock = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "third_party" / "UPSTREAM_LOCK.json").read_text(encoding="utf-8"))
    assert fabric.get("codebase_memory").commit == lock["codebase-memory-mcp"]["commit"]


def test_it_runs_out_of_process():
    """
    MIT permits importing, but this is a native binary with no Python API, so
    the boundary is a subprocess either way. Declaring MCP rather than ADAPTER
    keeps `imported` honest for anyone auditing what links into Friday.
    """
    provider = fabric.get("codebase_memory")
    assert provider.integration_mode == fabric.MCP
    assert provider.imported is False


def test_it_declares_owning_a_process_so_the_singleton_check_sees_it():
    assert fabric.get("codebase_memory").owns_process is True
    assert cbm.PROCESS_MARKER


def test_every_declared_operation_maps_to_an_upstream_tool():
    provider = fabric.get("codebase_memory")
    assert set(provider.operations) == set(cbm.OPERATIONS)


def test_it_needs_no_secret_and_no_model():
    provider = fabric.get("codebase_memory")
    assert provider.secrets == ()
    assert provider.model_required is False


# --- argument translation --------------------------------------------------


def test_unknown_arguments_are_dropped_rather_than_passed_through(monkeypatch):
    """
    A stray keyword must not become an unknown flag on a subprocess we do not
    control. The operation table is the allowlist.
    """
    seen = {}

    class Result:
        returncode = 0
        stdout = '{"content":[{"type":"text","text":"ok"}],"isError":false}'
        stderr = ""

    def fake_run(args, timeout=0):
        seen["args"] = args
        return Result()

    monkeypatch.setattr(cbm, "_run", fake_run)
    cbm.call("search", None, project="p", name_pattern="*x*",
             shell="rm -rf /", limit=5)
    assert "--shell" not in seen["args"]
    assert "rm -rf /" not in seen["args"]
    assert "--project" in seen["args"] and "--name-pattern" in seen["args"]


def test_empty_arguments_are_omitted_not_sent_as_empty_flags(monkeypatch):
    seen = {}

    class Result:
        returncode = 0
        stdout = '{"content":[{"type":"text","text":"ok"}],"isError":false}'
        stderr = ""

    monkeypatch.setattr(cbm, "_run",
                        lambda args, timeout=0: (seen.update(args=args), Result())[1])
    cbm.call("search", None, project="p", name_pattern="", limit=None)
    assert "--name-pattern" not in seen["args"]
    assert "--limit" not in seen["args"]


def test_an_upstream_error_object_becomes_an_exception_not_a_success(monkeypatch):
    """
    The binary reports failure inside a 200-ish envelope with isError. Reading
    only the exit code would turn 'repo_path is required' into a success.
    """
    class Result:
        returncode = 0
        stdout = ('{"content":[{"type":"text","text":"repo_path is required"}],'
                  '"structuredContent":{"error":"repo_path is required"},'
                  '"isError":true}')
        stderr = ""

    monkeypatch.setattr(cbm, "_run", lambda args, timeout=0: Result())
    with pytest.raises(RuntimeError, match="repo_path is required"):
        cbm.call("projects", None)


def test_log_lines_before_the_payload_do_not_break_parsing(monkeypatch):
    """The binary interleaves level= logs with its JSON on the same stream."""
    class Result:
        returncode = 0
        stdout = ("level=info msg=mem.init budget_mb=4037\n"
                  "hint: this command started a temporary CBM daemon.\n"
                  '{"content":[{"type":"text","text":"total: 1"}],'
                  '"structuredContent":{"total":1},"isError":false}\n')
        stderr = ""

    monkeypatch.setattr(cbm, "_run", lambda args, timeout=0: Result())
    assert cbm.call("projects", None) == {"total": 1}


def test_a_nonzero_exit_names_the_tool_and_keeps_the_stderr(monkeypatch):
    class Result:
        returncode = 3
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(cbm, "_run", lambda args, timeout=0: Result())
    with pytest.raises(RuntimeError, match="search_graph exited 3"):
        cbm.call("search", None, project="p")


# --- health honesty --------------------------------------------------------


def test_a_missing_binary_is_unavailable_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(cbm, "BINARY", tmp_path / "nope.exe")
    assert cbm.health(None)["state"] == fabric.UNAVAILABLE


def test_no_warm_daemon_is_degraded_rather_than_down(monkeypatch):
    """
    The CLI still answers without a daemon, just slowly. Reporting that as
    UNAVAILABLE would route around a provider that can still do the work.
    """
    class Result:
        returncode = 1
        stdout = "daemon: inactive"
        stderr = ""

    monkeypatch.setattr(cbm, "_run", lambda args, timeout=0: Result())
    probe = cbm.health(None)
    assert probe["state"] == fabric.DEGRADED
    assert "14s" in probe["detail"]


# --- live ------------------------------------------------------------------


@pytest.mark.live
@binary_present
def test_a_real_query_finds_a_known_symbol_at_its_real_line():
    """
    Not "the tool returned" - the tool returned the right place. HermesSupervisor
    is a class this repository definitely has, and the answer must name the file.
    """
    fabric.activate("codebase_memory")
    result = fabric.call("codebase_memory", "search", run_id="OBJ-TEST",
                         project=cbm.PROJECT, name_pattern="HermesSupervisor",
                         limit=5)
    assert result.status == c.SUCCEEDED, result.error
    assert "hermes_bridge.py" in str(result.output)


@pytest.mark.live
@binary_present
def test_the_index_excludes_the_env_file():
    """
    NON_NEGOTIABLE 4. The indexer walks the whole tree; `.env` holds live API
    keys. It is excluded by gitignore, and this is the test that says so out
    loud rather than trusting it.
    """
    fabric.activate("codebase_memory")
    result = fabric.call("codebase_memory", "search", run_id="OBJ-TEST",
                         project=cbm.PROJECT, name_pattern="*API_KEY*", limit=20)
    assert result.status == c.SUCCEEDED, result.error
    text = str(result.output)
    for secret_name in ("LIVEKIT_API_SECRET", "GROQ_API_KEY", "OPENAI_API_KEY"):
        assert secret_name not in text, f"{secret_name} reached the code graph"


# --- graft: the second code-intelligence provider --------------------------
#
# Graft answers the orientation question ("what is this repo, what would I
# break") where codebase_memory answers the exact one ("what calls this
# symbol"). Same family, so the router can pick either; these assert Friday's
# side of that contract without needing the CLI installed.


def test_graft_is_registered_in_the_same_family_as_the_exact_provider(monkeypatch):
    assert fabric.get("graft").family == "code_intelligence"
    assert {"codebase_memory", "graft"} <= {
        p.id for p in fabric.by_family("code_intelligence")}


def test_graft_is_pinned_to_the_audited_commit(monkeypatch):
    import json
    import pathlib

    lock = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "third_party" / "UPSTREAM_LOCK.json").read_text(encoding="utf-8"))
    assert fabric.get("graft").commit == lock["graft"]["commit"]


def test_graft_holds_no_daemon_so_the_singleton_check_leaves_it_alone(monkeypatch):
    """Unlike CBM there is no long-lived process: the graph is files."""
    assert fabric.get("graft").owns_process is False
    assert fabric.get("graft").imported is False


def test_graft_falls_back_to_the_exact_provider(monkeypatch):
    assert "codebase_memory" in fabric.get("graft").fallbacks


def test_every_graft_operation_maps_to_a_subcommand():
    assert set(fabric.get("graft").operations) == set(graft.OPERATIONS)


def test_graft_costs_no_model_and_no_secret(monkeypatch):
    """
    Tier-1 graft is deterministic tree-sitter. If this ever flips, the planner
    silently starts spending model budget on orientation questions.
    """
    provider = fabric.get("graft")
    assert provider.model_required is False
    assert provider.secrets == ()
    assert provider.cost_class == "free"


def _graft_run(monkeypatch, seen, result_class):
    """Swap graft._run for one that records its arguments, then restores."""
    def fake_run(arguments, timeout=0):
        seen["args"] = arguments
        return result_class()
    monkeypatch.setattr(graft, "_run", fake_run)


# --- graft: the two operations that must never be reachable ----------------


def test_graft_init_is_not_an_operation(monkeypatch):
    """
    `graft init` rewrites .claude/settings.json, AGENTS.md and .mcp.json.
    Friday does not edit the operator's agent configuration as a side effect
    of answering a code question.
    """
    assert "init" not in graft.OPERATIONS
    with pytest.raises(KeyError):
        graft.call("init", None)


def test_graft_deep_build_is_not_reachable_through_the_operation_table(monkeypatch):
    """
    `build --deep` runs an LLM pass over every file under a provider key.
    Spend is execution-economics' decision, so --deep must not be smuggled in
    as an argument to the free `build`.
    """
    seen = {}

    class Result:
        returncode = 0
        stdout = "built"
        stderr = ""

    _graft_run(monkeypatch, seen, Result)
    graft.call("build", None, deep=True, path=".")
    assert "--deep" not in seen["args"]


# --- graft: argument translation -------------------------------------------


def test_graft_drops_unknown_arguments_rather_than_passing_them_through(monkeypatch):
    seen = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    _graft_run(monkeypatch, seen, Result)
    graft.call("grep", None, query="PolicyEngine", shell="rm -rf /")
    assert "--shell" not in seen["args"]
    assert "rm -rf /" not in seen["args"]
    assert "PolicyEngine" in seen["args"]


def test_graft_passes_the_positional_argument_without_a_flag(monkeypatch):
    seen = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    _graft_run(monkeypatch, seen, Result)
    graft.call("callers", None, symbol="admit")
    assert seen["args"][:2] == ["callers", "admit"]
    assert "--symbol" not in seen["args"]


def test_graft_refuses_a_positional_operation_with_nothing_to_ask(monkeypatch):
    with pytest.raises(ValueError):
        graft.call("ask", None)


def test_graft_switches_are_bare_flags_not_key_value(monkeypatch):
    seen = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    _graft_run(monkeypatch, seen, Result)
    graft.call("map", None, no_refresh=True)
    assert "--no-refresh" in seen["args"]
    assert "True" not in seen["args"]


def test_graft_a_nonzero_exit_names_the_subcommand_and_keeps_stderr(monkeypatch):
    class Result:
        returncode = 2
        stdout = ""
        stderr = "boom: no graph"

    _graft_run(monkeypatch, {}, Result)
    with pytest.raises(RuntimeError, match="graft check"):
        graft.call("check", None)


def test_graft_non_json_output_is_returned_rather_than_raising(monkeypatch):
    """Most graft commands print prose. That is a result, not a parse error."""
    class Result:
        returncode = 0
        stdout = "src/friday/policy.py: 12 callers"
        stderr = ""

    _graft_run(monkeypatch, {}, Result)
    assert graft.call("map", None)["output"].startswith("src/friday/policy.py")


# --- graft: health honesty -------------------------------------------------


def test_graft_without_a_cli_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.setattr(graft, "_base_command", lambda: None)
    assert graft.health(None)["state"] == fabric.UNAVAILABLE


def test_graft_without_a_graph_is_degraded_rather_than_down(monkeypatch, tmp_path):
    """The CLI builds on demand, so 'no graph yet' is slow, not broken."""
    monkeypatch.setattr(graft, "_base_command", lambda: ["graft"])
    monkeypatch.setattr(graft, "ROOT", tmp_path)
    assert graft.health(None)["state"] == fabric.DEGRADED


def test_graft_telemetry_is_forced_off_in_every_subprocess(monkeypatch):
    """Opt-out upstream; Friday opts out on the operator's behalf."""
    assert graft._env()["DO_NOT_TRACK"] == "1"
