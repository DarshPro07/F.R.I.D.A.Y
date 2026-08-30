"""
Routing from evidence, and refusing to when there isn't any.

The failure guarded here is a router that looks informed. Swinging on one
lucky run is worse than a fixed default, because a fixed default is at least
honest about being one.
"""
import pytest
from friday import evaluation as E
from friday import executor_router as R


@pytest.fixture
def record():
    return E.Record()


def _attempts(record, agent, passed, failed, task="build", seconds=10.0):
    for _ in range(passed):
        record.add(E.Attempt(task=task, agent=agent, verdict=E.PASSED,
                             seconds=seconds, exit_code=0))
    for _ in range(failed):
        record.add(E.Attempt(task=task, agent=agent, verdict=E.FAILED,
                             seconds=seconds, exit_code=1))


@pytest.fixture
def two_available(monkeypatch):
    """Pretend a second executor is installed and buildable."""
    both = (
        R.Executor(id="claude", binary="claude", title="Claude Code",
                   buildable=True),
        R.Executor(id="opencode", binary="opencode", title="OpenCode",
                   buildable=True),
    )
    monkeypatch.setattr(R, "KNOWN", both)
    monkeypatch.setattr(R, "BY_ID", {e.id: e for e in both})
    monkeypatch.setattr(R.shutil, "which", lambda name: f"/usr/bin/{name}")
    return both


@pytest.fixture
def nothing_installed(monkeypatch):
    """
    Nothing findable, by either route.

    Patching `shutil.which` alone is not enough: `claude` locates itself
    through `cli.claude_path`, because it installs to `~/.local/bin` which is
    not on the PATH this process inherits. A test that patched only `which`
    would still find it and would be testing the wrong thing.
    """
    monkeypatch.setattr(R.shutil, 'which', lambda name: None)
    monkeypatch.setattr('friday.executors.cli.claude_path', lambda: None)


def test_discovery_is_a_runtime_check_not_a_config_file(nothing_installed):
    """
    A config file claiming opencode is available on a machine where it is not
    produces a confusing failure ten minutes into a run.
    """
    assert R.installed() == ()


def test_an_installed_executor_is_discovered(monkeypatch):
    monkeypatch.setattr(R.shutil, "which",
                        lambda name: "/usr/bin/x" if name == "claude" else None)
    assert "claude" in R.installed()


def test_installed_is_not_the_same_as_usable(monkeypatch):
    """
    An executor can be installed and still have no builder. Choosing it and
    failing at launch is worse than saying so.
    """
    monkeypatch.setattr(R.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert "opencode" in R.installed()
    assert "opencode" not in R.usable()


def test_discovery_separates_missing_from_unsupported(monkeypatch):
    monkeypatch.setattr(R.shutil, "which",
                        lambda name: "/usr/bin/x" if name in ("claude", "opencode")
                        else None)
    found = R.discover()
    assert found["usable"] == ["claude"]
    assert found["installed_without_a_builder"] == ["opencode"]
    assert "codex" in found["not_installed"]


def test_one_executor_needs_no_deliberation(monkeypatch):
    monkeypatch.setattr(R.shutil, "which",
                        lambda name: "/usr/bin/x" if name == "claude" else None)
    choice = R.choose("build")
    assert choice.executor == "claude"
    assert "only executor" in choice.because
    assert not choice.from_evidence


def test_no_usable_executor_says_so_rather_than_guessing(nothing_installed):
    choice = R.choose('build')
    assert choice.executor == ''
    assert 'install one of' in choice.because


def test_without_a_record_the_default_is_used_and_labelled(two_available):
    choice = R.choose("build")
    assert choice.executor == R.DEFAULT
    assert not choice.from_evidence
    assert "not enough evidence" in choice.because


def test_a_fallback_is_distinguishable_from_a_measurement(two_available, record):
    """
    Somebody will ask why one agent keeps getting the work. The answer has to
    say whether that was measured or merely defaulted.
    """
    _attempts(record, "claude", passed=1, failed=0)
    choice = R.choose("build", record=record)
    assert not choice.from_evidence, "one attempt is not evidence"


def test_enough_evidence_decides(two_available, record):
    _attempts(record, "claude", passed=0, failed=3)
    _attempts(record, "opencode", passed=3, failed=0)

    choice = R.choose("build", record=record)
    assert choice.executor == "opencode"
    assert choice.from_evidence
    assert "measured best" in choice.because


def test_the_reason_carries_the_number(two_available, record):
    _attempts(record, "claude", passed=0, failed=3)
    _attempts(record, "opencode", passed=3, failed=0)
    assert "100%" in R.choose("build", record=record).because


def test_evidence_for_another_task_does_not_decide_this_one(two_available, record):
    _attempts(record, "opencode", passed=3, failed=0, task="refactor")
    choice = R.choose("build", record=record)
    assert not choice.from_evidence


def test_what_was_considered_is_recorded(two_available, record):
    _attempts(record, "claude", passed=3, failed=0)
    _attempts(record, "opencode", passed=0, failed=3)
    choice = R.choose("build", record=record)
    assert set(choice.considered) == {"claude", "opencode"}
    assert choice.alternatives == ("opencode",)


def test_the_choice_serialises_for_the_run_record(two_available, record):
    choice = R.choose("build", record=record)
    as_dict = choice.as_dict()
    assert as_dict["executor"] == R.DEFAULT
    assert as_dict["from_evidence"] is False


def test_a_named_executor_is_used_when_usable(two_available):
    choice = R.choose("build", prefer="opencode")
    assert choice.executor == "opencode"
    assert choice.because == "asked for by name"


def test_a_named_executor_that_is_not_usable_is_refused_with_the_reason(monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda name: f"/usr/bin/{name}")
    choice = R.choose("build", prefer="opencode")
    assert choice.executor == ""
    assert "not usable here" in choice.because


def test_an_unknown_name_is_refused(monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert R.choose("build", prefer="nonesuch").executor == ""


def test_building_something_unknown_raises():
    with pytest.raises(LookupError):
        R.build("nonesuch", store=None)


def test_building_something_with_no_builder_raises_and_explains(monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(NotImplementedError) as caught:
        R.build("opencode", store=None)
    assert "no builder" in str(caught.value)


def test_building_something_not_installed_raises(nothing_installed):
    with pytest.raises(FileNotFoundError):
        R.build('claude', store=None)


def test_it_never_substitutes_a_different_executor(monkeypatch):
    """
    A caller that asked for one agent and silently got another cannot
    interpret its own results, and the record is worthless if it can.
    """
    monkeypatch.setattr(R.shutil, "which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(NotImplementedError):
        R.build("opencode", store=None)      # must not quietly build claude


def test_every_declared_executor_says_why_it_is_or_is_not_buildable():
    for executor in R.KNOWN:
        if not executor.buildable:
            assert executor.notes, f"{executor.id} is unsupported for no stated reason"


def test_the_default_is_one_of_the_known():
    assert R.DEFAULT in R.BY_ID
