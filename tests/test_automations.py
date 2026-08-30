"""
The automation engine: the graph, the retries, and the boundary.

Nothing here registers a real scheduled task or touches the network. The
scheduler is proved live by scripts/golden_automations.py - `schtasks /Query`
is the only thing that can settle whether an automation is armed, and a mock
that says yes proves nothing at all.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from friday import contracts as c
from friday.policy import PolicyEngine
from friday.store import Store
from friday.toolsets import automations as A


@pytest.fixture(autouse=True)
def store(tmp_path):
    fresh = Store(tmp_path / "test.db")
    A.reset_store(fresh)
    yield fresh
    A.reset_store(None)
    fresh.close()


@pytest.fixture
def tools(monkeypatch):
    """
    Replace the allow-list with recording fakes.

    Keeps the same call shape as a real capability - (run, engine=..., **args)
    returning an ActionResult - so what is exercised is the engine, not the web.
    """
    calls: list[tuple[str, dict]] = []
    plan: dict[str, list] = {}

    def make(name):
        def fake(run, *, engine=None, **args):
            # Real toolsets gate themselves before doing anything. A fake that
            # skipped this would make the gating test assert nothing at all.
            if engine is not None and not engine.decide(name).allowed:
                return c.started(run.run_id, name).finish(
                    status=c.CANCELLED, error="needs approval")
            calls.append((name, args))
            outcomes = plan.get(name, ["ok"])
            outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
            result = c.started(run.run_id, name)
            if outcome == "ok":
                return c.succeeded(
                    result, output={"said": args, "n": len(calls)},
                    verification=c.Verification(method="fake", evidence=f"{name} ran"))
            if outcome == "raise":
                raise RuntimeError("the tool exploded")
            return c.failed(result, f"{name} refused")
        return fake

    fakes = {name: make(name) for name in ("web.search", "web.news", "web.fetch")}
    monkeypatch.setattr(A, "TOOLS", fakes)
    return type("Tools", (), {"calls": calls, "plan": plan})()


def run_for(label: str = "test") -> c.Run:
    return c.Run.create(label, capability="automation")


def define(name="morning", steps=None, trigger=None, description=""):
    """
    The default step names a *real* capability, so tests that do not take the
    `tools` fixture still define something valid. Tests that want the fakes
    pass their steps explicitly.
    """
    return A.automations_create(
        run_for(), name,
        json.dumps(trigger or {"kind": "manual"}),
        json.dumps(steps or [{"id": "a", "tool": "web.news", "args": {}}]),
        description=description,
    )


# ---------------------------------------------------------------------------
# The boundary. These are the tests that matter most.
# ---------------------------------------------------------------------------


def test_a_shell_step_is_refused():
    """
    The donor engine had one. It turns "run automation X" into an arbitrary
    command primitive the moment the model can choose X.
    """
    with pytest.raises(A.AutomationError, match="shell"):
        A.validate_steps([{"id": "a", "shell": "rm -rf /"}])


def test_a_command_step_is_refused_too():
    with pytest.raises(A.AutomationError, match="shell"):
        A.validate_steps([{"id": "a", "command": "format c:"}])


def test_a_step_may_not_name_a_capability_outside_the_allow_list():
    with pytest.raises(A.AutomationError, match="not an automatable"):
        A.validate_steps([{"id": "a", "tool": "files.delete", "args": {}}])


def test_the_allow_list_holds_no_capability_that_writes_or_executes():
    """
    A guard on the list itself. Adding `files.write` here would be a decision
    to let unattended code write to disk at 3am, and it should have to argue
    with a failing test first.
    """
    forbidden = ("delete", "write", "edit", "create", "execute", "command",
                 "close", "kill", "shutdown", "install")
    for tool_id in A.TOOLS:
        assert not any(word in tool_id for word in forbidden), \
            f"{tool_id} writes or executes; it must not be automatable by default"


def test_every_allow_listed_capability_is_real_and_callable():
    """A typo here would fail at 3am instead of now."""
    for tool_id, fn in A.TOOLS.items():
        assert callable(fn), tool_id
        assert "run" in __import__("inspect").signature(fn).parameters, tool_id


def test_each_step_is_still_gated_by_the_policy_engine_when_it_runs(tools):
    """
    The automation's own permission is not its steps' permission. A step that
    would be refused when asked for out loud is refused here too.
    """
    define(steps=[{"id": "a", "tool": "web.search", "args": {}}])

    class RefuseEverything(PolicyEngine):
        def decide(self, tool_id):
            return super().decide("system.shutdown")

    record = asyncio.run(A.execute("morning", engine=RefuseEverything()))
    assert record["status"] == c.FAILED
    assert record["steps"][0]["status"] == c.CANCELLED
    assert tools.calls == [], "the step ran despite being refused"


def test_the_database_path_does_not_depend_on_the_working_directory():
    """
    The bug this guards against cost a whole live gate run, and cost nothing
    to miss: a scheduled automation fired, ran its steps, and wrote its result
    to C:\\Windows\\System32\\data\\ada.sqlite3, because Task Scheduler starts
    a process there. Nothing raised. The run was simply not in the database
    anyone reads, so working automation was indistinguishable from one that
    never fired.
    """
    from friday.store import DEFAULT_DB

    assert Path(DEFAULT_DB).is_absolute(), \
        "a relative default DB means every detached process gets its own"


def test_no_module_reintroduces_the_relative_default():
    """Thirteen modules had their own copy of it. None may grow a fourteenth."""
    root = Path(__file__).resolve().parent.parent / "friday"
    offenders = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if 'ADA_DB", "data/' in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert not offenders, f"cwd-relative database default is back in: {offenders}"


def test_every_runtime_root_is_absolute():
    """
    The invariant, stated once: A BACKGROUND PROCESS MUST NOT DEPEND ON THE
    CALLER'S WORKING DIRECTORY.

    Two of these are security boundaries rather than conveniences. The
    filesystem jail's root decides what the agent may touch, and the companion
    token is the secret the browser extension authenticates with - both were
    relative, so both moved with the working directory.
    """
    from friday.companion.bridge import TOKEN_PATH
    from friday.companion.pairing import ID_PATH, KEY_PATH
    from friday.config import ARTIFACTS_DIR, DATA_DIR, LOGS_DIR, PROJECT_ROOT
    from friday.fsjail import DEFAULT_WORKSPACE
    from friday.store import DEFAULT_DB
    from friday.toolsets.vision import captures_dir

    roots = {
        "project_root": PROJECT_ROOT, "data_dir": DATA_DIR,
        "logs_dir": LOGS_DIR, "artifacts_dir": ARTIFACTS_DIR,
        "database": DEFAULT_DB, "jail_workspace": DEFAULT_WORKSPACE,
        "companion_token": TOKEN_PATH, "companion_key": KEY_PATH,
        "companion_id": ID_PATH, "vision_captures": captures_dir(),
    }
    relative = {name: str(path) for name, path in roots.items()
                if not Path(path).is_absolute()}
    assert not relative, f"these move with the working directory: {relative}"


def test_a_run_records_where_it_resolved_its_paths(tools, store):
    """
    Without this the same defect has to be re-derived from scratch. With it,
    a run whose cwd is System32 and whose database is under the project root
    is visibly the fixed version.
    """
    define(steps=[{"id": "a", "tool": "web.search", "args": {}}])
    asyncio.run(A.execute("morning"))

    runtime = store.automation_history("morning")[0]["runtime"]
    for key in ("cwd", "project_root", "data_dir", "database", "logs_dir",
                "task_name"):
        assert key in runtime, f"{key} was not recorded"
    assert Path(runtime["database"]).is_absolute()


def test_a_scheduled_task_declares_where_it_runs():
    xml = A._task_xml({"kind": "daily", "at": "08:00"}, "py.exe", "-m x", "d")
    assert "<WorkingDirectory>" in xml, \
        "without this the task starts in System32"


# ---------------------------------------------------------------------------
# Validation, all at save time
# ---------------------------------------------------------------------------


def test_a_cycle_is_reported_by_name():
    with pytest.raises(A.AutomationError, match="cycle"):
        A.validate_steps([
            {"id": "a", "tool": "web.search", "args": {"query": "x"}, "needs": ["b"]},
            {"id": "b", "tool": "web.search", "args": {"query": "y"}, "needs": ["a"]},
        ])


def test_a_step_that_needs_something_nonexistent_is_refused():
    with pytest.raises(A.AutomationError, match="does not exist"):
        A.validate_steps([{"id": "a", "tool": "web.search",
                           "args": {"query": "x"}, "needs": ["ghost"]}])


def test_a_misspelled_argument_fails_now_not_at_three_am():
    with pytest.raises(A.AutomationError, match="takes no argument"):
        A.validate_steps([{"id": "a", "tool": "web.search", "args": {"quiery": "x"}}])


def test_a_missing_required_argument_is_refused():
    with pytest.raises(A.AutomationError, match="needs query"):
        A.validate_steps([{"id": "a", "tool": "web.search", "args": {}}])


def test_duplicate_step_ids_are_refused():
    with pytest.raises(A.AutomationError, match="share the id"):
        A.validate_steps([{"id": "a", "tool": "web.news", "args": {}},
                          {"id": "a", "tool": "web.news", "args": {}}])


def test_retries_are_capped():
    with pytest.raises(A.AutomationError, match="retries"):
        A.validate_steps([{"id": "a", "tool": "web.news", "args": {},
                           "retries": 99}])


def test_steps_come_back_in_dependency_order():
    ordered = A.validate_steps([
        {"id": "c", "tool": "web.news", "args": {}, "needs": ["b"]},
        {"id": "a", "tool": "web.news", "args": {}},
        {"id": "b", "tool": "web.news", "args": {}, "needs": ["a"]},
    ])
    assert [s["id"] for s in ordered] == ["a", "b", "c"]


@pytest.mark.parametrize("trigger,message", [
    ({"kind": "daily", "at": "25:00"}, "24-hour"),
    ({"kind": "daily"}, "24-hour"),
    ({"kind": "interval", "minutes": 1}, "between 5"),
    ({"kind": "interval", "minutes": 9999}, "between 5"),
    ({"kind": "whenever"}, "unknown trigger"),
])
def test_bad_triggers_are_refused(trigger, message):
    with pytest.raises(A.AutomationError, match=message):
        A.validate_trigger(trigger)


def test_good_triggers_survive():
    assert A.validate_trigger({"kind": "daily", "at": "08:00"})["at"] == "08:00"
    assert A.validate_trigger({"kind": "interval", "minutes": 30})["minutes"] == 30
    assert A.validate_trigger({"kind": "manual"}) == {"kind": "manual"}


# ---------------------------------------------------------------------------
# The graph: what a flat list cannot do
# ---------------------------------------------------------------------------


def test_a_step_whose_dependency_failed_is_skipped_not_run(tools):
    tools.plan["web.search"] = ["fail"]
    define(steps=[
        {"id": "a", "tool": "web.search", "args": {}},
        {"id": "b", "tool": "web.news", "args": {}, "needs": ["a"]},
    ])
    record = asyncio.run(A.execute("morning"))

    by_id = {s["id"]: s for s in record["steps"]}
    assert by_id["a"]["status"] == c.FAILED
    assert by_id["b"]["status"] == "skipped"
    assert "depends on a" in by_id["b"]["error"]
    assert [name for name, _ in tools.calls] == ["web.search"], "b ran anyway"


def test_a_failure_does_not_stop_an_independent_branch(tools):
    """The other half of the graph's value: unrelated work still happens."""
    tools.plan["web.search"] = ["fail"]
    define(steps=[
        {"id": "a", "tool": "web.search", "args": {}},
        {"id": "b", "tool": "web.news", "args": {}, "needs": ["a"]},
        {"id": "c", "tool": "web.fetch", "args": {}},
    ])
    record = asyncio.run(A.execute("morning"))

    by_id = {s["id"]: s for s in record["steps"]}
    assert by_id["c"]["status"] == c.SUCCEEDED
    assert record["status"] == c.PARTIAL, "partial, because it was partial"


def test_a_step_reads_the_output_of_the_step_it_needs(tools):
    define(steps=[
        {"id": "a", "tool": "web.search", "args": {}},
        {"id": "b", "tool": "web.news", "args": {"topic": "{{steps.a.n}}"},
         "needs": ["a"]},
    ])
    asyncio.run(A.execute("morning"))
    assert dict(tools.calls)["web.news"] == {"topic": "1"}


def test_variables_supplied_at_run_time_reach_the_step(tools):
    define(steps=[{"id": "a", "tool": "web.search", "args": {"q": "{{vars.subject}}"}}])
    asyncio.run(A.execute("morning", variables={"subject": "arc reactors"}))
    assert tools.calls[0][1] == {"q": "arc reactors"}


def test_an_unresolved_placeholder_is_left_alone_never_blanked(tools):
    """
    Replacing it with "" would make the step search for nothing and report
    success. A visibly wrong argument is better than an invisibly empty one.
    """
    define(steps=[{"id": "a", "tool": "web.search", "args": {"q": "{{vars.missing}}"}}])
    asyncio.run(A.execute("morning"))
    assert tools.calls[0][1] == {"q": "{{vars.missing}}"}


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------


def test_a_step_retries_and_the_attempts_are_recorded(tools):
    tools.plan["web.search"] = ["fail", "fail", "ok"]
    define(steps=[{"id": "a", "tool": "web.search", "args": {}, "retries": 2}])
    record = asyncio.run(A.execute("morning"))

    assert record["steps"][0]["status"] == c.SUCCEEDED
    assert record["steps"][0]["attempts"] == 3


def test_retries_stop_at_the_declared_number(tools):
    tools.plan["web.search"] = ["fail", "fail", "fail", "ok"]
    define(steps=[{"id": "a", "tool": "web.search", "args": {}, "retries": 1}])
    record = asyncio.run(A.execute("morning"))

    assert record["steps"][0]["status"] == c.FAILED
    assert record["steps"][0]["attempts"] == 2


def test_a_step_with_no_retries_runs_exactly_once(tools):
    tools.plan["web.search"] = ["fail"]
    define(steps=[{"id": "a", "tool": "web.search", "args": {}}])
    assert asyncio.run(A.execute("morning"))["steps"][0]["attempts"] == 1


def test_a_tool_that_raises_is_a_failed_step_not_a_crashed_automation(tools):
    tools.plan["web.search"] = ["raise"]
    define(steps=[
        {"id": "a", "tool": "web.search", "args": {}},
        {"id": "b", "tool": "web.news", "args": {}},
    ])
    record = asyncio.run(A.execute("morning"))

    assert record["steps"][0]["status"] == c.FAILED
    assert "RuntimeError" in record["steps"][0]["error"]
    assert record["steps"][1]["status"] == c.SUCCEEDED, "b never got its turn"


# ---------------------------------------------------------------------------
# The record: answerable the morning after
# ---------------------------------------------------------------------------


def test_every_run_is_persisted_with_its_steps(tools, store):
    define(steps=[{"id": "a", "tool": "web.search", "args": {}}])
    asyncio.run(A.execute("morning"))

    history = store.automation_history("morning")
    assert len(history) == 1
    assert history[0]["status"] == c.SUCCEEDED
    assert history[0]["steps"][0]["id"] == "a"
    assert history[0]["finished_at"]


def test_a_failed_run_records_why(tools, store):
    tools.plan["web.search"] = ["fail"]
    define(steps=[{"id": "a", "tool": "web.search", "args": {}}])
    asyncio.run(A.execute("morning"))

    assert "web.search refused" in store.automation_history("morning")[0]["error"]


def test_history_survives_reopening_the_database(tools, tmp_path):
    path = tmp_path / "durable.db"
    A.reset_store(Store(path))
    define(steps=[{"id": "a", "tool": "web.search", "args": {}}])
    asyncio.run(A.execute("morning"))
    A.reset_store(None)

    assert len(Store(path).automation_history("morning")) == 1


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------


def test_a_manual_automation_registers_no_task_and_claims_none():
    result = define(trigger={"kind": "manual"})
    assert result.status == c.SUCCEEDED
    assert result.output["task_name"] is None
    assert "no scheduled task exists, and none is claimed" in result.verification.evidence


def test_creating_with_a_bad_name_fails():
    assert A.automations_create(
        run_for(), "Not A Name!", '{"kind":"manual"}',
        '[{"id":"a","tool":"web.news","args":{}}]').status == c.FAILED


def test_creating_with_unparseable_json_fails_rather_than_raising():
    assert A.automations_create(
        run_for(), "x", "{not json", "[]").status == c.FAILED


def test_listing_reports_what_the_os_thinks_not_what_we_wrote():
    define(name="quiet", trigger={"kind": "manual"})
    result = A.automations_list(run_for())
    assert result.status == c.SUCCEEDED
    row = result.output["automations"][0]
    assert row["armed"] is False and row["orphaned"] is False


def test_running_an_unknown_automation_fails_cleanly():
    result = asyncio.run(A.automations_run(run_for(), "nothing-like-this"))
    assert result.status == c.FAILED
    assert "no automation" in result.error


def test_a_partly_successful_run_is_reported_as_partial_not_succeeded(tools):
    tools.plan["web.news"] = ["fail"]
    define(steps=[{"id": "a", "tool": "web.search", "args": {}},
                  {"id": "b", "tool": "web.news", "args": {}}])
    result = asyncio.run(A.automations_run(run_for(), "morning"))
    assert result.status == c.PARTIAL
    assert not result.may_claim_completion


def test_deleting_something_that_does_not_exist_fails():
    assert A.automations_delete(run_for(), "ghost").status == c.FAILED


def test_delete_removes_it(store):
    define(name="temp")
    assert A.automations_delete(run_for(), "temp").status == c.SUCCEEDED
    assert store.get_automation("temp") is None


def test_creating_the_same_name_twice_replaces_rather_than_duplicates(store):
    define(name="morning", steps=[{"id": "a", "tool": "web.news", "args": {}}])
    define(name="morning", steps=[{"id": "b", "tool": "web.news", "args": {}}])
    assert len(store.automations()) == 1
    assert store.get_automation("morning")["steps"][0]["id"] == "b"
