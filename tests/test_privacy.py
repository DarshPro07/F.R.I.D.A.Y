"""
Privacy mode, and the promise it must not overstate.

The failure being guarded is not a leak. It is a *silent* leak: a mode that
says private and quietly reroutes to a cloud model when the local one cannot
answer, after the person has stopped watching.
"""
import pytest
from friday import privacy as PV


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(PV.ENV_VAR, raising=False)
    monkeypatch.delenv("LLM_BACKEND", raising=False)


class _Cap:
    def __init__(self, cap_id, scope):
        self.id = cap_id
        self.execution_scope = scope
WEB = _Cap('web_search', 'network')
LOCAL = _Cap('files_read', 'user_device')


def test_the_default_is_open():
    assert PV.mode() == PV.OPEN
    assert not PV.private()


def test_the_mode_is_read_at_call_time(monkeypatch):
    """The boss may turn it on mid-session; the next request must honour it."""
    assert not PV.private()
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    assert PV.private()


def test_an_unknown_mode_falls_back_to_open(monkeypatch):
    monkeypatch.setenv(PV.ENV_VAR, "SORT_OF_PRIVATE")
    assert PV.mode() == PV.OPEN


def test_open_mode_refuses_nothing():
    assert PV.refuses(WEB) == ""
    assert PV.refuses(LOCAL) == ""


def test_private_mode_refuses_a_network_capability(monkeypatch):
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    reason = PV.refuses(WEB)
    assert reason
    assert "Nothing was sent" in reason


def test_private_mode_allows_a_local_capability(monkeypatch):
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    assert PV.refuses(LOCAL) == ""


def test_the_guard_raises_with_the_reason(monkeypatch):
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    with pytest.raises(PV.Refused) as caught:
        PV.guard(WEB)
    assert caught.value.capability == "web_search"
    assert "PRIVATE_ONLY" in caught.value.reason


def test_the_guard_is_silent_when_allowed(monkeypatch):
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    PV.guard(LOCAL)          # must not raise


def test_a_capability_can_be_named_rather_than_passed(monkeypatch):
    """Most callers have the id, not the object."""
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    assert PV.refuses("web_search")
    assert PV.refuses("files_read") == ""


def test_an_unknown_capability_is_not_refused_by_guesswork(monkeypatch):
    """
    Refusing something the registry has never heard of would break every
    caller that passes a name this module cannot resolve.
    """
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    assert PV.refuses("some_capability_that_does_not_exist") == ""


def test_open_mode_allows_any_backend():
    assert PV.backend_allowed("google")
    assert PV.backend_allowed("anthropic")


def test_private_mode_allows_a_local_backend(monkeypatch):
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    assert PV.backend_allowed("ollama")


@pytest.mark.parametrize("backend", ["google", "anthropic", "openai"])
def test_private_mode_refuses_a_cloud_backend(monkeypatch, backend):
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    assert not PV.backend_allowed(backend)


def test_an_unknown_backend_fails_closed(monkeypatch):
    """
    A cloud provider added later and forgotten here must fail closed, not be
    allowed by omission.
    """
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    assert not PV.backend_allowed("some_new_cloud_provider")
    assert not PV.backend_allowed("")


def test_open_mode_does_not_claim_privacy():
    assert "off" in PV.guarantee("google").summary.lower()
    assert not PV.guarantee("google").complete


def test_private_mode_with_a_cloud_model_says_what_it_does_not_cover(monkeypatch):
    """
    The hole that matters. A mode saying "private" while the conversation
    goes to a cloud model has told the boss something untrue, and he would
    have no way to find out.
    """
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    promise = PV.guarantee("google")

    assert promise.data_stays_local
    assert not promise.thinking_stays_local
    assert not promise.complete
    assert "still leaves the machine" in promise.summary


def test_private_mode_with_a_local_model_is_complete(monkeypatch):
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    promise = PV.guarantee("ollama")

    assert promise.complete
    assert "Nothing leaves this machine" in promise.summary


def test_the_backend_is_read_from_the_environment_when_unstated(monkeypatch):
    monkeypatch.setenv(PV.ENV_VAR, "PRIVATE_ONLY")
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    assert PV.guarantee().thinking_stays_local


def test_open_mode_leaves_everything_available():
    available, total = PV.available_under(PV.OPEN)
    assert available == total


def test_private_mode_costs_only_the_network_capabilities():
    """
    "114 of 127 still work" is a much better thing to say than leaving the
    boss to wonder whether the mode switched everything off.
    """
    available, total = PV.available_under(PV.PRIVATE_ONLY)
    assert 0 < available < total
    assert available / total > 0.8, \
        "privacy mode should cost about a tenth, not most of Friday"


def test_the_runtime_refuses_a_network_capability(monkeypatch):
    """
    The module is decorative unless the runtime consults it. This is the
    test that would fail if someone removed the call.
    """
    from friday.capability_runtime import CONVERSATION, CapabilityRuntime
    monkeypatch.setenv(PV.ENV_VAR, 'PRIVATE_ONLY')
    result = CapabilityRuntime(principal=CONVERSATION).execute('web_search', {'query': 'anything'})
    assert result.status == 'not_permitted'
    assert not result.may_claim_completion
    assert 'PRIVATE_ONLY' in result.error


def test_the_refusal_happens_before_anything_is_loaded(monkeypatch):
    """
    Refused before the adapter is resolved, so it cannot depend on every
    adapter remembering to ask - and so nothing is built from the request.
    """
    from friday.capability_runtime import CONVERSATION, CapabilityRuntime
    monkeypatch.setenv(PV.ENV_VAR, 'PRIVATE_ONLY')
    result = CapabilityRuntime(principal=CONVERSATION).execute('web_deep_research', {'question': 'anything'})
    assert result.status == 'not_permitted'
    assert 'Nothing was sent' in result.error


def test_a_local_capability_still_runs_under_privacy_mode(monkeypatch):
    """The mode costs about a tenth of Friday, not most of it."""
    from friday.capability_runtime import CONVERSATION, CapabilityRuntime
    monkeypatch.setenv(PV.ENV_VAR, 'PRIVATE_ONLY')
    result = CapabilityRuntime(principal=CONVERSATION).execute('get_current_time', {})
    assert result.status != 'not_permitted'
