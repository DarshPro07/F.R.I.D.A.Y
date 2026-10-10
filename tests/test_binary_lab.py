"""
BIN-004: dynamic analysis of a sample happens only through a Cyber Lab provider
at the offline tier, under an authorization that names the sample.

Nothing here runs a binary. The "sample" is inert bytes carrying a marker; the
host-execution tests also make every process-starting call fail loudly, so a
code path that tried to start one would break the test rather than pass it.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from friday import binary_lab as BL
from friday import contracts as c
from friday import cyber_lab
from friday import forge
from friday import trust as T
from friday.fsjail import FileJail
from friday.toolsets import files as F

MARKER = b"SAMPLE-BYTES-MUST-NOT-LEAK-0451"
SAMPLE = b"MZ" + b"\0" * 62 + MARKER
SHA = hashlib.sha256(SAMPLE).hexdigest()
FUTURE = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")


@pytest.fixture
def root(tmp_path):
    F.reset_jail(FileJail(roots=(tmp_path,)))
    yield tmp_path
    F.reset_jail(None)


@pytest.fixture
def sample(root):
    path = root / "unknown.exe"
    path.write_bytes(SAMPLE)
    return path


@pytest.fixture
def run():
    return c.Run.create("analyse that sample", capability="binary")


@pytest.fixture
def no_processes(monkeypatch):
    """Any attempt to start a process fails the test."""
    calls = []

    def boom(*a, **k):
        calls.append((a, k))
        raise AssertionError("a process was started")

    for mod, name in ((subprocess, "Popen"), (subprocess, "run"), (os, "system"),
                      (os, "startfile"), (os, "spawnv"), (os, "execv")):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, boom)
    return calls


def auth(**over):
    base = dict(owner_identity="owner", target_scope=(SHA,),
                ownership_or_permission_basis="my own research sample",
                allowed_actions=(BL.ACTION,), max_scan_intensity=T.PASSIVE,
                expires_at=FUTURE, approval_id="appr-1")
    return T.SecurityAuthorization(**{**base, **over})


def lab(tier=cyber_lab.TIER_OFFLINE):
    return cyber_lab.MockProvider("mock-lab", tier)


# -- the host never runs it -------------------------------------------------


@pytest.mark.parametrize("where", ["host", "local_machine", "", "HOST", "native"])
def test_executing_on_the_host_is_refused_whatever_is_requested(
        run, sample, no_processes, where):
    provider = lab()
    out = BL.dynamic_analysis(run, str(sample), provider=provider, auth=auth(), where=where)
    assert out["status"] == BL.REFUSED
    assert "host" in out["reason"]
    assert provider.calls == []
    assert out["evidence"]["allowed"] is False
    assert cyber_lab.verify_evidence(cyber_lab.LabEvidence(**out["evidence"]))
    assert no_processes == []


def test_with_no_lab_provider_it_refuses_and_says_why(run, sample, no_processes):
    out = BL.dynamic_analysis(run, str(sample), provider=None, auth=auth())
    assert out["status"] == BL.REFUSED
    assert "Cyber Lab provider" in out["reason"]
    assert out["sample"]["sha256"] == SHA
    assert no_processes == []


# -- the offline tier with an authorization naming the sample ---------------


def test_offline_provider_with_a_valid_authorization_completes_and_verifies(
        run, sample, no_processes):
    provider = lab()
    out = BL.dynamic_analysis(run, str(sample), provider=provider, auth=auth())
    assert out["status"] == BL.COMPLETED, out
    ev = cyber_lab.LabEvidence(**out["evidence"])
    assert cyber_lab.verify_evidence(ev)
    assert (ev.tier, ev.intensity, ev.host) == (cyber_lab.TIER_OFFLINE, T.PASSIVE, SHA)
    assert ev.allowed and ev.approval_id == "appr-1"
    assert out["report"] == ev.output and out["report"]["action"] == BL.ACTION
    assert len(provider.calls) == 1
    assert no_processes == []


def test_tampered_evidence_no_longer_verifies(run, sample):
    out = BL.dynamic_analysis(run, str(sample), provider=lab(), auth=auth())
    forged = dict(out["evidence"], tier="active")
    assert not cyber_lab.verify_evidence(cyber_lab.LabEvidence(**forged))


@pytest.mark.parametrize("make_auth,fragment", [
    (lambda: None, "no SecurityAuthorization"),
    (lambda: auth(target_scope=("f" * 64,)), "outside the authorized scope"),
    (lambda: auth(expires_at="2000-01-01T00:00:00+00:00"), "expired"),
    (lambda: auth(allowed_actions=("something_else",)), "allowed_actions"),
])
def test_without_an_authorization_naming_this_sample_nothing_runs(
        run, sample, make_auth, fragment):
    provider = lab()
    out = BL.dynamic_analysis(run, str(sample), provider=provider, auth=make_auth())
    assert out["status"] == BL.REFUSED
    assert fragment in out["reason"]
    assert provider.calls == []


@pytest.mark.parametrize("tier", [cyber_lab.TIER_SCOPED_NETWORK, cyber_lab.TIER_ACTIVE])
def test_a_provider_above_the_offline_tier_is_refused_for_a_sample(run, sample, tier):
    provider = lab(tier)
    ceiling = T.EXPLOIT if tier == cyber_lab.TIER_ACTIVE else T.LOW_ACTIVE
    out = BL.dynamic_analysis(run, str(sample), provider=provider,
                              auth=auth(max_scan_intensity=ceiling))
    assert out["status"] == BL.REFUSED
    assert "offline" in out["reason"] and tier in out["reason"]
    assert provider.calls == []
    assert out["evidence"]["tier"] == tier and out["evidence"]["allowed"] is False


# -- samples are hash and metadata only -------------------------------------


def test_the_sample_bytes_never_reach_the_provider_or_the_result(run, sample):
    provider = lab()
    out = BL.dynamic_analysis(run, str(sample), provider=provider, auth=auth())
    assert out["sample"] == {"sha256": SHA, "size": len(SAMPLE), "name": "unknown.exe"}
    assert MARKER.decode() not in json.dumps(out, default=str)
    assert MARKER.decode() not in repr(provider.calls)
    assert provider.calls[0].host == SHA


def test_a_path_outside_the_jail_is_refused(run, root, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "x.exe"
    elsewhere.write_bytes(SAMPLE)
    provider = lab()
    out = BL.dynamic_analysis(run, str(elsewhere), provider=provider, auth=auth())
    assert out["status"] == BL.REFUSED and "path refused" in out["reason"]
    assert provider.calls == []


def test_a_missing_file_is_refused_not_raised(run, root):
    out = BL.dynamic_analysis(run, str(root / "nope.exe"), provider=lab(), auth=auth())
    assert out["status"] == BL.REFUSED and "no such file" in out["reason"]


# -- canaries (SANDBOX-001) --------------------------------------------------


class _Toucher(cyber_lab.MockProvider):
    def __init__(self, reads, leak=""):
        super().__init__("toucher", cyber_lab.TIER_OFFLINE)
        self._reads, self._leak = reads, leak

    def run(self, request):
        super().run(request)
        return {"host": request.host, "reads": self._reads, "note": self._leak}


def test_a_touched_canary_fails_the_evaluation_and_withholds_the_report(run, sample, tmp_path):
    canaries = forge.seed_canaries(tmp_path / "canary-work")
    victim = next(p for p in canaries.home.rglob("*") if p.is_file())
    out = BL.dynamic_analysis(run, str(sample), provider=_Toucher([str(victim)]),
                              auth=auth(), canaries=canaries)
    assert out["status"] == BL.FAILED
    assert out["findings"] and "canary" in out["findings"][0]
    assert out["report"] == {}
    assert canaries.token not in json.dumps(out, default=str)
    # The run happened, and its hash-bound record says so; only the output is withheld.
    ev = cyber_lab.LabEvidence(**out["evidence"])
    assert cyber_lab.verify_evidence(ev)
    assert ev.allowed is True and ev.approval_id == "appr-1"
    assert (ev.provider, ev.tier, ev.host, ev.intensity) == (
        "toucher", cyber_lab.TIER_OFFLINE, SHA, T.PASSIVE)
    assert ev.output == {"withheld": "the lab run touched a canary"}


def test_a_canary_value_in_the_output_fails_the_evaluation(run, sample, tmp_path):
    canaries = forge.seed_canaries(tmp_path / "canary-work")
    out = BL.dynamic_analysis(run, str(sample), provider=_Toucher([], leak=canaries.token),
                              auth=auth(), canaries=canaries)
    assert out["status"] == BL.FAILED
    assert canaries.token not in json.dumps(out, default=str)


def test_untouched_canaries_pass(run, sample, tmp_path):
    canaries = forge.seed_canaries(tmp_path / "canary-work")
    out = BL.dynamic_analysis(run, str(sample), provider=_Toucher([]),
                              auth=auth(), canaries=canaries)
    assert out["status"] == BL.COMPLETED


class _Silent(cyber_lab.MockProvider):
    """Changes the canaries it was never told about and reports nothing."""

    def __init__(self, seen, action):
        super().__init__("silent", cyber_lab.TIER_OFFLINE)
        self._seen, self._action = seen, action

    def run(self, request):
        out = super().run(request)
        home = self._seen[0].home
        victim = sorted(p for p in home.rglob("*") if p.is_file())[0]
        self._action(home, victim)
        return out


@pytest.fixture
def seeded(monkeypatch):
    """Records the canaries `dynamic_analysis` seeds for itself."""
    seen = []
    real = forge.seed_canaries

    def spy(workdir):
        made = real(workdir)
        seen.append(made)
        return made

    monkeypatch.setattr(forge, "seed_canaries", spy)
    return seen


def test_canaries_are_seeded_by_default_and_a_clean_run_passes(run, sample, seeded):
    out = BL.dynamic_analysis(run, str(sample), provider=lab(), auth=auth())
    assert out["status"] == BL.COMPLETED and out["findings"] == []
    assert len(seeded) == 1
    assert not seeded[0].home.exists()  # the temporary workdir is cleaned up


@pytest.mark.parametrize("how,fragment", [
    (lambda home, victim: victim.write_text("tampered"), "modified"),
    (lambda home, victim: victim.unlink(), "missing"),
    (lambda home, victim: (home / "dropped.txt").write_text("x"), "added"),
])
def test_a_canary_file_changed_without_being_reported_fails_by_default(
        run, sample, seeded, how, fragment):
    out = BL.dynamic_analysis(run, str(sample), provider=_Silent(seeded, how), auth=auth())
    assert out["status"] == BL.FAILED
    assert any(fragment in f for f in out["findings"])
    assert out["report"] == {}
    ev = cyber_lab.LabEvidence(**out["evidence"])
    assert ev.allowed and cyber_lab.verify_evidence(ev)
    assert seeded[0].token not in json.dumps(out, default=str)


def test_a_canary_file_changed_without_being_reported_fails_with_given_canaries(
        run, sample, tmp_path):
    canaries = forge.seed_canaries(tmp_path / "canary-work")
    out = BL.dynamic_analysis(
        run, str(sample), auth=auth(), canaries=canaries,
        provider=_Silent([canaries], lambda home, victim: victim.write_text("tampered")))
    assert out["status"] == BL.FAILED and any("modified" in f for f in out["findings"])


def test_a_canary_read_is_caught_on_the_default_path(run, sample, seeded):
    class Reader(cyber_lab.MockProvider):
        def run(self, request):
            home = seeded[0].home
            return {"reads": [str(next(p for p in home.rglob("*") if p.is_file()))]}

    out = BL.dynamic_analysis(run, str(sample),
                              provider=Reader("reader", cyber_lab.TIER_OFFLINE), auth=auth())
    assert out["status"] == BL.FAILED and out["report"] == {}
