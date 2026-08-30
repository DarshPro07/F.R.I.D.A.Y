"""
Handing work to Claude Code without handing over control.

Two things are load-bearing here and everything else supports them:

  * a question broker decides what is TRUE, a permission broker decides what is
    ALLOWED, and neither may answer the other's question. That is how a
    preference turns into an authorisation.
  * "Claude says it's done" is not a result. finish() wants a diff.

The CLI facts these tests encode were probed against the installed 2.1.233,
not read from the docs - see friday/executors/cli.py.
"""
from __future__ import annotations
import asyncio
import json
import pytest
from friday.executors import brokers as B
from friday.executors import cli
from friday.executors.claude_code import ClaudeCodeExecutor, TaskBundle
from friday.store import FACT, INFERENCE, PATTERN, PREFERENCE, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "exec.sqlite3")
    yield s
    s.close()


@pytest.fixture
def bundle(tmp_path):
    return TaskBundle(goal="add a retry to the uploader",
                      workspace=str(tmp_path), project="halo",
                      acceptance=("tests pass",))


def test_bypass_permissions_is_not_a_choice():
    """
    Not a fallback, not behind a flag, not when a task is stuck. The policy
    engine exists so that this is never a decision anyone gets to make.
    """
    launch = cli.Launch(prompt="x", cwd=".", permission_mode="bypassPermissions")
    with pytest.raises(ValueError, match="never allowed"):
        launch.argv()


def test_the_prompt_is_never_on_the_command_line(monkeypatch):
    """
    --allowedTools and --disallowedTools are variadic, so a trailing prompt is
    swallowed as another tool name. The observed failure was no output at all -
    not an error, just silence.
    """
    monkeypatch.setattr(cli, "claude_path", lambda: "claude")
    argv = cli.Launch(prompt="do the thing", cwd=".",
                      allowed_tools=("Read",), disallowed_tools=("Write",)).argv()
    assert "do the thing" not in argv


def test_variadic_flags_come_last(monkeypatch):
    monkeypatch.setattr(cli, "claude_path", lambda: "claude")
    argv = cli.Launch(prompt="x", cwd=".", allowed_tools=("Read", "Grep"),
                      model="opus").argv()
    assert argv.index("--allowedTools") > argv.index("--model")


def test_hooks_are_off_by_default(monkeypatch):
    """
    The user's global config loads plugin hooks that fail on this machine, and
    one hangs long enough to blow a 300s timeout.
    """
    monkeypatch.setattr(cli, "claude_path", lambda: "claude")
    argv = cli.Launch(prompt="x", cwd=".").argv()
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["disableAllHooks"] is True


def test_the_default_permission_mode_is_the_installed_one(monkeypatch):
    """
    `manual` is the name 2.1.233 exposes; passing it makes init report
    `permissionMode: default`. Same mode, the CLI's own spelling - so the flag
    has to carry the spelling the binary accepts.
    """
    monkeypatch.setattr(cli, "claude_path", lambda: "claude")
    argv = cli.Launch(prompt="x", cwd=".").argv()
    assert argv[argv.index("--permission-mode") + 1] == "manual"


def test_resuming_replaces_a_fresh_session_id(monkeypatch):
    monkeypatch.setattr(cli, "claude_path", lambda: "claude")
    argv = cli.Launch(prompt="x", cwd=".", session_id="abc", resume="sess-1").argv()
    assert "--resume" in argv and "--session-id" not in argv


def test_mcp_config_is_strict_by_default(monkeypatch):
    """Only ADA's server. A development run does not inherit the user's."""
    monkeypatch.setattr(cli, "claude_path", lambda: "claude")
    argv = cli.Launch(prompt="x", cwd=".", mcp_config="ada.json").argv()
    assert "--strict-mcp-config" in argv


def test_an_assistant_message_yields_one_event_per_block():
    line = json.dumps({"type": "assistant", "session_id": "s", "message": {"content": [
        {"type": "thinking", "thinking": "..."},
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "text", "text": "reading it now"},
    ]}})
    kinds = [e.kind for e in cli.parse(line)]
    assert kinds == ["thinking", "tool", "text"]


def test_the_session_id_is_read_from_init():
    line = json.dumps({"type": "system", "subtype": "init", "session_id": "sess-9",
                       "tools": ["Read"], "permissionMode": "default"})
    event = cli.parse(line)[0]
    assert event.kind == "init" and event.session_id == "sess-9"


def test_a_junk_line_is_skipped_not_fatal():
    assert cli.parse("not json") == []
    assert cli.parse("[1,2,3]") == []


def test_exploring_cannot_change_anything():
    assert "Write" not in B.EXPLORE.allowed
    assert "Edit" not in B.EXPLORE.allowed
    assert "Read" in B.EXPLORE.allowed


def test_an_unrecognised_task_gets_the_smallest_profile():
    """Guessing wide produces a change nobody authorised."""
    assert B.profile_for("tell me how the uploader works") is B.EXPLORE
    assert B.profile_for("") is B.EXPLORE


def test_a_building_task_may_write():
    assert B.profile_for("implement retry in the uploader") is B.BUILD
    assert "Write" in B.BUILD.allowed


def test_no_profile_opens_up_shell_access_wholesale():
    """
    A broad rule wins over a specific one, so `Bash(*)` plus narrow allows is
    not the safe-looking thing it reads as. Only explicit prefixes.
    """
    for profile in B.PROFILES.values():
        assert "Bash" not in profile.allowed
        assert "Bash(*)" not in profile.allowed


@pytest.mark.parametrize("command", B.OUT_OF_SCOPE)
def test_the_dangerous_commands_are_in_no_profile(command):
    for profile in B.PROFILES.values():
        assert not any(command in rule for rule in profile.allowed), \
            f"{command} is reachable from {profile.name}"


def test_what_the_cli_refused_is_read_back():
    result = {"permission_denials": [
        {"tool_name": "Write", "tool_use_id": "t1", "tool_input": {"file_path": "x"}}]}
    denials = B.denials_from(result)
    assert denials[0]["tool"] == "Write"


def broker(store, **kwargs):
    return B.QuestionBroker(store=store, project="halo", **kwargs)


def test_an_accepted_decision_answers_without_asking(store):
    store.ensure_project("halo")
    store.record_decision("halo", decision="Postgres", source="he decided",
                          rationale="we need concurrent writers for the store")
    answer = broker(store).answer("should the store be SQLite or Postgres?")
    assert answer.text == "Postgres"
    assert answer.source == "decision"
    assert answer.grounded


def test_a_stated_fact_can_answer(store):
    store.remember("deploy.target", "Windows", kind=FACT,
                   source="he said it runs on Windows")
    answer = broker(store).answer("what deploy target should this build for?")
    assert answer.source == "fact"
    assert answer.text == "Windows"


def test_an_inference_never_decides(store):
    """
    The user model guessing at architecture is the exact failure the
    kind/authority split was built to stop.
    """
    store.remember("deploy.target", "Linux", kind=INFERENCE, confidence=0.9,
                   source="mentioned Homebrew once")
    answer = broker(store).answer("what deploy target should this build for?")
    assert answer.source == "unknown"
    assert not answer.grounded


def test_a_preference_is_context_not_a_decision(store):
    store.remember("style.tests", "pytest, no fixtures", kind=PREFERENCE,
                   source="said so twice")
    b = broker(store)
    assert b.answer("which test framework should this use?").source == "unknown"
    assert any("pytest" in line for line in b.context("which test framework"))


def test_a_pattern_is_context_too(store):
    store.remember("habit.commits", "small commits", kind=PATTERN,
                   source="observed")
    assert any("small commits" in line
               for line in broker(store).context("how should commits be made"))


def test_two_facts_that_both_fit_are_not_chosen_between(store):
    """That is what the contradiction machinery is for. Not this."""
    store.remember("db.primary", "Postgres", kind=FACT, source="he said")
    store.remember("db.cache", "Redis", kind=FACT, source="he said")
    answer = broker(store).answer("which db should we use, postgres or redis?")
    assert answer.source == "unknown"


def test_the_user_is_asked_when_nothing_settles_it(store):
    asked = []

    def ask(question, options):
        asked.append((question, tuple(options)))
        return "SQLite"

    answer = broker(store, ask_user=ask).answer(
        "sqlite or postgres?", ["SQLite", "Postgres"])
    assert answer.text == "SQLite" and answer.source == "user"
    assert asked == [("sqlite or postgres?", ("SQLite", "Postgres"))]


def test_the_same_question_is_never_asked_twice(store):
    calls = []

    def ask(question, options):
        calls.append(question)
        return "SQLite"

    b = broker(store, ask_user=ask)
    b.answer("sqlite or postgres?")
    b.answer("SQLite or Postgres?")   # same question, different shouting
    assert len(calls) == 1


def test_his_answer_survives_the_run(store):
    """A new broker, as if ADA had restarted, must not ask again."""
    def ask(question, options):
        return "Postgres"

    broker(store, ask_user=ask).answer("should the store be sqlite or postgres?")

    def must_not_ask(question, options):
        raise AssertionError("asked a second time after it was persisted")

    again = broker(store, ask_user=must_not_ask).answer(
        "should the store be sqlite or postgres?")
    assert again.text == "Postgres"
    assert again.source == "decision"


def test_an_unreachable_user_leaves_it_unanswered(store):
    """Better an admitted gap than a quiet guess."""
    answer = broker(store, ask_user=lambda q, o: None).answer("sqlite or postgres?")
    assert answer.source == "unknown" and answer.text == ""


def test_a_question_with_no_real_words_is_not_answered_from_noise(store):
    store.record_decision("halo", decision="Postgres", source="he decided",
                          rationale="concurrency")
    assert broker(store).answer("what should we do about the thing?").source != "decision"


def executor(store):
    return ClaudeCodeExecutor(store)


def started_for(bundle):
    from friday import contracts as c

    return c.started(c.Run.create(bundle.goal, capability="development").run_id,
                     "executor.claude_code")


def test_claude_saying_done_is_not_a_result(store, bundle, monkeypatch):
    """No diff, no completion - whatever the summary says."""
    from friday import contracts as c

    monkeypatch.setattr(ClaudeCodeExecutor, "changed_files",
                        staticmethod(lambda workspace: []))
    result = executor(store).finish(
        bundle, started_for(bundle),
        {"result": "All done! Added the retry and everything passes.",
         "is_error": False})
    assert result.status == c.PARTIAL
    assert "nothing changed" in (result.error or "")


def test_a_real_change_is_verified_by_git_not_by_claude(store, bundle, monkeypatch):
    from friday import contracts as c

    monkeypatch.setattr(ClaudeCodeExecutor, "changed_files",
                        staticmethod(lambda workspace: ["friday/uploader.py"]))
    result = executor(store).finish(
        bundle, started_for(bundle), {"result": "done", "is_error": False})
    assert result.status == c.SUCCEEDED
    assert result.verification.method == "worktree_diff"
    assert "uploader.py" in result.verification.evidence


def test_a_refused_action_makes_the_run_partial(store, bundle, monkeypatch):
    from friday import contracts as c

    monkeypatch.setattr(ClaudeCodeExecutor, "changed_files",
                        staticmethod(lambda workspace: ["a.py"]))
    result = executor(store).finish(
        bundle, started_for(bundle),
        {"result": "mostly done", "is_error": False,
         "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "git push"}}]})
    assert result.status == c.PARTIAL
    assert "refused by policy" in (result.error or "")


def test_no_result_event_is_a_failure_not_a_success(store, bundle):
    from friday import contracts as c

    assert executor(store).finish(bundle, started_for(bundle), {}).status == c.FAILED


def test_an_errored_run_is_a_failure(store, bundle):
    from friday import contracts as c

    result = executor(store).finish(
        bundle, started_for(bundle), {"is_error": True, "result": "rate limited"})
    assert result.status == c.FAILED


def test_a_missing_cli_fails_before_anything_runs(store, bundle, monkeypatch):
    from friday import contracts as c

    monkeypatch.setattr(cli, "available", lambda: False)
    result = asyncio.run(executor(store).execute(bundle))
    assert result.status == c.FAILED
    assert "nothing was run" in (result.error or "")


def test_ada_owns_the_run_to_session_mapping(store, bundle):
    ex = executor(store)
    ex._remember_session(bundle, "sess-123")
    assert ex.session_for(bundle.run_id) == "sess-123"


def test_resuming_an_unknown_run_says_so(store, bundle):
    with pytest.raises(LookupError):
        asyncio.run(executor(store).resume("DEV-nope", bundle))


def test_the_session_id_is_a_uuid_derived_from_the_run(bundle):
    """
    --session-id needs a uuid. Deriving it means the same run always claims
    the same session, so an interrupted run is resumable even if the mapping
    never got written.
    """
    import uuid

    value = bundle.run_id_as_uuid()
    assert uuid.UUID(value)
    assert value == bundle.run_id_as_uuid()


def test_the_prompt_carries_the_acceptance_criteria(bundle):
    assert "tests pass" in bundle.prompt()


def test_the_prompt_names_the_question_channel_when_there_is_one(bundle):
    text = bundle.prompt(ask_tool="mcp__ada__ada_ask")
    assert "mcp__ada__ada_ask" in text
    assert "prose is not a" in text


def test_the_prompt_does_not_invite_a_permission_question(bundle):
    lowered = bundle.prompt().lower()
    assert "do not ask for approval" in lowered


def test_a_workspace_without_a_repository_is_refused(tmp_path):
    from friday.executors.claude_code import workspace_is_sane

    ok, why = workspace_is_sane(str(tmp_path))
    assert not ok and "git repository" in why
SHELLS = ('Bash', 'PowerShell', 'Shell', 'Cmd', 'Terminal')


def test_no_profile_grants_an_unqualified_shell():
    """
    `PowerShell` sat in the build profile for as long as it existed, three
    lines under a comment explaining why a bare `Bash(*)` is unsafe. It was
    not a wildcard in the syntax the CLI checks, which is exactly why nothing
    caught it - but on Windows it reaches `Remove-Item -Recurse -Force` and
    `Start-Process -Verb RunAs`, so the enumerated Bash list and both NEVER
    entries were being handed back through the shell next to them.
    """
    from friday.executors import brokers as B
    for name, profile in B.PROFILES.items():
        bare = [tool for tool in profile.allowed if tool in SHELLS]
        assert not bare, f"profile {name!r} grants {bare} with no command specifier, which allows every command that tool can run"


def test_a_refused_command_is_refused_in_both_shells():
    """
    A denial written for one shell is not a denial. `rm -rf` and
    `Remove-Item -Recurse` are the same instruction to the same disk.
    """
    from friday.executors import brokers as B
    disallowed = ' '.join(B.NEVER)
    assert 'Bash(' in disallowed and 'PowerShell(' in disallowed, f"NEVER covers only one shell: {B.NEVER}"


def test_every_build_command_is_granted_to_both_shells():
    from friday.executors import brokers as B
    for command in B.BUILD_COMMANDS:
        for shell in ('Bash', 'PowerShell'):
            assert f"{shell}({command})" in B.BUILD.allowed, f"{command!r} is not runnable through {shell}"


def test_the_launched_argv_carries_no_bypass_and_no_bare_shell():
    """
    The audit run against the real command line rather than the source. What
    is written in a dataclass and what reaches the process are different
    facts, and only the second one is enforced.
    """
    from friday.executors import brokers as B
    from friday.executors import cli
    if cli.claude_path() is None:
        pytest.skip('the CLI is not installed on this machine')
    argv = cli.Launch(prompt='x', cwd='.', **B.BUILD.as_launch_kwargs()).argv()
    assert 'bypassPermissions' not in argv
    assert '--dangerously-skip-permissions' not in argv
    assert argv[argv.index('--permission-mode') + 1] == 'manual'
    granted = argv[argv.index('--allowedTools') + 1:]
    if '--disallowedTools' in granted:
        granted = granted[:granted.index('--disallowedTools')]
    assert not [tool for tool in granted if tool in SHELLS], f"an unqualified shell reached the command line: {granted}"
