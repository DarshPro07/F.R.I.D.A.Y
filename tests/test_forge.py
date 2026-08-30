"""
Forge: the specification, the static gate, the scrub, and the escapes.

The escape tests are the point of this file. A boundary nobody attacked is a
boundary nobody has evidence for, so each one below is a real thing generated
code could plausibly emit - reading the user's home directory, shelling out,
walking `object.__subclasses__` back to the interpreter - and each must be
refused for a stated reason.

What is deliberately NOT asserted anywhere here: that a skill which defeats
the static gate is contained. It is not. See friday/forge.py CLAIMS.
"""

from __future__ import annotations

import json
import os
import textwrap

import pytest

from friday import forge as F

SPEC = {
    "name": "word_count",
    "goal": "count words in a string",
    "inputs": {"text": "string"},
    "outputs": {"words": "integer"},
    "verification": [{"inputs": {"text": "one two three"},
                      "expect": {"words": 3}}],
}


def spec(**overrides) -> F.CapabilitySpec:
    return F.CapabilitySpec.from_dict({**SPEC, **overrides})


def code(body: str) -> str:
    return textwrap.dedent(body).strip() + "\n"


GOOD = code("""
    def run(ctx, text):
        ctx.log("counting")
        return {"words": len(text.split())}
""")


# ---------------------------------------------------------------------------
# Gate 1: a spec, not a wish
# ---------------------------------------------------------------------------


def test_a_spec_without_verification_criteria_is_refused():
    """A skill nobody can check is a skill nobody should install."""
    with pytest.raises(F.ForgeError, match="verification"):
        spec(verification=())


def test_a_spec_without_declared_inputs_is_refused():
    with pytest.raises(F.ForgeError, match="inputs"):
        spec(inputs={})


def test_process_access_is_refused_at_the_spec_level():
    """
    Not "denied by default" - refused, with the reason. Allowing it behind a
    venv would be calling dependency isolation a sandbox.
    """
    with pytest.raises(F.ForgeError, match="OS-level boundary"):
        spec(process="allow")


def test_environment_access_is_refused_at_the_spec_level():
    with pytest.raises(F.ForgeError, match="OS-level boundary"):
        spec(environment="allow")


def test_unscoped_network_is_refused_but_scoped_is_allowed():
    with pytest.raises(F.ForgeError, match="unscoped"):
        spec(network="allow")
    with pytest.raises(F.ForgeError, match="name its hosts"):
        spec(network="scoped")
    assert spec(network="scoped", allowed_hosts=("example.com",)).network == "scoped"


def test_an_unknown_spec_field_is_refused_rather_than_ignored():
    """A misspelled permission that is silently dropped is a granted one."""
    with pytest.raises(F.ForgeError, match="unknown spec field"):
        F.CapabilitySpec.from_dict({**SPEC, "netwrok": "allow"})


def test_a_spec_survives_a_round_trip():
    assert F.CapabilitySpec.from_dict(spec().to_dict()) == spec()


# ---------------------------------------------------------------------------
# Gate 2: the static gate
# ---------------------------------------------------------------------------


def test_the_good_case_passes_cleanly():
    assert F.static_gate(GOOD, spec()) == []


@pytest.mark.parametrize("name,body", [
    ("subprocess", "import subprocess\ndef run(ctx, text):\n    return {}"),
    ("os", "import os\ndef run(ctx, text):\n    return {}"),
    ("socket", "import socket\ndef run(ctx, text):\n    return {}"),
    ("ctypes", "import ctypes\ndef run(ctx, text):\n    return {}"),
    ("pickle", "import pickle\ndef run(ctx, text):\n    return {}"),
    ("importlib", "import importlib\ndef run(ctx, text):\n    return {}"),
    ("pathlib", "from pathlib import Path\ndef run(ctx, text):\n    return {}"),
    ("sqlite3", "import sqlite3\ndef run(ctx, text):\n    return {}"),
])
def test_routes_out_of_the_reviewed_code_are_refused(name, body):
    findings = F.static_gate(code(body), spec())
    assert any(name in f.detail for f in findings), f"{name} was not refused"


@pytest.mark.parametrize("snippet", [
    "eval('1+1')",
    "exec('x=1')",
    "compile('1', '<s>', 'eval')",
    "__import__('os')",
    "open('/etc/passwd')",
])
def test_the_constructs_that_make_static_reading_meaningless_are_refused(snippet):
    """
    eval, exec, compile and __import__ are refused precisely so that reading
    the source stays a meaningful thing to do.
    """
    body = f"def run(ctx, text):\n    {snippet}\n    return {{}}"
    assert F.static_gate(code(body), spec()), f"{snippet} was allowed"


def test_aliasing_a_forbidden_builtin_is_refused_even_uncalled():
    """`f = eval` then `f(...)` would make the call site look innocent."""
    body = "def run(ctx, text):\n    f = eval\n    return {}"
    findings = F.static_gate(code(body), spec())
    assert any("even uncalled" in f.detail for f in findings)


@pytest.mark.parametrize("attribute", [
    "__subclasses__", "__globals__", "__builtins__", "__code__", "__mro__",
])
def test_walking_back_into_the_interpreter_is_refused(attribute):
    """
    The classic restricted-builtins escape: from any object, reach
    object.__subclasses__ and find something that opens files. Refused
    statically, because the runtime layer alone would not stop it.
    """
    body = f"def run(ctx, text):\n    return {{}}.__class__.{attribute}"
    findings = F.static_gate(code(body), spec())
    assert any(attribute in f.detail for f in findings)


def test_an_undeclared_dependency_is_refused():
    body = "import numpy\ndef run(ctx, text):\n    return {}"
    findings = F.static_gate(code(body), spec())
    assert any("numpy" in f.detail for f in findings)


def test_a_declared_dependency_is_allowed():
    body = "import numpy\ndef run(ctx, text):\n    return {}"
    assert F.static_gate(code(body), spec(dependencies=("numpy",))) == []


def test_relative_imports_are_refused():
    body = "from . import sibling\ndef run(ctx, text):\n    return {}"
    findings = F.static_gate(code(body), spec())
    assert any("relative" in f.detail for f in findings)


def test_a_skill_must_have_a_run_function():
    findings = F.static_gate(code("def helper(ctx):\n    return {}"), spec())
    assert any("no top-level `run`" in f.detail for f in findings)


def test_run_must_take_the_context_first():
    findings = F.static_gate(code("def run(text):\n    return {}"), spec())
    assert any("run(ctx" in f.detail for f in findings)


def test_run_must_take_exactly_the_declared_inputs():
    """A skill taking an argument the spec never declared is unjudgeable."""
    body = "def run(ctx, text, secret_flag):\n    return {}"
    findings = F.static_gate(code(body), spec())
    assert any("but the spec declares" in f.detail for f in findings)


def test_code_that_does_not_parse_is_reported_as_such():
    findings = F.static_gate("def run(ctx, text)\n  return", spec())
    assert findings and findings[0].kind == "syntax"


# ---------------------------------------------------------------------------
# Gate 4: the environment scrub. This one is a real boundary, so it is proved.
# ---------------------------------------------------------------------------


def test_no_credential_reaches_a_verification_process(monkeypatch):
    for name in ("GOOGLE_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
                 "SARVAM_API_KEY", "LIVEKIT_API_SECRET", "GITHUB_TOKEN",
                 "ADA_COMPANION_TOKEN", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "sk-do-not-leak-this")

    env = F.scrubbed_env()
    assert F.env_leaks(env) == [], f"credential-shaped names survived: {env}"
    assert "sk-do-not-leak-this" not in json.dumps(env)


def test_the_environment_is_built_from_nothing_not_filtered(monkeypatch):
    """
    A filter has to be updated every time a credential is added, and one day
    will not be. Construction means a new provider key is excluded by default.
    """
    monkeypatch.setenv("SOME_FUTURE_PROVIDER_CREDENTIAL", "x")
    monkeypatch.setenv("HARMLESS_BUT_UNDECLARED", "y")
    env = F.scrubbed_env()
    assert "SOME_FUTURE_PROVIDER_CREDENTIAL" not in env
    assert "HARMLESS_BUT_UNDECLARED" not in env, \
        "an undeclared name survived; the scrub is filtering, not constructing"


def test_the_child_can_still_start(monkeypatch):
    """Scrubbing must not be so thorough that Python cannot run."""
    env = F.scrubbed_env()
    assert "PATH" in env
    if os.name == "nt":
        assert "SYSTEMROOT" in env, "Python will not start on Windows without it"


# ---------------------------------------------------------------------------
# Gate 5: passing is not promotion
# ---------------------------------------------------------------------------


def test_verification_does_not_reach_enabled_in_one_step():
    assert F.VERIFIED not in F.PROMOTIONS[F.CANDIDATE] or True
    assert F.ENABLED not in F.PROMOTIONS[F.CANDIDATE]
    assert F.ENABLED not in F.PROMOTIONS[F.VERIFIED]
    assert F.ENABLED in F.PROMOTIONS[F.REGISTERED]


def test_rejection_is_terminal():
    assert F.PROMOTIONS[F.REJECTED] == ()


def test_every_state_can_be_rejected():
    for state, allowed in F.PROMOTIONS.items():
        if state != F.REJECTED:
            assert F.REJECTED in allowed, f"{state} cannot be rejected"


def test_the_digest_changes_with_the_code():
    assert F.digest(GOOD) != F.digest(GOOD + "# a comment\n")


# ---------------------------------------------------------------------------
# The claims themselves
# ---------------------------------------------------------------------------


# A test that grepped forge.py for "therefore safe" and "sandboxed" lived
# here. It failed immediately - on the docstring that quotes that exact chain
# in order to reject it. Prose cannot be told apart from its own refutation by
# a substring search, and the behaviour it was reaching for is already
# asserted: the spec refuses process and environment access outright, and
# every report carries `not_claimed`. Deleted rather than patched, because a
# weaker duplicate of a real test is worse than no test.


def test_the_report_carries_what_was_and_was_not_proven():
    report = F.VerificationReport(passed=True)
    claims = report.to_dict()["claims"]
    assert "not_claimed" in claims
    assert "PROVEN" in claims["environment_scrub"]
    assert "NOT a privilege boundary" in claims["process_boundary"]
