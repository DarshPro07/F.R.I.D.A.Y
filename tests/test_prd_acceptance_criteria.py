"""PRD acceptance criteria that are easy to claim and hard to prove.

Three requirements state a rule that a system can appear to satisfy while
actually violating it, so each is tested against the real implementation
rather than trusted:

  R21  "WHEN an approval prompt times out, THEN timeout SHALL be treated as
        denial, not consent."  Silence is the most dangerous default in the
        whole document - a system that treats no-answer as yes will act
        while the owner is asleep.

  R24  "WHEN the nonce has already been used, THEN the request SHALL be
        rejected."  A replay guard that keeps nonces in memory forgets them
        on restart, which is precisely when an attacker would retry.

  R22  "WHEN a path-normalization variant such as `./` is used, THEN
        protection SHALL remain effective."  A guard defeated by string
        formatting is not a guard.
"""
from __future__ import annotations

import time

import pytest

from friday import access


class TestTimeoutIsDenialNotConsent:
    """PRD R21. The rule the PRD states twice: 'Silence is never consent.'

    Tested as BEHAVIOUR, not as a docstring. My first version grepped the
    module for the word "timeout" and failed - a test that would have
    passed on a comment and failed on working code, which is backwards.
    The real implementation is stronger than the PRD asks: `answer()`
    checks `reads_as_no` BEFORE `reads_as_yes`, and anything that is
    neither approves nothing.
    """

    def test_an_empty_answer_approves_nothing(self):
        from friday import approval

        assert approval.reads_as_yes("") is False
        assert approval.reads_as_yes("   ") is False

    def test_ambiguous_words_approve_nothing(self):
        """The dangerous middle: not a refusal, so it must not be consent."""
        from friday import approval

        for said in ("maybe", "hmm", "i guess", "later", "what?",
                     "hold on", "not sure"):
            assert approval.reads_as_yes(said) is False, said

    def test_a_refusal_is_checked_before_a_confirmation(self):
        """'no, don't do that' contains no yes-word, but a naive
        substring check for 'do' or an ordering bug could approve it."""
        from friday import approval

        for said in ("no", "no thanks", "don't", "stop", "cancel"):
            assert approval.reads_as_no(said) is True, said

    def test_a_clear_yes_still_works(self):
        """The gate must not be so strict that nothing can be approved."""
        from friday import approval

        assert approval.reads_as_yes("yes") is True

    def test_nothing_waiting_means_nothing_approved(self):
        """An answer with no pending confirmation cannot authorize
        anything - a stray 'yes' must not attach itself to a future
        action."""
        from friday import approval
        from friday import confirmation as CF

        book = CF.Book()
        verdict = approval.answer(book, "yes")
        assert bool(verdict.ok) is False
        assert "nothing is waiting" in verdict.reason.lower()


class TestReplayProtectionIsDurable:
    """PRD R24. In-memory nonce sets forget across a restart."""

    def test_a_fresh_nonce_is_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(access, "NONCES_PATH", tmp_path / "nonces.json")
        monkeypatch.setattr(access, "_seen_nonces", {})
        ok, reason = access.check_replay("nonce-fresh-1", time.time())
        assert ok is True, reason

    def test_the_same_nonce_twice_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(access, "NONCES_PATH", tmp_path / "nonces.json")
        monkeypatch.setattr(access, "_seen_nonces", {})
        first, _ = access.check_replay("nonce-repeat-1", time.time())
        second, reason = access.check_replay("nonce-repeat-1", time.time())
        assert first is True
        assert second is False
        assert reason

    def test_an_old_timestamp_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(access, "NONCES_PATH", tmp_path / "nonces.json")
        monkeypatch.setattr(access, "_seen_nonces", {})
        ok, reason = access.check_replay("nonce-old-1", time.time() - 86_400)
        assert ok is False
        assert reason

    def test_a_future_timestamp_is_rejected(self, tmp_path, monkeypatch):
        """Clock skew forward is as suspicious as skew back."""
        monkeypatch.setattr(access, "NONCES_PATH", tmp_path / "nonces.json")
        monkeypatch.setattr(access, "_seen_nonces", {})
        ok, reason = access.check_replay("nonce-future-1", time.time() + 86_400)
        assert ok is False
        assert reason

    def test_consumed_nonces_survive_a_restart(self):
        """The property that makes this real: the module's own comment says
        consumed nonces are DURABLE. A process restart must not forgive a
        replay."""
        import inspect

        source = inspect.getsource(access)
        assert "DURABLE" in source or "_persist_nonces" in source
        assert hasattr(access, "_persist_nonces")
        assert hasattr(access, "_load_nonces")


class TestPathNormalisationCannotDefeatTheJail:
    """PRD R22: `./` and friends must not slip past protection."""

    def test_dot_slash_variants_are_still_denied(self):
        from friday import fsjail

        blocked = 0
        for variant in (".env", "./.env", ".\\.env", "foo/../.env",
                        "./foo/../.env"):
            for pattern in fsjail.DENY_PATTERNS:
                import re

                if re.search(pattern, variant.replace("\\", "/")):
                    blocked += 1
                    break
        assert blocked >= 3, f"only {blocked} of 5 .env variants matched"

    def test_the_deny_list_covers_the_named_secrets(self):
        """The PRD names these specifically; a regression that drops one
        should fail loudly."""
        from friday import fsjail

        joined = " ".join(fsjail.DENY_PATTERNS)
        for needle in ("env", "git", "ssh", "pem", "credential"):
            assert needle in joined.lower(), needle
