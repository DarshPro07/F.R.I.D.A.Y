"""
The trust plane's three missing pieces (PRD v3.1 §4.8, §4.11, §11):

  1. Risk tiers R0-R4 (FR-059).  Every policy category maps to exactly one
     tier, deterministically, from a table - not from the model. The tier
     is what the approval card and the audit record say; the PolicyEngine's
     AUTO/ASK/CONFIRM/DENY decision stays authoritative per call.

  2. SecurityAuthorization (FR-061/062, §11.2).  Active security tooling
     lives in the AUTHORIZED_SECURITY namespace, disabled by default; an
     objective may use it only inside a contract that names the owner,
     the target scope, the allowed and prohibited actions, the intensity,
     the time window and an expiry. `target_guard()` is the deterministic
     check: out-of-scope host or action is refused whatever the tool asks.

  3. Append-only audit log (FR-065).  Every privileged action and every
     authorization decision is a row in `data/audit.sqlite3` with a
     sha256 chain over (previous hash + row). `verify_chain()` proves
     nothing was edited or removed in place. The log answers
     who / what / when / target / decision / result for every R2+ action.

Nothing here reads a model output. That is the point.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from friday import policy as P

logger = logging.getLogger("friday.trust")

# ---------------------------------------------------------------------------
# 1. Risk tiers
# ---------------------------------------------------------------------------

R0 = "R0"   # read
R1 = "R1"   # reversible local
R2 = "R2"   # external write
R3 = "R3"   # destructive / security
R4 = "R4"   # forbidden
TIERS = (R0, R1, R2, R3, R4)

TIER_HANDLING = {
    R0: "automatic within scope",
    R1: "automatic + audit",
    R2: "approval or explicit pre-authorization",
    R3: "exact-action approval + policy validation",
    R4: "blocked regardless of model request",
}

#: Category -> tier. Complete over policy.DEFAULT_POLICY (asserted by
#: tests/test_trust.py so a new category cannot arrive without a tier).
CATEGORY_TIER: dict[str, str] = {
    P.READ_LOCAL_SAFE: R0, P.MEMORY_READ: R0, P.WEB_SEARCH: R0,
    P.SCREEN_CAPTURE: R0, P.CAMERA_CAPTURE: R0, P.SCREEN_POINT: R0,
    P.OBJECTIVE_CONTROL: R0, P.POWER_CANCEL: R0,
    P.SAFE_APP_OPEN: R1, P.MEMORY_WRITE: R1, P.DEVICE_SETTING: R1,
    P.MEDIA_CONTROL: R1, P.REMINDER: R1, P.BROWSER_CONTROL: R1,
    P.CLIPBOARD_WRITE: R1, P.APP_CLOSE: R1, P.GRACEFUL_PROCESS_CLOSE: R1,
    P.FILE_WRITE: R1,
    P.BROWSER_AUTOMATION: R2, P.COMMAND_EXECUTION: R2,
    P.DELETE: R3, P.FORCE_PROCESS_TERMINATION: R3, P.SESSION_LOCK: R3,
    P.SLEEP: R3, P.HIBERNATE: R3, P.SHUTDOWN: R3, P.RESTART: R3,
    P.FORCED_SHUTDOWN: R3, P.POWER_ACTION: R3, P.DESKTOP_CONTROL: R3,
    P.SYSTEM_CRITICAL_PROCESS_TERMINATION: R4, P.SECRET_READ: R4,
    P.DESKTOP_CREDENTIAL_ENTRY: R4,
}

#: The AUTHORIZED_SECURITY namespace: fabric provider ids and tool prefixes
#: that are active recon / pentest. Disabled by default (FR-061); only
#: reachable through a SecurityAuthorization (FR-062).
SECURITY_NAMESPACE = frozenset({
    "security_skills", "strix_pentest",
    "nmap", "masscan", "naabu", "rustscan", "amass", "subfinder", "whatweb",
    "photon", "finalrecon", "spiderfoot", "recon-ng", "ivre",
})
SECURITY_PREFIXES = ("security_", "recon_", "pentest_", "scan_")


def tier_of_category(category: str | None) -> str:
    if category is None:
        return R2   # unknown is treated as an external write: ask
    return CATEGORY_TIER.get(category, R2)


def tier_of_tool(tool_id: str) -> str:
    """The tier of a tool id, via its policy category. Security-namespace
    tools are R3 by definition. Accepts both spellings of an MCP tool id
    (`files_delete` and `files.delete`), like `Capability.requires_approval`."""
    if is_security_capability(tool_id):
        return R3
    category = P.TOOL_CATEGORIES.get(tool_id)
    if category is None and "_" in tool_id:
        category = P.TOOL_CATEGORIES.get(tool_id.replace("_", ".", 1))
    return tier_of_category(category)


def is_security_capability(name: str) -> bool:
    low = (name or "").lower()
    return low in SECURITY_NAMESPACE or low.startswith(SECURITY_PREFIXES)


# ---------------------------------------------------------------------------
# 2. SecurityAuthorization
# ---------------------------------------------------------------------------

PASSIVE = "passive"
LOW_ACTIVE = "low_active"
HIGH_VOLUME = "high_volume"
EXPLOIT = "exploit"
INTENSITIES = (PASSIVE, LOW_ACTIVE, HIGH_VOLUME, EXPLOIT)


@dataclass
class SecurityAuthorization:
    """PRD §11.2 contract. Immutable once issued; `expires_at` is the only
    clock. `target_scope` entries are hostnames, exact IPs or CIDR ranges;
    a hostname matches itself and its subdomains."""

    owner_identity: str
    target_scope: tuple[str, ...]
    ownership_or_permission_basis: str
    allowed_actions: tuple[str, ...]
    prohibited_actions: tuple[str, ...] = ()
    max_scan_intensity: str = LOW_ACTIVE
    time_window: str = ""
    data_retention: str = "objective"
    expires_at: str = ""
    approval_id: str = ""
    objective_id: str = ""
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def __post_init__(self) -> None:
        if not self.owner_identity.strip():
            raise ValueError("owner_identity is required")
        if not self.target_scope:
            raise ValueError("target_scope must name at least one target")
        if not self.ownership_or_permission_basis.strip():
            raise ValueError("ownership_or_permission_basis is required: say why you may test this")
        if not self.allowed_actions:
            raise ValueError("allowed_actions must name at least one action")
        if self.max_scan_intensity not in INTENSITIES:
            raise ValueError(f"max_scan_intensity must be one of {INTENSITIES}")
        if not self.expires_at:
            raise ValueError("expires_at is required: an authorization without an end is not one")
        if not self.approval_id:
            raise ValueError("approval_id is required: the person's yes must be on record")
        for target in self.target_scope:
            t = target.strip().lower()
            if t in ("*", "0.0.0.0/0", "::/0", "any", "all"):
                raise ValueError(f"target_scope {target!r} is unbounded; name the hosts you own")

    def expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return True
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return now >= exp

    def covers_target(self, host: str) -> bool:
        h = (host or "").strip().lower().rstrip(".")
        if not h:
            return False
        for scope in self.target_scope:
            s = scope.strip().lower().rstrip(".")
            if "/" in s:
                try:
                    if ipaddress.ip_address(h) in ipaddress.ip_network(s, strict=False):
                        return True
                except ValueError:
                    continue
            elif h == s or h.endswith("." + s):
                return True
        return False

    def covers_action(self, action: str) -> bool:
        a = (action or "").strip().lower()
        if a in (p.lower() for p in self.prohibited_actions):
            return False
        return a in (x.lower() for x in self.allowed_actions) or "*" in self.allowed_actions

    def to_dict(self) -> dict:
        return asdict(self)


_INTENSITY_ORDER = {PASSIVE: 0, LOW_ACTIVE: 1, HIGH_VOLUME: 2, EXPLOIT: 3}


def target_guard(auth: SecurityAuthorization | None, *, host: str, action: str,
                 intensity: str = LOW_ACTIVE, now: datetime | None = None) -> dict:
    """The deterministic gate in front of every AUTHORIZED_SECURITY call.

    Returns {"allowed": bool, "reason": str}. Refuses when there is no
    contract, it has expired, the host is out of scope, the action is not
    allowed or is prohibited, or the requested intensity exceeds the
    contract's ceiling. A tool asking for something else is exactly the
    case FR-062's acceptance names, and it is refused here, not debated.
    """
    if auth is None:
        return {"allowed": False, "reason": "no SecurityAuthorization: the security "
                                            "workspace is disabled by default"}
    if auth.expired(now):
        return {"allowed": False, "reason": f"authorization expired at {auth.expires_at}"}
    if not auth.covers_target(host):
        return {"allowed": False,
                "reason": f"{host!r} is outside the authorized scope {list(auth.target_scope)}"}
    if not auth.covers_action(action):
        return {"allowed": False,
                "reason": f"action {action!r} is not in allowed_actions "
                          f"{list(auth.allowed_actions)} or is prohibited"}
    if _INTENSITY_ORDER.get(intensity, 99) > _INTENSITY_ORDER[auth.max_scan_intensity]:
        return {"allowed": False,
                "reason": f"intensity {intensity} exceeds the authorized ceiling "
                          f"{auth.max_scan_intensity}"}
    return {"allowed": True, "reason": f"within scope of approval {auth.approval_id}"}


# ---------------------------------------------------------------------------
# 3. Append-only audit log
# ---------------------------------------------------------------------------

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    tier        TEXT NOT NULL,
    decision    TEXT NOT NULL,
    result      TEXT NOT NULL DEFAULT '',
    objective_id TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '{}',
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);
"""

GENESIS = "0" * 64


class AuditLog:
    """Hash-chained, append-only. There is no update or delete method, and
    the chain makes any out-of-band edit detectable."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            from friday.config import DATA_DIR
            path = Path(DATA_DIR) / "audit.sqlite3"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # One long-lived connection in WAL mode: a fresh connect per
        # verdict cost ~8ms (measured, 300 decisions = 2.4s), which is a
        # tax on every policy decision. WAL keeps appends durable and
        # readers unblocked; `synchronous=NORMAL` is the documented
        # durable-on-checkpoint setting for WAL.
        self._conn = sqlite3.connect(str(self.path), timeout=10,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(AUDIT_SCHEMA)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            pass
        self._conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    @staticmethod
    def _digest(prev_hash: str, row: dict) -> str:
        payload = json.dumps({k: row[k] for k in (
            "at", "actor", "action", "target", "tier", "decision", "result",
            "objective_id", "detail")}, sort_keys=True, default=str)
        return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()

    def record(self, *, actor: str, action: str, tier: str, decision: str,
               target: str = "", result: str = "", objective_id: str = "",
               detail: dict | None = None) -> int:
        """Append one record. `detail` is redacted of anything that looks
        like a secret before it is written (FR-057)."""
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
        row = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "actor": actor, "action": action, "target": target[:500],
            "tier": tier, "decision": decision, "result": result[:500],
            "objective_id": objective_id,
            "detail": json.dumps(_redact(detail or {}), sort_keys=True, default=str)[:4000],
        }
        with self._lock, self._connect() as conn:
            last = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else GENESIS
            row["prev_hash"] = prev
            row["hash"] = self._digest(prev, row)
            cur = conn.execute(
                "INSERT INTO audit_log (at, actor, action, target, tier, decision, result, "
                "objective_id, detail, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                tuple(row[k] for k in ("at", "actor", "action", "target", "tier", "decision",
                                       "result", "objective_id", "detail", "prev_hash", "hash")))
            return int(cur.lastrowid)

    def query(self, *, objective_id: str = "", min_tier: str = R0,
              limit: int = 100) -> list[dict]:
        wanted = TIERS[TIERS.index(min_tier):]
        sql = "SELECT * FROM audit_log WHERE tier IN (%s)" % ",".join("?" for _ in wanted)
        params: list = list(wanted)
        if objective_id:
            sql += " AND objective_id=?"
            params.append(objective_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params)]
        for r in rows:
            try:
                r["detail"] = json.loads(r["detail"])
            except ValueError:
                pass
        return rows

    def verify_chain(self) -> dict:
        """Walk every row and recompute the chain. Any edited, removed or
        inserted row breaks it at a named id."""
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id")]
        prev = GENESIS
        for r in rows:
            if r["prev_hash"] != prev:
                return {"ok": False, "rows": len(rows), "broken_at": r["id"],
                        "reason": "prev_hash does not match the preceding row"}
            if self._digest(prev, r) != r["hash"]:
                return {"ok": False, "rows": len(rows), "broken_at": r["id"],
                        "reason": "row content does not match its hash"}
            prev = r["hash"]
        return {"ok": True, "rows": len(rows), "head": prev}


_SECRET_KEYS = ("password", "passwd", "secret", "token", "api_key", "apikey",
                "authorization", "cookie", "credential", "private_key")


def _redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if any(s in str(k).lower() for s in _SECRET_KEYS)
                    else _redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, str) and len(value) > 24 and _looks_like_token(value):
        return "[REDACTED]"
    return value


def _looks_like_token(s: str) -> bool:
    prefixes = ("sk-", "ghp_", "xoxb-", "AKIA", "AIza", "ya29.", "eyJ", "sk-ant-")
    return s.startswith(prefixes)


_AUDIT: AuditLog | None = None
_AUDIT_LOCK = threading.Lock()


def audit() -> AuditLog:
    global _AUDIT
    with _AUDIT_LOCK:
        if _AUDIT is None:
            _AUDIT = AuditLog()
        return _AUDIT


def configure_audit(new: AuditLog | None) -> None:
    """Test seam."""
    global _AUDIT
    with _AUDIT_LOCK:
        _AUDIT = new


def record_decision(tool_id: str, verdict, *, actor: str = "friday",
                    target: str = "", objective_id: str = "",
                    result: str = "", detail: dict | None = None) -> int | None:
    """Audit one policy verdict. R0 AUTO decisions are not written (they
    would be every file read); everything R1+ and every non-AUTO decision
    is. Never raises: audit failure is logged, not fatal."""
    tier = tier_of_tool(tool_id)
    decision = getattr(verdict, "decision", str(verdict))
    if tier == R0 and decision == P.AUTO:
        return None
    try:
        return audit().record(actor=actor, action=tool_id, target=target, tier=tier,
                              decision=str(decision), result=result,
                              objective_id=objective_id,
                              detail={"category": getattr(verdict, "category", ""),
                                      "reason": getattr(verdict, "reason", ""),
                                      **(detail or {})})
    except Exception:  # noqa: BLE001
        logger.exception("audit write failed for %s", tool_id)
        return None


def expiry_in(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")
