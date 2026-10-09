"""
Workspace identity: the tenant boundary private state is not allowed to cross.

FR-PRV-002 / architectural invariant 6. One FRIDAY install can hold several
workspaces - a user, a company, a brand, an agency's client - and the PRD's
acceptance criteria are absolute about what that must mean:

    a memory query in Workspace A cannot return Workspace B data;
    secrets are namespaced per workspace/user;
    browser profiles and private skills are never globally reused
    without explicit sharing.

The failure this module exists to prevent is not a missing feature; it is a
*silent* one. A single-tenant store that grows a second customer leaks by
default, and it leaks quietly, because every query still returns rows and
every row still looks plausible. So the design is deliberately unkind to
convenience:

1. **The word already meant something else.** `fsjail.DEFAULT_WORKSPACE` is
   the filesystem jail root - a directory. This module introduces the tenant
   sense of the word and never touches the jail. They are different concepts
   with the same English name, which is exactly why the collision is called
   out here rather than left for a reader to trip over.

2. **Every pre-existing row belongs to `default`.** A live install has tens
   of thousands of rows written before this boundary existed. Backfilling
   them to a made-up owner would be a lie; leaving them NULL would make them
   readable from every workspace. They are `default`, which is the single
   workspace that install always was.

3. **Scope is resolved once, explicitly, and never guessed.** `current()`
   reads the environment, then the recorded active workspace, then falls back
   to `default`. A caller that wants a different one passes it. Nothing
   infers a workspace from a filename, a subject line or model output.

4. **Locations are owned by the subsystem that uses them.** Where a
   workspace's secrets live is `friday.secret_broker.SecretBroker.home_for`;
   its private skills, `friday.skills.files.tier_roots`; its browser
   profile, `friday.browser.workspace_profile_dir`. This module answers only
   "who is asking", because two functions answering "where are this
   workspace's secrets" is how the answers drift.

5. **The declaration is a ratchet.** `SCOPED` names the private-state tables
   that must carry a workspace, and `UNSCOPED` names the ones that
   deliberately do not, each with a reason. `tests/test_workspace_isolation.py`
   asserts every table in the schema appears in exactly one of them, so a new
   table holding private state cannot be added without someone deciding which
   it is. This is the same discipline `friday/reachability.py` applies to dead
   code: the list is the argument, not the absence of a complaint.

What this module does NOT claim: cryptographic separation of one workspace's
data from another's on disk. The store is one SQLite file with a mandatory
scope column and a guard on every private read; secrets are separately
encrypted per workspace with their own key file. An attacker with the
database file and the key directory reads everything, as they would with any
local-first product. The PRD's word "cryptographically" is satisfied for
secrets and is honestly *not* satisfied for memory rows, and that is stated
here instead of implied away.
"""

from __future__ import annotations

import os
import re

#: The workspace every row written before this boundary existed belongs to,
#: and the one a single-workspace install keeps using forever.
DEFAULT_ID = "default"

#: Where an explicit choice comes from for the current process. Read at call
#: time, not import time: a worker may be handed a different workspace and
#: the next query must honour it.
ENV_VAR = "FRIDAY_WORKSPACE"

#: Ids become directory names (secrets, skills, browser profiles), so the
#: charset is constrained rather than sanitised. An id that cannot be a safe
#: path component is not an id.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: What a workspace can be. The kind is descriptive - it changes no rule -
#: but onboarding and the control room need to say "brand" rather than
#: "workspace 3" to a person. Validated by `Store.register_workspace`.
KINDS = ("user", "company", "brand", "agency", "project")


class InvalidWorkspace(ValueError):
    """An id that is not a legal workspace id."""


class UnknownWorkspace(KeyError):
    """A workspace that was never registered. Reading private state under an
    unregistered id is refused rather than returning an empty result, because
    an empty result reads as 'nothing here' when the truth is 'wrong key'."""


class CrossWorkspaceRefused(PermissionError):
    """A private read or write that would cross the boundary.

    Raised, never logged-and-continued. The PRD's trust metric is *zero*
    cross-workspace leakage, and a warning is not zero.
    """


def valid_id(workspace_id: str) -> str:
    """Return the id if it is legal, else raise. Used at every seam that
    turns an id into a path or a SQL parameter."""
    if not _ID_RE.match(workspace_id or ""):
        raise InvalidWorkspace(
            f"invalid workspace id {workspace_id!r}: lower-case letters, "
            "digits, dash and underscore only, starting with a letter or "
            "digit, max 64 characters")
    return workspace_id


def slug(label: str) -> str:
    """A label a person typed -> a legal id. Deterministic, so the same
    company name always maps to the same workspace."""
    out = re.sub(r"[^a-z0-9]+", "-", (label or "").strip().lower()).strip("-")
    out = out[:64].rstrip("-")
    return out or DEFAULT_ID


# --- the current workspace ---------------------------------------------------
#
# Process-local override, set by a worker that has been handed a workspace.
# Deliberately not a thread-local: FRIDAY's private-state access happens on
# the serialized store connection, and a per-thread answer would let two
# threads disagree about who they are working for.
_override: str | None = None


def current() -> str:
    """The workspace this process is working for.

    Order: an explicit override set by `use()`, then the environment, then the
    active workspace recorded in the store, then `default`. The store lookup
    is last and is tolerant of a missing database so that importing this
    module never requires one.
    """
    if _override is not None:
        return _override
    env = (os.environ.get(ENV_VAR) or "").strip()
    if env:
        return valid_id(env)
    recorded = _recorded_active()
    return recorded or DEFAULT_ID


def _recorded_active() -> str | None:
    # The configured database, like every other store in the process - not
    # `Store()`, which is always the live data/ada.sqlite3: that made every
    # test read (and migrate) the owner's database, and a test's answer
    # depend on which workspace was active there (V7-D17). Closed after the
    # one read.
    try:
        from friday.store import DEFAULT_DB, Store
        store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
    except Exception:
        # No database yet (first run, a test with no store, a packaged
        # process before bootstrap). Absence is not an error here; the
        # caller falls back to `default`.
        return None
    try:
        return store.active_workspace()
    finally:
        store.close()


class use:
    """Context manager and setter for the process's workspace.

        with workspace.use("crunchy-fashion"):
            store.recall_scoped(...)      # sees only that workspace

    Used by the objective engine when it picks up work belonging to a
    workspace, and by tests. Restores the previous value on exit even if the
    body raised, so a failure cannot leave the process pointed at someone
    else's data.
    """

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = valid_id(workspace_id)
        self._previous: str | None = None

    def __enter__(self) -> str:
        global _override
        self._previous = _override
        _override = self.workspace_id
        return self.workspace_id

    def __exit__(self, *exc) -> None:
        global _override
        _override = self._previous
        return None


def reset_override() -> None:
    """Drop any process override. For test teardown; production uses `use`."""
    global _override
    _override = None


def resolve(workspace_id: str | None) -> str:
    """The id a private-state call should use: what it was given, or the
    current one. Centralised so no call site invents its own default."""
    if workspace_id is None:
        return current()
    return valid_id(workspace_id)


def guard(requested: str | None, owner: str) -> None:
    """Refuse a private read or write that crosses the boundary.

    `owner` is the workspace a record actually belongs to; `requested` is the
    workspace asking. Equal is fine, anything else raises. This is the single
    place the refusal is defined so a test can prove it goes red.
    """
    asking = resolve(requested)
    if asking != owner:
        raise CrossWorkspaceRefused(
            f"workspace {asking!r} may not reach private state owned by "
            f"{owner!r}")


# --- the schema ratchet ------------------------------------------------------
#
# Every table in the store is either private state that must carry a
# workspace, or it is not - and the second case needs a reason a reader can
# argue with. `tests/test_workspace_isolation.py` asserts the schema contains
# nothing outside these two lists.

#: Private state. A row here belongs to exactly one workspace, and a read
#: from another workspace must not return it.
SCOPED = {
    "memories": "the durable facts - the PRD's named leak case",
    "observations": "raw episodes that memories are promoted from",
    "utterances": "what the user actually said, verbatim",
    "conversations": "a workspace's conversation history",
    "messages": "the turns inside those conversations",
    "contacts": "people, i.e. personal data",
    "projects": "a workspace's projects",
    "project_decisions": "decisions taken inside those projects",
    "requirements": "what a workspace asked for",
    "open_questions": "questions asked of a particular owner",
    "objective_runs": "what work was done for whom",
    "forged_skills": "capabilities built for one workspace (FR-CAP-003)",
    "product_records": "a workspace's catalogue rows",
    "product_runs": "runs over that catalogue",
    "automations": "a workspace's standing automations",
    "schedules": "a workspace's schedule entries",
    "reminders": "a workspace's reminders",
}

#: Deliberately global. Each entry is an argument, not an omission.
UNSCOPED = {
    "artifacts": "child rows of `runs`; reached only through a scoped run",
    "runs": "turn mechanics; the objective_runs row above carries the owner",
    "run_events": "child rows of `runs`",
    "run_tasks": "child rows of `runs`",
    "run_task_attempts": "child rows of `run_tasks`",
    "run_checkpoints": "child rows of `runs`",
    "run_controls": "child rows of `runs`",
    "run_narrations": "child rows of `runs`",
    "run_portions": "child rows of `runs`",
    "run_wakes": "child rows of `runs`",
    "tool_results": "child rows of `runs`",
    "objective_events": "child rows of `objective_runs`",
    "objective_tasks": "child rows of `objective_runs`",
    "objective_deliveries": "child rows of `objective_runs`",
    "assumption_register": "child rows of `objective_runs`",
    "automation_runs": "child rows of `automations`",
    "automation_executions": "child rows of `automation_runs`",
    "schedule_runs": "child rows of `schedules`",
    "executor_runs": "child rows of an objective run",
    "contradictions": "keyed by memory subject; resolved with the scoped row",
    "promotions": "FRIDAY's own self-development history, not a tenant's",
    "forged_skill_history": "child rows of `forged_skills`",
    "shadow_predictions": "FRIDAY's own routing calibration, no tenant data",
    "confirmations": "a yes to one action, bound to the process that asked "
                     "and to a run; only a fingerprint, no tenant data",
    "network_grants": "child rows of `objective_runs`, which carries the owner; "
                      "hosts and expiry only",
    "stop_latch": "the machine-wide emergency stop; one row, no tenant data",
    "routing_corrections": "FRIDAY's own routing calibration",
    "fabric_outcomes": "provider health, a property of the install",
    "pending_power": "this machine's power state",
    "workspaces": "the registry itself",
}
