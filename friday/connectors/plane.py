"""
ConnectorControlPlane — Jarvis administers Hermes so the boss never does.

The product behavior (boss-decided): "Jarvis, connect Claude" and the ONLY
human step is the identity/secret action itself. Everything else - provider
discovery, auth-type selection, credential storage, profile configuration,
health verification, resuming the objective that was waiting - is Friday's
job through this plane.

Design rules, each of which cost something to learn:

* DISCOVER, never memorize. The provider list comes from the Hermes
  gateway's own `/api/model/options` (slug, auth_type, authenticated,
  key_env, models). A provider plugin installed six months from now appears
  here with zero Friday code change. No `if anthropic:` branches.
* The model NEVER sees a secret. API keys go through a dedicated secure
  entry process (friday/connectors/secure_entry.py) straight into Windows
  Credential Manager; what returns to the tool result - and therefore to
  model context - is an opaque reference like `wincred:hermes/FIREWORKS`.
  No plaintext in .env, no Notepad scratch file. The existing
  SecretBroker's Fernet vault remains for non-provider secrets; provider
  credentials use the OS keystore per Microsoft's guidance.
* Auth type drives the flow. `api_key` -> secure entry window;
  `oauth_device_code`/`oauth_external` -> the provider's official flow
  (surfaced as ONE human step); already-authenticated -> straight to
  verify. The auth taxonomy is Hermes's own, read from the registry.
* Missing credentials are PROVIDER_AUTH (USER_REQUIRED), never
  CONNECTIVITY - the live RC1.1 lesson. A connector repair is a
  recoverable sub-objective: the parent ObjectiveRun parks WAITING at its
  auth boundary and resumes when `connector_verify` proves the credential
  works, without the boss repeating the original request.
* The CLI (`hermes model`, `hermes auth`) stays as the admin/diagnostic
  fallback. It is not the customer UX and this module never requires it.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("friday-agent.connectors")

#: Where the gateway's API server answers. Read at call time, not import
#: time, so tests and multi-profile setups can point elsewhere.
def _api_base() -> str:
    return os.environ.get("HERMES_API_BASE", "http://127.0.0.1:8642")


def _api_key() -> str:
    """The gateway API key, from env or the friday profile's .env.

    This is a LOCAL service credential for talking to the user's own
    gateway - not a provider secret - and it never enters a tool result.
    """
    key = os.environ.get("API_SERVER_KEY", "")
    if key:
        return key
    profile_env = Path(os.environ.get(
        "HERMES_PROFILE_ENV", r"D:\hermes\profiles\friday\.env"))
    try:
        for line in profile_env.read_text(encoding="utf-8").splitlines():
            if line.startswith("API_SERVER_KEY="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _api(path: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
    """One authenticated call to the gateway API server."""
    request = urllib.request.Request(
        f"{_api_base()}{path}",
        method="POST" if payload is not None else "GET",
        headers={"Authorization": f"Bearer {_api_key()}",
                 "Content-Type": "application/json"},
        data=json.dumps(payload).encode() if payload is not None else None)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Registry: what CAN be connected (discovered, not memorized)
# ---------------------------------------------------------------------------


@dataclass
class Connector:
    """One connectable thing, in capability terms the model may see."""

    id: str
    name: str
    kind: str                      # AI_MODEL for provider registry entries
    auth_type: str                 # api_key / oauth_device_code / ...
    authenticated: bool
    key_env: str = ""              # env var name only - never a value
    models: tuple = ()
    is_current: bool = False
    note: str = ""

    def describe(self) -> dict:
        return {
            "connector": self.id, "name": self.name, "kind": self.kind,
            "auth_type": self.auth_type,
            "authenticated": self.authenticated,
            "is_current": self.is_current,
            "models": list(self.models)[:12],
            "human_step": self.human_step(),
            "note": self.note,
        }

    def human_step(self) -> str:
        """The one thing only the boss can do, stated plainly."""
        if self.authenticated:
            return "none - already authenticated"
        if self.auth_type == "api_key":
            return ("paste the API key into the secure entry window "
                    "(Friday cannot read it)")
        if self.auth_type.startswith("oauth"):
            return "complete the provider's official sign-in when it opens"
        if self.auth_type in ("external_process", "subscription_cli"):
            return "complete the provider CLI's own login once"
        return f"authenticate ({self.auth_type})"


def discover() -> list[Connector]:
    """The live provider registry from the gateway - AI_MODEL connectors.

    Deliberately a thin projection of Hermes's own metadata: when a new
    provider plugin registers itself, it appears here without any change
    in Friday.
    """
    options = _api("/api/model/options")
    found: list[Connector] = []
    for provider in options.get("providers", []):
        found.append(Connector(
            id=str(provider.get("slug") or ""),
            name=str(provider.get("name") or provider.get("slug") or ""),
            kind="AI_MODEL",
            auth_type=str(provider.get("auth_type") or "api_key"),
            authenticated=bool(provider.get("authenticated")),
            key_env=str(provider.get("key_env") or ""),
            models=tuple(
                str(m.get("id") or m) if isinstance(m, dict) else str(m)
                for m in (provider.get("models") or [])[:40]),
            is_current=bool(provider.get("is_current")),
            note=str(provider.get("warning") or ""),
        ))
    return [c for c in found if c.id]


def find(connector_id: str) -> Connector | None:
    wanted = (connector_id or "").strip().lower()
    for connector in discover():
        if connector.id.lower() == wanted or connector.name.lower() == wanted:
            return connector
    return None


# ---------------------------------------------------------------------------
# Durable connector state (metadata + opaque refs ONLY)
# ---------------------------------------------------------------------------


_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS connectors (
    connector_id  TEXT PRIMARY KEY,
    kind          TEXT NOT NULL DEFAULT 'AI_MODEL',
    status        TEXT NOT NULL DEFAULT 'DISCONNECTED',
    auth_type     TEXT NOT NULL DEFAULT '',
    default_model TEXT NOT NULL DEFAULT '',
    credential_ref TEXT NOT NULL DEFAULT '',
    last_verified REAL NOT NULL DEFAULT 0,
    health        TEXT NOT NULL DEFAULT '',
    detail        TEXT NOT NULL DEFAULT ''
)
"""

#: A ref is `scheme:location` - e.g. `wincred:hermes/FIREWORKS_API_KEY` or
#: `hermes-auth:anthropic` (credential lives in Hermes's own auth store).
#: The VALUE never appears anywhere in this table.


class ConnectorState:
    """Durable connector metadata in the same SQLite Friday already uses."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        import sqlite3

        if db_path is None:
            from friday.config import DATA_DIR
            db_path = Path(DATA_DIR) / "ada.sqlite3"
        self._path = str(db_path)
        self._sqlite3 = sqlite3
        with self._connect() as db:
            db.execute(_STATE_SCHEMA)

    def _connect(self):
        from friday.dbconn import ledger_connection
        return ledger_connection(self._path, row_factory=self._sqlite3.Row)

    def upsert(self, connector_id: str, **fields) -> None:
        allowed = {"kind", "status", "auth_type", "default_model",
                   "credential_ref", "last_verified", "health", "detail"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown connector fields: {sorted(unknown)}")
        with self._connect() as db:
            db.execute(
                "INSERT INTO connectors (connector_id) VALUES (?)"
                " ON CONFLICT(connector_id) DO NOTHING", (connector_id,))
            if fields:
                sets = ", ".join(f"{k} = ?" for k in fields)
                db.execute(
                    f"UPDATE connectors SET {sets} WHERE connector_id = ?",
                    (*fields.values(), connector_id))

    def get(self, connector_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM connectors WHERE connector_id = ?",
                (connector_id,)).fetchone()
        return dict(row) if row else None

    def all(self) -> list[dict]:
        with self._connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM connectors ORDER BY connector_id")]


# ---------------------------------------------------------------------------
# The control plane
# ---------------------------------------------------------------------------


@dataclass
class FlowStep:
    """What happens next, in words Friday can speak."""

    action: str            # done / human_step / verify / error
    say: str
    detail: dict = field(default_factory=dict)


class ConnectorControlPlane:
    """Semantic operations Friday reasons over. No provider branches."""

    def __init__(self, state: ConnectorState | None = None) -> None:
        self.state = state or ConnectorState()

    # -- discovery ---------------------------------------------------------

    def discover_connectors(self) -> list[dict]:
        return [c.describe() for c in discover()]

    def describe_connector(self, connector_id: str) -> dict:
        connector = find(connector_id)
        if connector is None:
            known = ", ".join(c.id for c in discover())
            return {"status": "unknown_connector",
                    "known": known,
                    "say": f"I don't see {connector_id!r} in Hermes. "
                           f"Installed providers: {known}."}
        record = self.state.get(connector.id) or {}
        described = connector.describe()
        described["stored_state"] = {
            k: record.get(k) for k in
            ("status", "default_model", "credential_ref", "last_verified",
             "health") if record}
        return described

    # -- connection --------------------------------------------------------

    def begin_connection(self, connector_id: str,
                         model: str = "") -> FlowStep:
        """Choose and start the correct flow from the auth type."""
        connector = find(connector_id)
        if connector is None:
            return FlowStep("error", self.describe_connector(
                connector_id)["say"])

        if connector.authenticated:
            return self._activate(connector, model)

        if connector.auth_type == "api_key":
            from friday.connectors import secure_entry

            outcome = secure_entry.request_secret(
                title=f"Connect {connector.name}",
                credential_name=connector.key_env
                or f"{connector.id.upper()}_API_KEY")
            if outcome.get("status") != "stored":
                return FlowStep(
                    "human_step",
                    f"The secure entry window for {connector.name} was "
                    f"closed without a key. Say the word and I'll open "
                    f"it again.",
                    outcome)
            self.state.upsert(
                connector.id, kind=connector.kind, status="CREDENTIAL_SET",
                auth_type=connector.auth_type,
                credential_ref=outcome["credential_ref"])
            return self._activate(connector, model)

        if connector.auth_type.startswith("oauth"):
            # The provider's own flow is the identity boundary. Hermes owns
            # the OAuth machinery; the boss's only step is the sign-in - and
            # the sign-in surface is opened for him when it can be, so the
            # step is "look at the window", not "go and find the page".
            launched = self._launch_auth_surface(connector)
            self.state.upsert(
                connector.id, kind=connector.kind,
                status=("AUTH_IN_PROGRESS" if launched.get("launched")
                        else "AUTH_REQUIRED"),
                auth_type=connector.auth_type,
                credential_ref=f"hermes-auth:{connector.id}")
            if launched.get("launched"):
                say = (f"{connector.name} needs you to sign in once, boss - I've "
                       f"opened the official sign-in for you. "
                       f"{launched.get('hint', '')}").strip() + \
                    " I'll verify and carry on the moment it lands."
            else:
                say = (f"{connector.name} needs a one-time sign-in and I could "
                       f"not open its auth surface myself ("
                       f"{launched.get('note', 'no launchable surface')}). Run "
                       f"the provider's sign-in once; I'll pick it up.")
            return FlowStep(
                "human_step", say,
                {"connector": connector.id,
                 "auth_type": connector.auth_type,
                 **launched})

        return FlowStep(
            "human_step",
            f"{connector.name} authenticates via {connector.auth_type}; "
            f"complete its one-time login and I'll take it from there.",
            {"connector": connector.id, "auth_type": connector.auth_type})

    def _launch_auth_surface(self, connector: Connector) -> dict:
        """Open the provider's official auth flow - Jarvis's job, not the
        boss's. Metadata-driven: every oauth_* provider goes through
        Hermes's own `auth add --type oauth`, which runs the provider's
        device-code/browser flow and stores the credential in Hermes's
        auth store. No provider names appear here.

        The subprocess is DETACHED: the human sign-in may take minutes and
        must never block Friday's loop. Completion is detected by
        connector_verify re-reading the registry - the same seam the rest
        of the plane already trusts.
        """
        import subprocess

        hermes = os.environ.get(
            "HERMES_CLI", r"D:\hermes\hermes-agent\venv\Scripts\hermes.exe")
        if not Path(hermes).exists():
            return {"launched": False,
                    "note": "hermes CLI not found for auth launch"}
        try:
            process = subprocess.Popen(
                [hermes, "auth", "add", connector.id, "--type", "oauth"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess,
                                      "CREATE_NEW_PROCESS_GROUP", 0))
        except OSError as exc:
            return {"launched": False, "note": f"launch failed: {exc}"}
        return {"launched": True, "auth_pid": process.pid,
                "hint": "Your browser (or a device-code page) is opening."}

    def _activate(self, connector: Connector, model: str) -> FlowStep:
        """Select provider/model for the profile and verify for real."""
        chosen = model or (connector.models[0] if connector.models else "")
        try:
            self._set_profile_model(connector.id, chosen)
        except Exception as exc:                             # noqa: BLE001
            return FlowStep(
                "error",
                f"Hermes would not switch to {connector.name}: "
                f"{type(exc).__name__}. Its credential may need repair.",
                {"connector": connector.id, "error": str(exc)[:200]})
        return self.verify_connection(connector.id, expected_model=chosen)

    @staticmethod
    def _set_profile_model(provider: str, model: str) -> None:
        """Write model.default/model.provider in the profile config - the
        SAME keys `hermes model` and the dashboard's model/set write. The
        gateway API server exposes no model-set route (measured: 404), so
        the profile config IS the supported programmatic surface; new
        Hermes sessions (every delegation creates one) pick it up without
        a gateway restart.

        SURGICAL line edits, not a yaml round-trip: safe_dump strips every
        comment from the user's config (measured: 21 -> 0 on the live
        file). Only the two lines inside the `model:` block change; the
        rest of the file stays byte-identical.
        """
        import re

        config_path = Path(os.environ.get(
            "HERMES_PROFILE_CONFIG", r"D:\hermes\profiles\friday\config.yaml"))
        text = config_path.read_text(encoding="utf-8")

        def replace_key(body: str, key: str, value: str) -> str:
            # Match `  key: ...` inside the top-level `model:` block.
            pattern = re.compile(
                rf"(^model:\s*\n(?:[ \t]+.*\n)*?[ \t]+{key}:)[^\n]*",
                re.MULTILINE)
            if pattern.search(body):
                return pattern.sub(rf"\g<1> {value}", body, count=1)
            # Key absent inside the block: insert directly under `model:`.
            block = re.compile(r"^model:[ \t]*\n", re.MULTILINE)
            if block.search(body):
                return block.sub(f"model:\n  {key}: {value}\n", body,
                                 count=1)
            return body + f"\nmodel:\n  {key}: {value}\n"

        text = replace_key(text, "provider", provider)
        if model:
            text = replace_key(text, "default", model)
        config_path.write_text(text, encoding="utf-8")

    def verify_connection(self, connector_id: str,
                          expected_model: str = "") -> FlowStep:
        """Truth, not hope: re-read the registry and confirm."""
        connector = find(connector_id)
        if connector is None:
            return FlowStep("error", f"{connector_id} vanished from the "
                                     f"registry during verification.")
        healthy = connector.authenticated
        self.state.upsert(
            connector.id, kind=connector.kind,
            status="AUTHENTICATED" if healthy else "AUTH_REQUIRED",
            auth_type=connector.auth_type,
            default_model=expected_model,
            last_verified=time.time(),
            health="AUTH_OK" if healthy else "AUTH_REQUIRED")
        if healthy:
            what = expected_model or connector.name
            return FlowStep("done",
                            f"{what} is authenticated and selected, boss. "
                            f"Running the live check next.",
                            {"connector": connector.id,
                             "model": expected_model,
                             "state": "AUTHENTICATED"})
        return FlowStep(
            "human_step",
            f"{connector.name} still reports unauthenticated - the "
            f"sign-in has not landed yet. Finish it and I'll re-check.",
            {"connector": connector.id})

    def smoke_test(self, connector_id: str, model: str = '') -> FlowStep:
        """READY comes only from HERE: one REAL inference through the same
        HermesSupervisor path production delegations use, then effective
        provider/model checked against what was requested - a silent
        fallback is a FAIL, not a pass.
        """
        connector = find(connector_id)
        if connector is None:
            return FlowStep("error", f"unknown connector {connector_id!r}")
        record = self.state.get(connector.id) or {}
        chosen = model or record.get("default_model") or (
            connector.models[0] if connector.models else "")
        try:
            from friday.hermes_bridge import TaskBundle
            from friday.tools import hermes_control

            supervisor = hermes_control.supervisor()
            supervisor.start()
            bundle = TaskBundle(
                goal="reply with exactly: CONNECTOR SMOKE OK and nothing else. "
                     "Do not use any tools.")
            outcome = supervisor.delegate(bundle, wait=True,
                                          turn_timeout=120.0)
        except Exception as exc:
            self.state.upsert(connector.id, status="DEGRADED",
                              health=f"smoke error: {type(exc).__name__}")
            return FlowStep("error",
                            f"the live check against {connector.name} failed "
                            f"before inference: {type(exc).__name__}",
                            {"connector": connector.id})
        inner = (outcome.get("result")
                 if isinstance(outcome.get("result"), dict) else outcome)
        status = str(inner.get("status") or outcome.get("status") or "")
        effective_provider = str(inner.get("provider") or "")
        effective_model = str(inner.get("model") or "")
        ok = status == "COMPLETE"
        fallback = bool(inner.get("fallback_to"))
        model_matches = (not chosen or chosen in effective_model
                         or effective_model in chosen)
        if ok and not fallback and model_matches:
            self.state.upsert(
                connector.id, status="READY", default_model=chosen,
                last_verified=time.time(), health="INFERENCE_VERIFIED",
                detail=f"effective={effective_provider}/{effective_model}")
            return FlowStep(
                "done",
                f"{chosen or connector.name} is connected and verified with a "
                f"live response, boss.",
                {"connector": connector.id, "state": "READY",
                 "effective_provider": effective_provider,
                 "effective_model": effective_model})
        self.state.upsert(
            connector.id, status="DEGRADED",
            health=f"smoke status={status} fallback={fallback} "
                   f"model_match={model_matches}")
        return FlowStep(
            "error",
            f"{connector.name} authenticated but the live check did not pass "
            f"cleanly (status {status or 'unknown'}"
            + (", silent fallback detected" if fallback else "")
            + (", wrong effective model" if not model_matches else "")
            + "). I'd treat it as not ready.",
            {"connector": connector.id, "status": status,
             "fallback": fallback,
             "effective_provider": effective_provider,
             "effective_model": effective_model})

    # -- status / repair ---------------------------------------------------

    def status(self) -> dict:
        live = {c.id: c for c in discover()}
        rows = []
        for record in self.state.all():
            connector = live.get(record["connector_id"])
            rows.append({
                **{k: record[k] for k in (
                    "connector_id", "kind", "status", "default_model",
                    "credential_ref", "health")},
                "live_authenticated": (connector.authenticated
                                       if connector else None),
            })
        return {"connectors": rows,
                "registry_size": len(live)}

    def repair(self, connector_id: str) -> FlowStep:
        """The auto-repair entry: called when a run hits PROVIDER_AUTH.

        Inspect first; re-auth only if the credential is genuinely absent
        or stale. Never restarts anything - a credential problem is not a
        process problem.
        """
        connector = find(connector_id)
        if connector is None:
            return FlowStep("error",
                            f"cannot repair unknown connector "
                            f"{connector_id!r}")
        if connector.authenticated:
            # Credential exists and the registry trusts it: the earlier
            # failure was transient or profile-level. Verify and move on.
            return self.verify_connection(connector.id)
        return self.begin_connection(connector.id)
