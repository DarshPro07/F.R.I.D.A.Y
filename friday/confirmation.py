"""
A yes that means one thing.

`CONFIRM` says a human has to approve an action that autonomy will not approve
for them. This is what turns that into something safe, because the dangerous
shape is not "Friday asked" - it is:

    Friday: "Restart this computer?"
    boss:   "yes"
    Friday: terminates Spotify

A confirmation is therefore bound to the exact action, and the binding is
checked again at the moment of execution rather than at the moment of asking.
If the action, the target, the arguments or the run changed in between, the
yes does not apply to what is about to happen.

That matters here more than it would in most systems. Four migration batches
in a row, a request landed on a capability that merely shared vocabulary with
the intended one - "what's playing right now" reached a window arranger, "set
a reminder" reached the brightness control. A confirmation scoped to
POWER_ACTION rather than to *this restart* would be a routing bug away from
carrying out the wrong irreversible thing.

Three properties, and each one is a test:

    one action    the fingerprint covers action, target, arguments and run
    one use       consumed is permanent; there is no "approved for 10 minutes"
    one moment    it expires, because a yes from four minutes ago was about
                  the machine as it was four minutes ago
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

#: Long enough to say yes, short enough that the world has not moved on.
DEFAULT_SECONDS = 60.0

PENDING = "PENDING"
APPROVED = "APPROVED"
CONSUMED = "CONSUMED"
REFUSED = "REFUSED"
EXPIRED = "EXPIRED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(run_id: str, action: str, target: str,
                arguments: dict | None = None) -> str:
    """
    What this confirmation is *for*, as one comparable value.

    Arguments are sorted so that the same request phrased in a different key
    order fingerprints identically - and `default=str` so that a Path or a
    datetime in the arguments cannot make an authorised action unauthorisable
    by failing to serialise.
    """
    payload = json.dumps(
        {"run": run_id, "action": action, "target": target,
         "arguments": arguments or {}},
        sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class Confirmation:
    nonce: str
    run_id: str
    action: str
    target: str
    arguments: dict
    fingerprint: str
    question: str
    created_at: datetime
    expires_at: datetime
    state: str = PENDING

    def expired(self, now: datetime | None = None) -> bool:
        return (now or _now()) >= self.expires_at

    def to_dict(self) -> dict:
        return {"nonce": self.nonce, "action": self.action,
                "target": self.target, "question": self.question,
                "state": self.state,
                "expires_at": self.expires_at.isoformat(),
                "seconds_left": max(
                    0.0, (self.expires_at - _now()).total_seconds())}


@dataclass
class Verdict:
    ok: bool
    reason: str
    confirmation: Confirmation | None = None


@dataclass
class Book:
    """
    The confirmations in flight.

    Deliberately in memory and per-process. A confirmation that survived a
    restart would be a yes to a question asked of a machine that no longer
    exists in that state, and the whole point is that it stops applying the
    moment anything moves.
    """

    pending: dict[str, Confirmation] = field(default_factory=dict)

    def ask(self, run_id: str, action: str, target: str, question: str,
            arguments: dict | None = None,
            seconds: float = DEFAULT_SECONDS) -> Confirmation:
        """Record what is being asked, and hand back the handle for the yes."""
        now = _now()
        confirmation = Confirmation(
            nonce=secrets.token_urlsafe(12),
            run_id=run_id, action=action, target=target,
            arguments=dict(arguments or {}),
            fingerprint=fingerprint(run_id, action, target, arguments),
            question=question, created_at=now,
            expires_at=now + timedelta(seconds=seconds))
        self.pending[confirmation.nonce] = confirmation
        return confirmation

    def approve(self, nonce: str) -> Verdict:
        """
        The human said yes.

        Callers must only reach this from a real answer. There is deliberately
        no tool the model can call to approve its own request, which is the
        same reasoning that keeps `approve_for_session` out of the tool
        surface.
        """
        confirmation = self.pending.get(nonce)
        if confirmation is None:
            return Verdict(False, f"no confirmation {nonce!r} is pending")
        if confirmation.state == CONSUMED:
            return Verdict(False, "that confirmation has already been used")
        if confirmation.state == REFUSED:
            # A no is final. Without this, "no" followed by a second approval
            # path lets the action through - the state moved back to APPROVED
            # and consume was perfectly happy.
            return Verdict(False, "that was already refused; ask again")
        if confirmation.expired():
            confirmation.state = EXPIRED
            return Verdict(False, "that confirmation expired before the answer")
        confirmation.state = APPROVED
        return Verdict(True, "approved", confirmation)

    def refuse(self, nonce: str) -> Verdict:
        confirmation = self.pending.get(nonce)
        if confirmation is not None:
            confirmation.state = REFUSED
        return Verdict(False, "refused")

    def consume(self, nonce: str, *, run_id: str, action: str, target: str,
                arguments: dict | None = None) -> Verdict:
        """
        Spend the yes on exactly the thing it was given for.

        The fingerprint is recomputed here, from what is about to happen,
        rather than trusted from when the question was asked. That is the
        whole mechanism: a confirmation for restarting the machine cannot
        authorise terminating a process, however the two got confused between
        the question and the act.
        """
        confirmation = self.pending.get(nonce)
        if confirmation is None:
            return Verdict(False, f"no confirmation {nonce!r} is pending")
        if confirmation.state == CONSUMED:
            return Verdict(False, "that confirmation has already been used")
        if confirmation.state == REFUSED:
            return Verdict(False, "that was refused")
        if confirmation.state != APPROVED:
            return Verdict(False, "that has not been approved yet")
        if confirmation.expired():
            confirmation.state = EXPIRED
            return Verdict(False, "that confirmation has expired - ask again")

        wanted = fingerprint(run_id, action, target, arguments)
        if wanted != confirmation.fingerprint:
            # Say what was approved and what is being attempted. "Invalid
            # confirmation" sends somebody looking for a bug in the plumbing.
            return Verdict(
                False,
                f"that yes was for {confirmation.action} on "
                f"{confirmation.target!r}, and this is {action} on "
                f"{target!r} - ask again for this one")

        confirmation.state = CONSUMED
        return Verdict(True, "confirmed for this exact action", confirmation)

    def forget_expired(self) -> int:
        """Drop what can no longer be used. Returns how many went."""
        dead = [nonce for nonce, confirmation in self.pending.items()
                if confirmation.expired() or confirmation.state in
                (CONSUMED, REFUSED)]
        for nonce in dead:
            del self.pending[nonce]
        return len(dead)


#: The process-wide book. Reset in tests rather than shared between them.
book = Book()


def reset() -> None:
    book.pending.clear()
