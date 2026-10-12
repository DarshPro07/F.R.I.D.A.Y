"""
EVO-005: a signed candidate FRIDAY.exe is promoted only with release-gate
evidence on its commit and an owner approval naming its hash, is health-checked
after the restart, and is rolled back when the check fails. An unsigned,
tampered, replayed or downgraded candidate is refused before anything is
stopped or replaced.

Everything is a temp file and an injected stop/start/healthy; no real process,
no network, no machine state.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from friday import exe_promotion as ep

OLD = b"MZ-old-friday-exe"
NEW = b"MZ-new-friday-exe"
COMMIT = "a" * 40


def _raw_pub(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)


def _raw_priv(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.Raw,
                             serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())


class Runtime:
    """Records stop/start; `healthy_for` says which exe bytes pass the probe."""

    def __init__(self, live, healthy_for=(NEW, OLD), start_raises_for=()):
        self.live = live
        self.healthy_for = set(healthy_for)
        self.start_raises_for = set(start_raises_for)
        self.calls: list[str] = []

    def stop(self):
        self.calls.append("stop")

    def start(self):
        content = self.live.read_bytes()
        self.calls.append("start:" + ("new" if content == NEW else "old"))
        if content in self.start_raises_for:
            raise OSError("exe will not launch")

    def healthy(self):
        self.calls.append("probe")
        return self.live.read_bytes() in self.healthy_for


@pytest.fixture()
def key():
    return Ed25519PrivateKey.generate()


@pytest.fixture()
def env(tmp_path, key):
    live = tmp_path / "FRIDAY.exe"
    live.write_bytes(OLD)
    candidate = tmp_path / "candidate" / "FRIDAY.exe"
    candidate.parent.mkdir()
    candidate.write_bytes(NEW)
    manifest = ep.Manifest(version="7.2.0", sequence=5,
                           exe_sha256=hashlib.sha256(NEW).hexdigest(),
                           commit=COMMIT, key_id="owner-1")
    return {
        "live": live, "candidate": candidate, "manifest": manifest,
        "signature": ep.sign_manifest(manifest, _raw_priv(key)),
        "state_path": tmp_path / "exe_update_state.json",
        "trusted_keys": {"owner-1": _raw_pub(key)},
        "gate_report": {"ready": True,
                        "commit": {"sha": COMMIT, "dirty": False}},
        "approved_sha256": hashlib.sha256(NEW).hexdigest(),
        "runtime": Runtime(live),
    }


def _promote(env, **override):
    args = {k: env[k] for k in ("candidate", "manifest", "signature", "live",
                                "state_path", "trusted_keys", "gate_report",
                                "approved_sha256")}
    args.update(override)
    runtime = args.pop("runtime", env["runtime"])
    return ep.promote(args.pop("candidate"), args.pop("manifest"),
                      args.pop("signature"), live_exe=args.pop("live"),
                      state_path=args.pop("state_path"),
                      trusted_keys=args.pop("trusted_keys"),
                      gate_report=args.pop("gate_report"),
                      approved_sha256=args.pop("approved_sha256"),
                      stop=runtime.stop, start=runtime.start,
                      healthy=runtime.healthy)


def _state(env):
    return json.loads(env["state_path"].read_text(encoding="utf-8"))


def _untouched(env):
    assert env["live"].read_bytes() == OLD
    assert env["runtime"].calls == []


# --- the happy path -------------------------------------------------------

def test_verified_approved_candidate_is_promoted_restarted_and_health_checked(env):
    out = _promote(env)
    assert out.status == ep.PROMOTED, out.reasons
    assert env["live"].read_bytes() == NEW
    assert env["runtime"].calls == ["stop", "start:new", "probe"]
    assert _state(env)["sequence"] == 5
    assert _state(env)["exe_sha256"] == hashlib.sha256(NEW).hexdigest()


def test_the_prior_exe_is_kept_so_the_swap_can_be_undone(env):
    _promote(env)
    assert ep.previous_path(env["live"]).read_bytes() == OLD


# --- rollback on a failed health check ------------------------------------

def test_failed_health_check_restores_the_prior_exe_and_restarts_it(env):
    env["runtime"] = Runtime(env["live"], healthy_for=(OLD,))
    out = _promote(env)
    assert out.status == ep.ROLLED_BACK
    assert env["live"].read_bytes() == OLD
    assert env["runtime"].calls == ["stop", "start:new", "probe",
                                    "stop", "start:old", "probe"]
    assert not env["state_path"].exists()            # the floor did not move


def test_a_candidate_that_will_not_launch_is_rolled_back_not_ignored(env):
    env["runtime"] = Runtime(env["live"], start_raises_for=(NEW,))
    out = _promote(env)
    assert out.status == ep.ROLLED_BACK
    assert any("exe will not launch" in r for r in out.reasons)
    assert env["live"].read_bytes() == OLD


def test_a_rollback_that_is_itself_unhealthy_is_reported_not_hidden(env):
    env["runtime"] = Runtime(env["live"], healthy_for=())
    out = _promote(env)
    assert out.status == ep.ROLLBACK_FAILED
    assert env["live"].read_bytes() == OLD


def test_after_a_rollback_the_same_candidate_can_be_tried_again(env):
    env["runtime"] = Runtime(env["live"], healthy_for=(OLD,))
    assert _promote(env).status == ep.ROLLED_BACK
    env["runtime"] = Runtime(env["live"])
    assert _promote(env).status == ep.PROMOTED


# --- unsigned / tampered / downgraded are refused -------------------------

@pytest.mark.parametrize("signature", [None, b"", bytes(64), b"short"],
                         ids=["none", "empty", "forged", "malformed"])
def test_an_unsigned_or_badly_signed_candidate_is_refused(env, signature):
    out = _promote(env, signature=signature)
    assert out.status == ep.REFUSED
    assert any("signature" in r for r in out.reasons)
    _untouched(env)


def test_a_signature_from_an_untrusted_key_is_refused(env):
    other = Ed25519PrivateKey.generate()
    forged = ep.sign_manifest(env["manifest"], _raw_priv(other))
    out = _promote(env, signature=forged)
    assert out.status == ep.REFUSED
    _untouched(env)


def test_a_key_id_nobody_trusts_is_refused(env):
    out = _promote(env, trusted_keys={})
    assert out.status == ep.REFUSED
    assert any("key" in r for r in out.reasons)
    _untouched(env)


def test_an_exe_that_differs_from_the_signed_hash_is_refused(env):
    env["candidate"].write_bytes(NEW + b"-backdoor")
    out = _promote(env)
    assert out.status == ep.REFUSED
    assert any("sha256" in r for r in out.reasons)
    _untouched(env)


def test_a_manifest_edited_after_signing_is_refused(env):
    edited = dataclasses.replace(env["manifest"], sequence=99)
    out = _promote(env, manifest=edited)
    assert out.status == ep.REFUSED
    assert any("signature" in r for r in out.reasons)
    _untouched(env)


def test_a_downgrade_to_an_older_sequence_is_refused(env):
    env["state_path"].write_text(json.dumps(
        {"sequence": 9, "version": "7.9.0", "exe_sha256": "x"}), encoding="utf-8")
    out = _promote(env)
    assert out.status == ep.REFUSED
    assert any("downgrade" in r for r in out.reasons)
    _untouched(env)


def test_replaying_the_installed_sequence_is_refused(env):
    assert _promote(env).status == ep.PROMOTED
    env["runtime"].calls.clear()
    out = _promote(env)
    assert out.status == ep.REFUSED
    assert env["runtime"].calls == []


def test_a_lower_version_is_refused_even_with_a_higher_sequence(env):
    env["state_path"].write_text(json.dumps(
        {"sequence": 1, "version": "8.0.0", "exe_sha256": "x"}), encoding="utf-8")
    out = _promote(env)
    assert out.status == ep.REFUSED
    assert any("version" in r for r in out.reasons)
    _untouched(env)


def test_an_unreadable_update_state_fails_closed(env):
    env["state_path"].write_text("{not json", encoding="utf-8")
    out = _promote(env)
    assert out.status == ep.REFUSED
    assert any("state" in r for r in out.reasons)
    _untouched(env)


# --- owner approval and release evidence ----------------------------------

@pytest.mark.parametrize("approved", [None, "", "f" * 64],
                         ids=["none", "empty", "other-hash"])
def test_without_owner_approval_naming_this_hash_nothing_is_promoted(env, approved):
    out = _promote(env, approved_sha256=approved)
    assert out.status == ep.REFUSED
    assert any("approval" in r for r in out.reasons)
    _untouched(env)


@pytest.mark.parametrize("report", [
    {"ready": False, "commit": {"sha": COMMIT, "dirty": False}},
    {"ready": True, "commit": {"sha": "b" * 40, "dirty": False}},
    {"ready": True, "commit": {"sha": COMMIT, "dirty": True}},
    {}, None,
], ids=["not-ready", "other-commit", "dirty-tree", "empty", "missing"])
def test_release_gate_evidence_must_pass_on_the_candidate_commit(env, report):
    out = _promote(env, gate_report=report)
    assert out.status == ep.REFUSED
    assert any("release gate" in r for r in out.reasons)
    _untouched(env)


def test_every_reason_is_listed_not_just_the_first(env):
    out = _promote(env, signature=None, approved_sha256=None, gate_report=None)
    assert out.status == ep.REFUSED
    text = " ".join(out.reasons)
    assert "signature" in text and "approval" in text and "release gate" in text


def test_a_missing_candidate_file_is_refused(env):
    env["candidate"].unlink()
    out = _promote(env)
    assert out.status == ep.REFUSED
    _untouched(env)


def test_without_an_installed_exe_there_is_nothing_to_roll_back_to(env):
    env["live"].unlink()
    out = _promote(env)
    assert out.status == ep.REFUSED
    assert any("roll back" in r for r in out.reasons)
    assert env["runtime"].calls == []


# --- review repairs: stale prior, malformed manifest, unpersistable floor --

def test_a_failed_prior_copy_refuses_and_never_restores_a_stale_previous(env, monkeypatch):
    """A leftover .previous from an older attempt must never become the rollback."""
    prior = ep.previous_path(env["live"])
    prior.write_bytes(b"STALE-OLD")
    real_replace = ep.os.replace

    def failing_replace(src, dst, *a, **kw):
        if str(dst).endswith(".previous"):
            raise OSError("disk full")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(ep.os, "replace", failing_replace)
    out = _promote(env)
    assert out.status == ep.REFUSED
    assert any("prior" in r for r in out.reasons)
    _untouched(env)                          # live exe intact, nothing stopped


def test_rollback_restores_the_exe_that_was_live_not_a_stale_previous(env):
    ep.previous_path(env["live"]).write_bytes(b"STALE-OLD")
    env["runtime"] = Runtime(env["live"], healthy_for=(OLD,))
    out = _promote(env)
    assert out.status == ep.ROLLED_BACK
    assert env["live"].read_bytes() == OLD
    assert ep.previous_path(env["live"]).read_bytes() == OLD


@pytest.mark.parametrize("field,value", [
    ("exe_sha256", "\u00e9" * 64),
    ("exe_sha256", "ab" * 10),
    ("exe_sha256", "g" * 64),
    ("exe_sha256", 12345),
    ("commit", 123),
    ("commit", ""),
    ("key_id", 7),
    ("version", 7),
    ("sequence", "5"),
    ("sequence", True),
], ids=lambda v: repr(v)[:14])
def test_a_malformed_manifest_is_refused_with_reasons_not_an_exception(env, field, value):
    bad = dataclasses.replace(env["manifest"], **{field: value})
    out = _promote(env, manifest=bad)
    assert out.status == ep.REFUSED
    assert any(field in r for r in out.reasons), out.reasons
    _untouched(env)


def test_a_non_ascii_approval_is_a_refusal_not_an_exception(env):
    out = _promote(env, approved_sha256="\u00e9" * 64)
    assert out.status == ep.REFUSED
    _untouched(env)


def test_a_floor_that_cannot_be_persisted_rolls_the_swap_back(env, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"x")
    out = _promote(env, state_path=blocker / "state.json")
    assert out.status == ep.ROLLED_BACK
    assert any("update state" in r for r in out.reasons), out.reasons
    assert env["live"].read_bytes() == OLD
    assert env["runtime"].calls[-3:] == ["stop", "start:old", "probe"]


def test_the_floor_directory_is_created_when_missing(env, tmp_path):
    state = tmp_path / "fresh" / "dir" / "state.json"
    out = _promote(env, state_path=state)
    assert out.status == ep.PROMOTED, out.reasons
    assert json.loads(state.read_text(encoding="utf-8"))["sequence"] == 5
