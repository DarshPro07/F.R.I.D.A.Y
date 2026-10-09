"""
S1-001: the System One gateway - choose / score / yes_no / rank / classify /
confidence behind pluggable backends - and the rule that LOCAL_ONLY content
never reaches a hosted backend.

The gateway is real; the backends are a deterministic mock (a local one named
`ollama`, so the shared locality list calls it local) and a hosted spy that
records every call it receives.
"""
from __future__ import annotations

import pytest

from friday import data_class, privacy
from friday import system_one as s1

# A line the classifier reads as LOCAL_ONLY without any hint.
LOCAL_ONLY_TEXT = "LOCAL_ONLY: the Hartwell settlement terms"


class HostedSpy:
    """A hosted backend (not on the local list) that records what it is sent."""

    name = "hosted-spy"

    def __init__(self, value=True):
        self.calls: list[tuple] = []
        self.value = value

    def call(self, op, prompt, items):
        self.calls.append((op, prompt, tuple(items)))
        return self.value


@pytest.fixture(autouse=True)
def _open_mode(monkeypatch):
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)


def local_mock():
    return s1.MockBackend(name="ollama")


def test_the_six_operations_return_typed_answers_from_the_mock():
    g = s1.SystemOne([local_mock()])
    opts = ["red", "green", "blue"]

    assert g.choose("pick a colour", opts).value in opts
    assert 0.0 <= g.score("how good is this plan").value <= 1.0
    assert isinstance(g.yes_no("is the sky blue").value, bool)
    assert sorted(g.rank("order these", opts).value) == sorted(opts)
    assert g.classify("which kind of file", opts).value in opts
    assert 0.0 <= g.confidence("the answer is 4", "what is 2+2").value <= 1.0


def test_the_mock_is_deterministic():
    a = s1.SystemOne([local_mock()]).choose("pick", ["x", "y", "z"])
    b = s1.SystemOne([local_mock()]).choose("pick", ["x", "y", "z"])
    assert a.value == b.value and a.backend == "ollama" and a.status == "ok"


def test_a_local_only_forced_route_never_calls_the_hosted_backend():
    hosted = HostedSpy()
    g = s1.SystemOne([hosted])

    res = g.yes_no("is this fine to share", data_class_hint=data_class.LOCAL_ONLY)

    assert res.status == "refused"
    assert res.data_class == data_class.LOCAL_ONLY
    assert hosted.calls == []
    assert res.attempts[0]["backend"] == "hosted-spy"
    assert res.attempts[0]["status"] == "refused"


def test_local_only_text_is_refused_by_a_hosted_backend_without_any_hint():
    hosted = HostedSpy()
    res = s1.SystemOne([hosted]).yes_no(LOCAL_ONLY_TEXT)
    assert data_class.classify(LOCAL_ONLY_TEXT) == data_class.LOCAL_ONLY
    assert res.status == "refused" and hosted.calls == []


def test_a_local_only_request_skips_hosted_and_is_served_by_the_local_backend():
    hosted = HostedSpy()
    g = s1.SystemOne([hosted, local_mock()])

    res = g.choose("pick", ["a", "b"], data_class_hint=data_class.LOCAL_ONLY)

    assert res.status == "ok" and res.backend == "ollama"
    assert hosted.calls == []


def test_the_options_are_classified_too():
    hosted = HostedSpy(value="a")
    res = s1.SystemOne([hosted]).choose("pick one", ["a", LOCAL_ONLY_TEXT])
    assert res.status == "refused" and hosted.calls == []


def test_a_hosted_backend_gets_redacted_text_for_redactable_content():
    hosted = HostedSpy(value=True)
    text = "is Jane Doe at jane.doe@example.com a good lead"
    assert data_class.classify(text) == data_class.CUSTOMER_PII

    res = s1.SystemOne([hosted]).yes_no(text)

    assert res.status == "ok" and res.redactions > 0
    sent = hosted.calls[0][1]
    assert "jane.doe@example.com" not in sent and "Jane Doe" not in sent


def test_private_only_mode_refuses_every_hosted_backend(monkeypatch):
    monkeypatch.setenv(privacy.ENV_VAR, privacy.PRIVATE_ONLY)
    hosted = HostedSpy()
    res = s1.SystemOne([hosted]).yes_no("email me at jane.doe@example.com, Jane Doe")
    assert res.status == "refused" and hosted.calls == []


def test_a_failing_backend_falls_over_to_the_next_and_the_failure_is_recorded():
    class Broken:
        name = "broken"

        def call(self, op, prompt, items):
            raise RuntimeError("down")

    res = s1.SystemOne([Broken(), local_mock()]).yes_no("fine?")
    assert res.status == "ok" and res.backend == "ollama"
    assert res.attempts[0]["backend"] == "broken" and res.attempts[0]["status"] == "failed"


def test_a_backend_answer_of_the_wrong_shape_is_a_failure_not_a_result():
    bad = HostedSpy(value="not-an-option")
    res = s1.SystemOne([bad]).choose("pick", ["a", "b"])
    assert res.status == "failed" and res.value is None
    assert bad.calls  # it was called, and its answer was rejected


def test_bad_arguments_raise_before_any_backend_is_called():
    hosted = HostedSpy()
    g = s1.SystemOne([hosted])
    with pytest.raises(ValueError):
        g.choose("pick", [])
    with pytest.raises(ValueError):
        g.rank("order", ["only-one", "only-one"])
    with pytest.raises(ValueError):
        s1.SystemOne([])
    assert hosted.calls == []
