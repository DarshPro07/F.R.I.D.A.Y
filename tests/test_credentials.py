"""A configured credential is not a usable one: placeholders and refused keys
are named as such, and no value ever reaches a verdict."""

from __future__ import annotations

from pathlib import Path

import pytest

from friday import credentials as C

ROOT = Path(__file__).resolve().parents[1]

#: Shaped like real keys (no template markers). Never valid anywhere.
REAL_LOOKING = {
    "LIVEKIT_URL": "wss://friday-7k2m9q.livekit.cloud",
    "LIVEKIT_API_KEY": "APIkq3Zb8Hn2WvTt",
    "LIVEKIT_API_SECRET": "Q8sD2fGh1JkL4zXcV7bN0mP3oI6uY9tR5eW2aS1d",
    "GOOGLE_API_KEY": "AIzaSyD4kQ9mZ2pL7vB1nX8cF3hJ6tR0wE5yU",
    "OPENAI_API_KEY": "sk-proj-Ab3dE5gH7jK9mN1pQ3rS5tU7vW9yZ1aB",
}


def template_values() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            values[name.strip()] = value.strip()
    return values


@pytest.mark.parametrize("name", ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
                                  "SARVAM_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"])
def test_every_value_in_the_env_template_is_a_placeholder(name):
    verdict = C.classify_static(name, template_values()[name])
    assert verdict.state == C.PLACEHOLDER, name


def test_blank_and_unset_are_missing():
    assert C.classify_static("GROQ_API_KEY", template_values()["GROQ_API_KEY"]).state == C.MISSING
    assert C.classify_static("X_API_KEY", None).state == C.MISSING
    assert C.classify_static("X_API_KEY", "   ").state == C.MISSING


@pytest.mark.parametrize("name", sorted(REAL_LOOKING))
def test_a_key_shaped_value_is_not_mistaken_for_a_template(name):
    assert C.classify_static(name, REAL_LOOKING[name]).state == C.UNVERIFIED


def test_a_livekit_url_that_is_not_a_url_is_refused():
    assert C.classify_static("LIVEKIT_URL", "friday-7k2m9q.livekit.cloud").state == C.REJECTED
    assert C.classify_static("LIVEKIT_URL", "ftp://friday-7k2m9q.livekit.cloud").state == C.REJECTED


def test_a_probe_decides_only_credentials_that_are_not_templates():
    calls = []

    def probe(name, value):
        calls.append(name)
        return C.Verdict(C.REJECTED, f"{name} was rejected by the provider (HTTP 401)")

    assert C.check_credential("OPENAI_API_KEY", "sk-proj-xxxxxxxxxxxxxxxx", probe=probe).state \
        == C.PLACEHOLDER
    assert C.check_credential("OPENAI_API_KEY", "", probe=probe).state == C.MISSING
    assert calls == []
    assert C.check_credential("OPENAI_API_KEY", REAL_LOOKING["OPENAI_API_KEY"], probe=probe).state \
        == C.REJECTED
    assert calls == ["OPENAI_API_KEY"]


def test_a_provider_without_a_probe_stays_unverified_never_valid():
    assert C.live_probe("SARVAM_API_KEY", "anything") is None
    verdict = C.check_credential("SARVAM_API_KEY", "k3Jd9sLp2QwErTy", probe=C.live_probe)
    assert verdict.state == C.UNVERIFIED


def test_a_probe_that_raises_becomes_unverified_not_a_crash():
    def probe(name, value):
        raise RuntimeError(f"boom {value}")

    verdict = C.check_credential("OPENAI_API_KEY", REAL_LOOKING["OPENAI_API_KEY"], probe=probe)
    assert verdict.state == C.UNVERIFIED
    assert REAL_LOOKING["OPENAI_API_KEY"] not in verdict.detail


class FakeFetch:
    def __init__(self, status=200, content=b"{}", error=None):
        self.status, self.content, self.error = status, content, error
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return {"status": self.status, "content": self.content, "headers": {}, "url": url,
                "chain": [url]}


@pytest.mark.parametrize("probe,key", [(C.probe_openai, "OPENAI_API_KEY"),
                                       (C.probe_google, "GOOGLE_API_KEY")])
def test_provider_probes_map_the_providers_answer(monkeypatch, probe, key):
    from friday import netguard

    value = REAL_LOOKING[key]
    for status, expected in ((200, C.VALID), (401, C.REJECTED), (500, C.UNVERIFIED)):
        fetch = FakeFetch(status)
        monkeypatch.setattr(netguard, "fetch", fetch)
        verdict = probe(key, value)
        assert verdict.state == expected, (key, status)
        assert value not in verdict.detail
        url, kwargs = fetch.calls[0]
        assert value not in url                      # the key travels in a header, not the URL
        assert kwargs["timeout"] == C.PROBE_TIMEOUT_S
        assert value in kwargs["headers"].values() or f"Bearer {value}" in kwargs["headers"].values()


def test_google_answers_an_unknown_key_with_400_api_key_invalid(monkeypatch):
    from friday import netguard

    body = b'{"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT", ' \
           b'"details": [{"reason": "API_KEY_INVALID"}]}}'
    monkeypatch.setattr(netguard, "fetch", FakeFetch(400, body))
    assert C.probe_google("GOOGLE_API_KEY", REAL_LOOKING["GOOGLE_API_KEY"]).state == C.REJECTED
    monkeypatch.setattr(netguard, "fetch", FakeFetch(400, b'{"error": "bad request"}'))
    assert C.probe_google("GOOGLE_API_KEY", REAL_LOOKING["GOOGLE_API_KEY"]).state == C.UNVERIFIED


def test_a_gemini_key_in_the_wrong_format_is_refused_before_any_call(monkeypatch):
    """The 2026-10-04 defect: a 97-char 'AQ.' value is not a Gemini key."""
    from friday import netguard

    def no_network(*args, **kwargs):
        raise AssertionError("a wrong-format key must not reach the provider")

    monkeypatch.setattr(netguard, "fetch", no_network)
    wrong = "AQ." + "Ab3dE5gH7jK9mN1pQ3rS5tU7vW9yZ1aB" * 3
    verdict = C.check_credential("GOOGLE_API_KEY", wrong, probe=C.live_probe)
    assert verdict.state == C.REJECTED
    assert "GOOGLE_API_KEY" in verdict.detail and "AIza" in verdict.detail
    assert wrong not in verdict.detail and wrong[:6] not in verdict.detail.replace("AIza", "")


@pytest.mark.parametrize("probe,key,body,code", [
    (C.probe_google, "GOOGLE_API_KEY",
     b'{"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT", '
     b'"details": [{"reason": "API_KEY_INVALID"}]}}', "API_KEY_INVALID"),
    (C.probe_google, "GOOGLE_API_KEY",
     b'{"error": {"code": 401, "message": "bad", "status": "UNAUTHENTICATED"}}', "UNAUTHENTICATED"),
    (C.probe_openai, "OPENAI_API_KEY",
     b'{"error": {"message": "Incorrect API key provided: sk-proj-****Z1aB.", '
     b'"type": "invalid_request_error", "code": "invalid_api_key"}}', "invalid_api_key"),
])
def test_a_refusal_carries_the_providers_reason_code_and_never_its_prose(monkeypatch, probe, key, body, code):
    from friday import netguard

    status = 400 if code == "API_KEY_INVALID" else 401
    monkeypatch.setattr(netguard, "fetch", FakeFetch(status, body))
    verdict = probe(key, REAL_LOOKING[key])
    assert verdict.state == C.REJECTED
    assert key in verdict.detail and code in verdict.detail
    assert "Incorrect" not in verdict.detail and "****" not in verdict.detail
    assert REAL_LOOKING[key] not in verdict.detail


def test_an_unreachable_provider_is_unverified_not_rejected(monkeypatch):
    from friday import netguard

    monkeypatch.setattr(netguard, "fetch", FakeFetch(error=TimeoutError("timed out")))
    verdict = C.probe_openai("OPENAI_API_KEY", REAL_LOOKING["OPENAI_API_KEY"])
    assert verdict.state == C.UNVERIFIED
    assert "TimeoutError" in verdict.detail


class FakeClient:
    """Stands in for httpx.Client: records the POST, answers a fixed status."""

    status = 200
    error = None
    seen: list = []

    def __init__(self, **kwargs):
        type(self).seen = [kwargs]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        type(self).seen.append((url, kwargs))
        if self.error:
            raise self.error

        class Reply:
            status_code = FakeClient.status

        return Reply()


def test_livekit_probe_signs_a_token_the_project_can_verify(monkeypatch):
    import httpx
    from livekit import api

    monkeypatch.setattr(httpx, "Client", FakeClient)
    key, secret = REAL_LOOKING["LIVEKIT_API_KEY"], REAL_LOOKING["LIVEKIT_API_SECRET"]
    for status, expected in ((200, C.VALID), (401, C.REJECTED), (503, C.UNVERIFIED)):
        monkeypatch.setattr(FakeClient, "status", status)
        verdict = C.probe_livekit(REAL_LOOKING["LIVEKIT_URL"], key, secret)
        assert verdict.state == expected, status
        assert secret not in verdict.detail and key not in verdict.detail
    init, (url, kwargs) = FakeClient.seen
    assert init["timeout"] == C.PROBE_TIMEOUT_S and init["follow_redirects"] is False
    assert url == "https://friday-7k2m9q.livekit.cloud/twirp/livekit.RoomService/ListRooms"
    token = kwargs["headers"]["Authorization"].removeprefix("Bearer ")
    claims = api.TokenVerifier(key, secret).verify(token)
    assert claims.video.room_list is True


def test_livekit_probe_timeout_is_unverified(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(FakeClient, "error", TimeoutError("timed out"))
    verdict = C.probe_livekit(REAL_LOOKING["LIVEKIT_URL"], "k", "s")
    assert verdict.state == C.UNVERIFIED


def livekit_args(**override):
    values = {k: v for k, v in REAL_LOOKING.items() if k.startswith("LIVEKIT_")}
    values.update(override)
    return values["LIVEKIT_URL"], values["LIVEKIT_API_KEY"], values["LIVEKIT_API_SECRET"]


def test_a_rejected_livekit_key_pair_does_not_condemn_the_url():
    refuse = lambda url, key, secret: C.Verdict(C.REJECTED, "rejected (HTTP 401)")   # noqa: E731
    verdicts = C.check_livekit(*livekit_args(), probe=refuse)
    assert verdicts["LIVEKIT_URL"].state == C.UNVERIFIED
    assert verdicts["LIVEKIT_API_KEY"].state == verdicts["LIVEKIT_API_SECRET"].state == C.REJECTED
    assert C.worst_livekit(verdicts).state == C.REJECTED
    assert "LIVEKIT_URL" not in C.worst_livekit(verdicts).detail


def test_livekit_is_probed_only_when_all_three_values_are_sound():
    calls = []

    def probe(url, key, secret):
        calls.append(url)
        return C.Verdict(C.VALID, "accepted")

    C.check_livekit(*livekit_args(LIVEKIT_URL=template_values()["LIVEKIT_URL"]), probe=probe)
    C.check_livekit(*livekit_args(LIVEKIT_API_SECRET=""), probe=probe)
    C.check_livekit(*livekit_args(), live=False, probe=probe)
    assert calls == []
    assert C.worst_livekit(C.check_livekit(*livekit_args(), probe=probe)).state == C.VALID
    assert len(calls) == 1


def test_worst_livekit_names_the_variables_in_the_worst_state():
    verdicts = C.check_livekit(template_values()["LIVEKIT_URL"], "", "", live=False)
    worst = C.worst_livekit(verdicts)
    assert worst.state == C.MISSING
    assert "LIVEKIT_API_KEY" in worst.detail and "LIVEKIT_URL" not in worst.detail
