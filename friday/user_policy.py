"""User-delegated permissions - versioned, audited, conversation-updatable
(Phase 5 of the vnext build).

The static tables in friday/policy.py answer "what does this tool class
require by default". This module answers the OTHER question: "what has
THIS user explicitly delegated or revoked", with three properties the
static tables cannot provide:

1. VERSIONED: every change is a new row; the current state is a fold.
   Nothing is ever destroyed, so "why is this allowed?" always has an
   answer with a timestamp and the user's own words.
2. AUDITED: grants/revocations/refusals are events, queryable.
3. BOUNDED: constitutional DENY classes cannot be granted here at all.
   A casual "I trust you, do whatever" cannot weaken the kernel -
   banking observation, malware creation, secret exposure stay denied
   regardless of what this store contains.

The engine consults: kernel DENY -> user policy (this) -> static default.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

#: Delegable permission domains - the action policy from the north star.
DOMAINS = (
    "research", "file_write", "local_build", "supplier_communication",
    "order_operations", "customer_service", "customer_email",
    "social_publish", "ad_campaign_within_envelope", "sub_agent_create",
    "deploy_website", "spend_money", "buy_domain", "budget_envelope_change",
    "delete_data", "workspace_access",
)

#: The kernel: never delegable through conversation, by construction.
#: These are refused at grant time - not stored-then-ignored, REFUSED,
#: so the audit trail shows the refusal.
CONSTITUTIONAL_DENY = frozenset({
    "banking_observation", "malware_creation", "secret_exposure",
    "guardrail_removal", "policy_self_weakening",
})

#: Default states per domain when the user has said nothing. AUTO acts
#: without asking; CONFIRM asks with amount/domain shown; the rest of
#: the domains default to CONFIRM until delegated.
_DEFAULTS = {
    "research": "AUTO",
    "file_write": "AUTO",
    "local_build": "AUTO",
    "supplier_communication": "AUTO",
    "order_operations": "AUTO",
    "customer_service": "AUTO",
    "customer_email": "AUTO",
    "social_publish": "AUTO",
    "ad_campaign_within_envelope": "AUTO",
    "sub_agent_create": "AUTO",
    "deploy_website": "CONFIRM",
    "spend_money": "CONFIRM",
    "buy_domain": "CONFIRM",
    "budget_envelope_change": "CONFIRM",
    "delete_data": "CONFIRM",
    "workspace_access": "CONFIRM",
}

_TABLE = """
CREATE TABLE IF NOT EXISTS user_policy_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain      TEXT NOT NULL,
    state       TEXT NOT NULL,            -- AUTO | CONFIRM | DENY | REFUSED
    reason      TEXT NOT NULL DEFAULT '', -- the user's own words
    actor       TEXT NOT NULL DEFAULT 'user',
    created_at  REAL NOT NULL
)
"""

#: Delegated spend envelopes (kernel: "SCOPED_DELEGATED"). Real-world
#: spending is never implied by general autonomy - Friday spends
#: autonomously ONLY inside an explicitly authorized envelope, and
#: exceeding it reverts to CONFIRM.
_ENVELOPE_TABLE = """
CREATE TABLE IF NOT EXISTS spend_envelopes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT NOT NULL,            -- provider/platform
    purpose     TEXT NOT NULL DEFAULT '', -- campaign/purpose
    daily_cap   REAL NOT NULL DEFAULT 0,
    total_cap   REAL NOT NULL DEFAULT 0,
    spent       REAL NOT NULL DEFAULT 0,
    currency    TEXT NOT NULL DEFAULT 'INR',
    expires_at  REAL NOT NULL DEFAULT 0,  -- 0 = no expiry
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL
)
"""



class UserPolicy:
    """The versioned delegation store + audit trail."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from friday.config import DATA_DIR
            db_path = Path(DATA_DIR) / "ada.sqlite3"
        self._path = str(db_path)
        with self._connect() as db:
            db.execute(_TABLE)
            db.execute(_ENVELOPE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    # -- changes (all audited) ---------------------------------------------

    def grant(self, domain: str, state: str, *, reason: str,
              actor: str = "user") -> dict:
        """
        Record a delegation change from EXPLICIT user language.

        Constitutional classes are refused and the refusal is itself an
        audit event - the trail shows someone tried.
        """
        state = state.upper()
        if domain in CONSTITUTIONAL_DENY:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO user_policy_events (domain, state, reason,"
                    " actor, created_at) VALUES (?,?,?,?,?)",
                    (domain, "REFUSED", f"constitutional class: {reason}",
                     actor, time.time()))
            return {"status": "refused", "domain": domain,
                    "note": "constitutional protections cannot be changed "
                            "in conversation - they require the kernel "
                            "approval flow"}
        if domain not in DOMAINS:
            return {"status": "failed",
                    "error": f"unknown permission domain {domain!r}",
                    "domains": list(DOMAINS)}
        if state not in ("AUTO", "CONFIRM", "DENY"):
            return {"status": "failed",
                    "error": f"state must be AUTO/CONFIRM/DENY, got {state!r}"}
        with self._connect() as db:
            db.execute(
                "INSERT INTO user_policy_events (domain, state, reason,"
                " actor, created_at) VALUES (?,?,?,?,?)",
                (domain, state, reason, actor, time.time()))
        return {"status": "succeeded", "domain": domain, "state": state}

    # -- reads --------------------------------------------------------------

    def state_of(self, domain: str) -> str:
        """Current effective state: latest user event, else default."""
        if domain in CONSTITUTIONAL_DENY:
            return "DENY"
        with self._connect() as db:
            row = db.execute(
                "SELECT state FROM user_policy_events WHERE domain = ?"
                " AND state != 'REFUSED' ORDER BY id DESC LIMIT 1",
                (domain,)).fetchone()
        if row:
            return row["state"]
        return _DEFAULTS.get(domain, "CONFIRM")

    def snapshot(self) -> dict:
        return {domain: self.state_of(domain) for domain in DOMAINS}

    def audit_trail(self, domain: str = "", limit: int = 50) -> list[dict]:
        with self._connect() as db:
            if domain:
                rows = db.execute(
                    "SELECT * FROM user_policy_events WHERE domain = ?"
                    " ORDER BY id DESC LIMIT ?", (domain, limit)).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM user_policy_events ORDER BY id DESC"
                    " LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # -- spend envelopes (SCOPED_DELEGATED) --------------------------------

    def authorize_envelope(self, *, platform: str, purpose: str = "",
                           daily_cap: float = 0, total_cap: float = 0,
                           currency: str = "INR",
                           expires_at: float = 0) -> dict:
        """Record a user-CONFIRMED spend envelope. Creating one is itself
        a CONFIRM-class action - callers gate it; this stores the grant
        and audits it."""
        with self._connect() as db:
            cur = db.execute(
                "INSERT INTO spend_envelopes (platform, purpose, daily_cap,"
                " total_cap, currency, expires_at, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (platform, purpose, daily_cap, total_cap, currency,
                 expires_at, time.time()))
            db.execute(
                "INSERT INTO user_policy_events (domain, state, reason,"
                " actor, created_at) VALUES (?,?,?,?,?)",
                ("budget_envelope_change", "AUTO",
                 f"envelope authorized: {platform}/{purpose} "
                 f"daily={daily_cap} total={total_cap} {currency}",
                 "user", time.time()))
            envelope_id = cur.lastrowid
        return {"status": "succeeded", "envelope_id": envelope_id}

    def can_spend(self, *, platform: str, amount: float,
                  purpose: str = "") -> dict:
        """
        The pre-spend gate. AUTO only when an active, unexpired envelope
        for this platform covers the amount within its remaining total
        cap (and per-transaction daily cap when set). Everything else is
        CONFIRM - never a silent overrun.
        """
        now = time.time()
        if amount <= 0:
            return {"decision": "CONFIRM",
                    "reason": "non-positive amounts are never auto-"
                              "approved (adversarial guard)"}
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM spend_envelopes WHERE platform = ? AND"
                " active = 1 ORDER BY id DESC", (platform,)).fetchall()
        for row in rows:
            r = dict(row)
            if r["expires_at"] and r["expires_at"] < now:
                continue
            if purpose and r["purpose"] and purpose != r["purpose"]:
                continue
            if r["daily_cap"] and amount > r["daily_cap"]:
                continue
            if r["total_cap"] and r["spent"] + amount > r["total_cap"]:
                continue
            return {"decision": "AUTO", "envelope_id": r["id"],
                    "remaining": (r["total_cap"] - r["spent"]
                                  if r["total_cap"] else None)}
        return {"decision": "CONFIRM",
                "reason": "no active envelope covers this spend - "
                          "confirmation with amount/platform required"}

    def record_spend(self, envelope_id: int, amount: float) -> dict:
        with self._connect() as db:
            db.execute("UPDATE spend_envelopes SET spent = spent + ?"
                       " WHERE id = ?", (amount, envelope_id))
            row = db.execute("SELECT spent, total_cap FROM spend_envelopes"
                             " WHERE id = ?", (envelope_id,)).fetchone()
        return {"status": "succeeded", "spent": row["spent"],
                "total_cap": row["total_cap"]}
