"""
The action evidence ledger: one record of what actually happened, across
every executor, that every spoken claim is checked against.

Why one ledger. Measured on the live master prompt (2026-09-20, room M1):

  FALSE POSITIVE  "I'm still running into the same path restriction" -
                  three turns in a row with ZERO file tool calls in any
                  of them. A failure was narrated that was never attempted.
  FALSE NEGATIVE  Hermes genuinely wrote jarvis-hello.py (on disk), and the
                  completion gate refused Friday's true "the file exists"
                  because Hermes's work was not in `_acted_this_turn`.
  STATE           "14 families, all READY" recited with no skill_list call.

Three symptoms, one cause: the evidence lived in five places
(`_acted_this_turn`, `_ran_this_turn`, the WorkRun log, objective tasks,
the skill ladder) and the gate could see one of them. So:

  every executor  -> ActionEvidence row here
  every claim     -> classified, then checked against the rows

A claim is a sentence in one of six classes (`classify_claims`):

  SUCCESS      "I created the file"           needs SUCCEEDED + verified
  COMPLETION   "Done." / "it's ready"         needs SUCCEEDED + verified
  FAILURE      "I couldn't write there"       needs an attempt that FAILED
  ATTEMPT      "I tried to delete it"         needs an attempt (any status)
  STATE        "all 14 families are READY"    needs a fresh authoritative snapshot
  PERCEPTION   "I'm looking at your screen"   needs a read that returned

`honesty.py` keeps the sentence grammar (what is a completion claim, a
perception claim, a negation); this module owns the evidence and the
verdict. The negation list there is deliberately NOT reused for FAILURE:
"I couldn't write there" is a negation to the completion gate (so it is
not a success claim) and it is exactly the FAILURE claim here.

Evidence is durable (SQLite, `data/action_evidence.sqlite3`) because
executors finish in other processes and after the turn: Hermes's
tool.complete arrives in the supervisor thread minutes after delegation,
and the objective worker runs in the MCP server. A turn's evidence is the
union of rows tagged with the turn AND rows the turn can legitimately
cite: anything that finished since the owner last spoke.

Verification is the executor's job, recorded here, never inferred:
`verified=1` means a read-back happened (file exists with the content,
the objective evidence row passed). An unverified SUCCEEDED row backs an
ATTEMPT claim, never a SUCCESS claim.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from friday import honesty
from friday.dbconn import ledger_connection

logger = logging.getLogger("friday.action_evidence")

# -- executors ---------------------------------------------------------------

FRIDAY_DIRECT = "FRIDAY_DIRECT"      # a capability the voice/browser brain called itself
MCP = "MCP"                          # the MCP server ran it (same thing, other process)
HERMES = "HERMES"                    # a Hermes WorkRun's tool call
CLAUDE_CODE = "CLAUDE_CODE"          # a Claude Code executor run
OBJECTIVE_WORKER = "OBJECTIVE_WORKER"  # the durable objective engine
BROWSER = "BROWSER"
DESKTOP = "DESKTOP"
SCHEDULER = "SCHEDULER"
EXECUTORS = (FRIDAY_DIRECT, MCP, HERMES, CLAUDE_CODE, OBJECTIVE_WORKER, BROWSER, DESKTOP, SCHEDULER)

# -- statuses (the contracts vocabulary, lowercase) --------------------------

STARTED, SUCCEEDED, FAILED, CANCELLED, PARTIAL = "started", "succeeded", "failed", "cancelled", "partial"
TERMINAL = (SUCCEEDED, FAILED, CANCELLED, PARTIAL)

# -- claim classes -----------------------------------------------------------

SUCCESS_CLAIM = "SUCCESS_CLAIM"
FAILURE_CLAIM = "FAILURE_CLAIM"
ATTEMPT_CLAIM = "ATTEMPT_CLAIM"
STATE_CLAIM = "STATE_CLAIM"
PERCEPTION_CLAIM = "PERCEPTION_CLAIM"
COMPLETION_CLAIM = "COMPLETION_CLAIM"
CLAIM_CLASSES = (SUCCESS_CLAIM, FAILURE_CLAIM, ATTEMPT_CLAIM, STATE_CLAIM, PERCEPTION_CLAIM, COMPLETION_CLAIM)

#: What is being read/written/deleted, as it matters to a claim: the
#: capability family. "files" backs "I created the file"; "hermes" does not
#: back "I deleted it" unless the Hermes tool call touched a file.
_FILE_TOOLS = ("files_", "files.", "write_file", "edit_file", "patch", "create_file", "delete_file",
               "read_file", "search_files", "write", "edit", "Write", "Edit", "MultiEdit")

DEFAULT_PATH = Path(os.getenv("FRIDAY_ACTION_EVIDENCE",
                              str(Path(__file__).resolve().parent.parent / "data" / "action_evidence.sqlite3")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS action_evidence (
    action_id     TEXT PRIMARY KEY,
    objective_id  TEXT NOT NULL DEFAULT '',
    turn_id       TEXT NOT NULL DEFAULT '',
    executor      TEXT NOT NULL,
    capability    TEXT NOT NULL,
    operation     TEXT NOT NULL DEFAULT '',
    arguments_summary TEXT NOT NULL DEFAULT '',
    target        TEXT NOT NULL DEFAULT '',
    started_at    REAL NOT NULL,
    finished_at   REAL,
    status        TEXT NOT NULL,
    result        TEXT NOT NULL DEFAULT '',
    evidence_type TEXT NOT NULL DEFAULT '',
    evidence_ref  TEXT NOT NULL DEFAULT '',
    verified      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_evidence_turn ON action_evidence(turn_id);
CREATE INDEX IF NOT EXISTS ix_evidence_finished ON action_evidence(finished_at);
CREATE TABLE IF NOT EXISTS state_snapshots (
    source        TEXT PRIMARY KEY,
    collected_at  REAL NOT NULL,
    generation    INTEGER NOT NULL DEFAULT 0,
    health        TEXT NOT NULL DEFAULT '',
    count         INTEGER NOT NULL DEFAULT 0,
    evidence      TEXT NOT NULL DEFAULT '',
    ttl_s         REAL NOT NULL DEFAULT 300,
    scope         TEXT NOT NULL DEFAULT '',
    authority_level TEXT NOT NULL DEFAULT 'observed'
);
"""

#: Columns added after the ledger first shipped, ALTERed in at open: `CREATE
#: TABLE IF NOT EXISTS` does nothing to an existing file, and the owner's live
#: ledger keeps its old shape until something migrates it.
_ADDED_SNAPSHOT_COLUMNS: dict[str, str] = {
    "scope": "TEXT NOT NULL DEFAULT ''",
    "authority_level": "TEXT NOT NULL DEFAULT 'observed'",
}


@dataclass(frozen=True)
class ActionEvidence:
    action_id: str
    executor: str
    capability: str
    status: str
    started_at: float
    finished_at: float | None = None
    objective_id: str = ""
    turn_id: str = ""
    operation: str = ""
    arguments_summary: str = ""
    target: str = ""
    result: str = ""
    evidence_type: str = ""
    evidence_ref: str = ""
    verified: bool = False

    @property
    def touches_files(self) -> bool:
        cap = self.capability
        return any(cap.startswith(p) or cap == p for p in _FILE_TOOLS)

    def describe(self) -> str:
        what = f"{self.executor}:{self.capability}"
        if self.target:
            what += f" {self.target}"
        tail = " (verified)" if self.verified else ""
        return f"{what} -> {self.status}{tail}"


#: Who may vouch for a state. A tool that READ the state (skill_list read the
#: ladder table; capability_families probed the fabric) is authoritative; a
#: static declaration (a manifest, a config listing what SHOULD exist) is
#: not - configured is not healthy, installed is not usable.
AUTHORITY_OBSERVED = "observed"        # the source read/probed the live state
AUTHORITY_DECLARED = "declared"        # a manifest/config/registry listing
AUTHORITY_INFERRED = "inferred"        # derived from other rows


@dataclass(frozen=True)
class CapabilityStateSnapshot:
    """An authoritative reading of some state (skills, families, providers)
    taken at `collected_at`. A STATE claim may cite it while it is fresh
    AND its authority is OBSERVED - a declared listing can never back
    "all fourteen are READY"."""
    source: str
    collected_at: float
    generation: int = 0
    health: str = ""
    count: int = 0
    evidence: str = ""
    ttl_s: float = 300.0
    #: what the snapshot covers ("skills", "fabric_families", "self_model", ...)
    scope: str = ""
    authority_level: str = AUTHORITY_OBSERVED

    def fresh(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) - self.collected_at <= self.ttl_s

    @property
    def authoritative(self) -> bool:
        return self.authority_level == AUTHORITY_OBSERVED


def _summ(arguments: dict | None) -> str:
    """Argument NAMES and any path/target, never content. The ledger is read
    back into model context and spoken; file bodies and secrets stay out."""
    if not arguments:
        return ""
    keys = sorted(str(k) for k in arguments)
    return ",".join(keys)[:200]


def target_of(arguments: dict | None) -> str:
    if not arguments:
        return ""
    for key in ("path", "file", "file_path", "filename", "target", "name", "url", "command"):
        value = arguments.get(key)
        if value:
            return str(value)[:240]
    return ""


class ActionEvidenceLedger:
    """SQLite-backed; one per process, shared file across processes."""

    _ADDED_COLUMNS: dict[str, str] = {}

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)
            have = {r[1] for r in db.execute("PRAGMA table_info(action_evidence)")}
            for col, ddl in self._ADDED_COLUMNS.items():
                if col not in have:
                    db.execute(f"ALTER TABLE action_evidence ADD COLUMN {col} {ddl}")
            have_snap = {r[1] for r in db.execute("PRAGMA table_info(state_snapshots)")}
            for col, ddl in _ADDED_SNAPSHOT_COLUMNS.items():
                if col not in have_snap:
                    db.execute(f"ALTER TABLE state_snapshots ADD COLUMN {col} {ddl}")

    def _connect(self):
        return ledger_connection(self.path)

    # -- writes ---------------------------------------------------------------

    def start(self, *, executor: str, capability: str, arguments: dict | None = None,
              turn_id: str = "", objective_id: str = "", operation: str = "",
              action_id: str | None = None) -> str:
        if executor not in EXECUTORS:
            raise ValueError(f"unknown executor {executor!r}; one of {EXECUTORS}")
        action_id = action_id or f"EV-{uuid.uuid4().hex[:12]}"
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO action_evidence (action_id, objective_id, turn_id, executor,"
                " capability, operation, arguments_summary, target, started_at, status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (action_id, objective_id, turn_id, executor, capability, operation,
                 _summ(arguments), target_of(arguments), time.time(), STARTED))
        return action_id

    def finish(self, action_id: str, *, status: str, result: str = "",
               evidence_type: str = "", evidence_ref: str = "", verified: bool = False) -> None:
        if status not in TERMINAL:
            raise ValueError(f"finish needs a terminal status, got {status!r}")
        if verified and not evidence_ref:
            raise ValueError("verified=True needs an evidence_ref (what was read back)")
        with self._connect() as db:
            db.execute(
                "UPDATE action_evidence SET status=?, result=?, evidence_type=?, evidence_ref=?,"
                " verified=?, finished_at=? WHERE action_id=?",
                (status, (result or "")[:600], evidence_type, (evidence_ref or "")[:400],
                 1 if verified else 0, time.time(), action_id))

    def record(self, *, executor: str, capability: str, status: str, arguments: dict | None = None,
               turn_id: str = "", objective_id: str = "", operation: str = "", result: str = "",
               evidence_type: str = "", evidence_ref: str = "", verified: bool = False,
               action_id: str | None = None) -> str:
        """start + finish in one call, for executors that report after the fact."""
        action_id = self.start(executor=executor, capability=capability, arguments=arguments,
                               turn_id=turn_id, objective_id=objective_id, operation=operation,
                               action_id=action_id)
        self.finish(action_id, status=status, result=result, evidence_type=evidence_type,
                    evidence_ref=evidence_ref, verified=verified)
        return action_id

    def snapshot(self, snap: CapabilityStateSnapshot) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO state_snapshots (source, collected_at, generation, health,"
                " count, evidence, ttl_s, scope, authority_level) VALUES (?,?,?,?,?,?,?,?,?)",
                (snap.source, snap.collected_at, snap.generation, snap.health, snap.count,
                 snap.evidence[:400], snap.ttl_s, snap.scope, snap.authority_level))

    # -- reads ----------------------------------------------------------------

    def _rows(self, sql: str, params: tuple) -> list[ActionEvidence]:
        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["verified"] = bool(d["verified"])
            out.append(ActionEvidence(**d))
        return out

    def for_turn(self, turn_id: str) -> list[ActionEvidence]:
        return self._rows("SELECT * FROM action_evidence WHERE turn_id=? ORDER BY started_at", (turn_id,))

    def for_objective(self, objective_id: str) -> list[ActionEvidence]:
        """Every row an objective / WorkRun / Claude run produced, in order."""
        return self._rows("SELECT * FROM action_evidence WHERE objective_id=? ORDER BY started_at",
                          (objective_id,))

    def since(self, t: float, *, executors: tuple[str, ...] | None = None) -> list[ActionEvidence]:
        """Rows that FINISHED at or after `t` - what a turn may cite from
        executors that completed while the owner was not speaking."""
        if executors:
            marks = ",".join("?" * len(executors))
            return self._rows(f"SELECT * FROM action_evidence WHERE finished_at>=? AND executor IN ({marks})"
                              " ORDER BY finished_at", (t, *executors))
        return self._rows("SELECT * FROM action_evidence WHERE finished_at>=? ORDER BY finished_at", (t,))

    def get(self, action_id: str) -> ActionEvidence | None:
        rows = self._rows("SELECT * FROM action_evidence WHERE action_id=?", (action_id,))
        return rows[0] if rows else None

    def snapshot_for(self, source: str) -> CapabilityStateSnapshot | None:
        with self._connect() as db:
            r = db.execute("SELECT * FROM state_snapshots WHERE source=?", (source,)).fetchone()
        return CapabilityStateSnapshot(**dict(r)) if r else None

    def evidence_before_turn(self, turn_started_at: float, *, limit: int = 200) -> list[ActionEvidence]:
        """Older rows - what a claim that explicitly speaks of an EARLIER
        time ("I created it earlier", "yesterday's run") may cite. Never
        offered to a present-tense claim (see `audit_claims(history=)`)."""
        return self._rows(
            "SELECT * FROM action_evidence WHERE finished_at IS NOT NULL AND finished_at<?"
            " ORDER BY finished_at DESC LIMIT ?", (turn_started_at, limit))

    def evidence_for_turn(self, turn_id: str, turn_started_at: float) -> list[ActionEvidence]:
        """The rows a reply in this turn may cite: tagged with the turn, or
        finished by ANY executor since the turn started (Hermes finishing
        mid-turn counts; Hermes finishing during the previous turn was
        already delivered as its own message and must not license a claim
        now - that is what `turn_started_at` bounds)."""
        seen: dict[str, ActionEvidence] = {}
        for row in self.for_turn(turn_id) + self.since(turn_started_at):
            seen[row.action_id] = row
        return sorted(seen.values(), key=lambda r: r.started_at)


_DEFAULT: ActionEvidenceLedger | None = None
_PINNED = False


def ledger() -> ActionEvidenceLedger:
    """The process's ledger. One explicitly installed with `reset_ledger(x)`
    stays installed (tests); otherwise it follows DEFAULT_PATH, which the
    test conftest points at a temp file."""
    global _DEFAULT
    if _DEFAULT is None or (not _PINNED and _DEFAULT.path != DEFAULT_PATH):
        _DEFAULT = ActionEvidenceLedger()
    return _DEFAULT


def reset_ledger(new: ActionEvidenceLedger | None = None) -> None:
    global _DEFAULT, _PINNED
    _DEFAULT = new
    _PINNED = new is not None


# ---------------------------------------------------------------------------
# Claim classification
# ---------------------------------------------------------------------------

#: "I couldn't / was unable to / am unable to <do>", "it failed", "the
#: write was blocked", "I ran into a restriction", "I'm still hitting the
#: same error". First person + an inability or a named obstacle. Not "I
#: can't see the future" (no action verb) and not "I cannot look through
#: the camera right now" when the camera is switched OFF - that one is a
#: STATE claim about a switch, handled by `_SWITCH_STATE`.
_FAILURE_RE = re.compile(
    r"\b(?:i(?:'m| am| was|'ve been| have been)?\s*(?:still\s+)?(?:unable to|not able to|couldn't|could not|"
    r"can't|cannot|wasn't able to|was not able to|failed to)\s+(?:\w+\s+){0,3}?"
    r"(?:write|create|read|delete|remove|open|save|edit|modify|access|reach|run|start|delegate|send|undo|"
    r"perform|complete|interact|find|locate)\b"
    r"|\b(?:it|that|the (?:write|read|delete|deletion|call|attempt|command|tool|objective|run)) (?:has )?failed\b"
    r"|\b(?:i(?:'m| am)? )?(?:still )?(?:running|ran|run) into (?:the same |an? )?(?:path |access |permission )?"
    r"(?:restriction|error|problem|issue|block|wall)"
    r"|\bpath restriction\b|\baccess restriction|\bpermission (?:denied|error)|\boutside (?:my|the) permitted\b"
    r"|\bwas (?:blocked|denied|refused|rejected)\b)",
    re.I)

#: "I tried to X", "I attempted X", "my attempt to X" - an attempt that may
#: or may not have failed; needs at least a started row.
_ATTEMPT_RE = re.compile(r"\b(?:i (?:tried|attempted|have tried|had tried)|my (?:previous |last |earlier )?attempt)\b", re.I)

#: A state claim about the fleet: N skills/families/providers/routes are
#: <state>. Needs a fresh authoritative snapshot, never memory.
_STATE_RE = re.compile(
    r"\b(?:(?:\d+|all|every|each|no|none of the|the|both|two|three|four|five|six|seven|eight|nine|ten|"
    r"one of|two of|some of|most of|half of|none of)\s+(?:\w+\s+){0,2}?(?:skills?|families|family|capabilit(?:y|ies)|"
    r"providers?|routes?|models?|tools?|objectives?|runs?)\b[^.]{0,80}?\b(?:are|is|remain|remains|sit|sits)\b"
    r"[^.]{0,30}?\b(?:ready|live|available|healthy|degraded|stale|unprobed|validated|candidates?|rejected|"
    r"deprecated|active|enabled|disabled|online|offline|running|idle|waiting|parked|blocked|"
    r"needs? revalidation|in (?:a |the )?\w+ state)\b"
    r"|\b(?:their|the) (?:ladder )?(?:states?|status) (?:is|are)\b"
    # "My live capability families include web, clock, memory ..." - a
    # recital of what is live/ready/available (probe B, D-15): a state
    # claim whether or not the word "are" appears.
    r"|\b(?:my|the|our) (?:live|ready|available|active|current)\s+(?:\w+\s+){0,2}?"
    r"(?:skills?|families|family|capabilit(?:y|ies)|providers?|tools?)\b[^.]{0,20}?\b(?:include|includes|are|cover|span)\b)",
    re.I)

#: "the camera is switched off / disabled / on" - a switch state Friday
#: owns; backed by a self_model row or a switch read.
_SWITCH_STATE = re.compile(r"\b(?:camera|screen capture|microphone|mic)\b[^.]{0,30}\b(?:switched|turned) (?:off|on)\b|\bdisabled\b", re.I)


@dataclass(frozen=True)
class Claim:
    sentence: str
    kind: str
    #: for FAILURE/ATTEMPT/SUCCESS: whether the sentence is about files
    about_files: bool = False
    #: The thing the sentence names - a filename, URL, app or host - lowered.
    #: A row must be about the SAME thing to back the claim ("I created
    #: bar.txt" is not backed by a write of foo.txt). "" = nothing named.
    target: str = ""
    #: The kind of deed the sentence names (write / read / delete / open /
    #: click / send / run / undo / ""), so a read never backs "I deleted".
    operation: str = ""
    #: True when the sentence explicitly speaks of an EARLIER turn ("earlier",
    #: "yesterday", "last time", "previously") - such a claim may cite rows
    #: older than this turn; a present-tense claim may not.
    historical: bool = False


#: Operation words in a claim -> the operation class a backing row must have.
_CLAIM_OPERATIONS = (
    ("delete", re.compile(r"\b(?:deleted|removed|erased|recycled|binned|deleting|delete|remove)\b", re.I)),
    ("undo", re.compile(r"\b(?:undid|undone|reverted|rolled back|undo|revert)\b", re.I)),
    ("write", re.compile(r"\b(?:created|wrote|written|saved|overwrote|overwritten|edited|appended|updated|"
                          r"create|write|save|overwrite|edit|append|update|put)\b", re.I)),
    ("read", re.compile(r"\b(?:read|reads|reading|opened and read|looked at|checked|inspected|listed)\b", re.I)),
    ("open", re.compile(r"\b(?:opened|launched|started up|brought up|open|launch)\b", re.I)),
    ("click", re.compile(r"\b(?:clicked|pressed|tapped|submitted|selected|typed into|click|press|submit)\b", re.I)),
    ("send", re.compile(r"\b(?:sent|emailed|messaged|posted|delegated|handed (?:it )?to|send|email)\b", re.I)),
    ("run", re.compile(r"\b(?:ran|executed|run|execute|launched the (?:command|script|test))\b", re.I)),
)

#: Capability id -> operation class of the row, for the same comparison.
_ROW_OPERATIONS = (
    ("delete", ("files_delete", "files_recycle", "files.delete", "files.recycle", "delete_file", "remove")),
    ("undo", ("files_undo", "files.undo", "undo")),
    ("write", ("files_create", "files_write", "files_edit", "files_move", "files.create", "files.write",
               "files.edit", "files.move", "write_file", "patch", "edit_file", "create_file", "files/write",
               "files/create", "files/edit")),
    ("read", ("files_read", "files_info", "files_list", "files_search", "files_wait", "files.read",
              "files.info", "files.list", "files.search", "read_file", "search_files", "files/read", "files/list")),
    ("open", ("apps_open", "open_in_browser", "browser_open", "apps.open", "desktop_step", "desktop/step")),
    ("click", ("browser_click", "browser_type", "browser_submit", "desktop_step", "desktop/step", "browser/click",
               "browser/type")),
    ("send", ("hermes_delegate", "hermes/delegate", "email_send", "message_send", "contacts_save", "contacts/save")),
    ("run", ("terminal", "execute_code", "automations_run", "objective_start", "hermes_delegate")),
)


def _claim_operation(sentence: str) -> str:
    for op, rx in _CLAIM_OPERATIONS:
        if rx.search(sentence):
            return op
    return ""


def row_operation(capability: str) -> str:
    cap = (capability or "").lower()
    for op, names in _ROW_OPERATIONS:
        if cap in names:
            return op
    # Unknown capability: fall back on its suffix verb so a new tool is
    # classified by its name, never left as "matches anything".
    tail = cap.rsplit("_", 1)[-1].rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    for op, rx in _CLAIM_OPERATIONS:
        if rx.fullmatch(tail):
            return op
    return ""


def _claim_target(sentence: str) -> str:
    """The named thing in a claim sentence, lowered: the first spoken
    literal (path / URL / email), else the app or control named after an
    open/launch/click verb ("the Start Menu", "submit"), else ""."""
    from friday import literals as LIT
    for lit in LIT.find(sentence):
        if lit.kind in (LIT.PATH, LIT.URL, LIT.EMAIL):
            return lit.value.lower()
    m = re.search(r"\b(?:opened|launched|open|launch|started|brought up|clicked|pressed|tapped|submitted)\s+"
                  r"(?:on\s+)?(?:the\s+)?((?:[A-Z][\w+-]*)(?:\s+[A-Z][\w+-]*)*|[a-z][\w-]+)"
                  r"(?=\s+(?:for|button|window|app|menu|link|field|tab)\b|[\s.,;!]*$|[.,;!])", sentence)
    if m:
        name = m.group(1).strip().lower()
        return "" if name in ("it", "that", "them", "up", "a", "an") else name
    return ""


_HISTORICAL = re.compile(r"\b(?:earlier|previously|before|yesterday|last (?:time|turn|night|week)|"
                         r"a (?:while|moment|minute) ago|this morning|in (?:a|the) (?:previous|earlier) (?:turn|session)|"
                         r"already (?:did|had))\b", re.I)


def _same_target(row_target: str, claim_target: str) -> bool:
    """A row is about the claim's thing when the names agree on the file /
    last path component / host - path prefixes and slashes differ between
    what the owner said and what the tool resolved. A row that recorded NO
    target (an executor whose arguments carried none) is compatible, not a
    mismatch: the operation check still applies, and it can never be the
    "success about something else" that negative control 5 forbids."""
    if not claim_target or not row_target:
        return True
    a = row_target.lower().replace("\\", "/").rstrip("/")
    b = claim_target.lower().replace("\\", "/").rstrip("/")
    if a == b or a.endswith("/" + b) or b.endswith("/" + a):
        return True
    if a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]:
        return True
    # A run/goal row whose target is the TEXT of the goal ("write hello.py to
    # the Desktop") is about the file that text names.
    return len(b) >= 4 and b in a


def classify_claims(text: str) -> list[Claim]:
    """Every claim-bearing sentence with its class. A sentence can be one
    class only; the order below is the precedence (a failure sentence
    contains negations that would otherwise hide it from every gate)."""
    out: list[Claim] = []
    perception = {s for s, _ in honesty.perception_claims(text)}

    def claim(sentence: str, kind: str, files: bool) -> Claim:
        return Claim(sentence, kind, files, target=_claim_target(sentence),
                     operation=_claim_operation(sentence) if kind in (SUCCESS_CLAIM, FAILURE_CLAIM, ATTEMPT_CLAIM) else "",
                     historical=bool(_HISTORICAL.search(sentence)))

    for sentence in honesty.sentences(text):
        low = sentence.lower()
        files = any(w in low for w in ("file", ".txt", ".py", ".md", ".json", "desktop", "folder", "directory", "write", "delete", "path"))
        if sentence.endswith("?"):
            continue
        if _FAILURE_RE.search(sentence) and not _SWITCH_STATE.search(sentence):
            out.append(claim(sentence, FAILURE_CLAIM, files))
            continue
        if _ATTEMPT_RE.search(sentence):
            out.append(claim(sentence, ATTEMPT_CLAIM, files))
            continue
        if sentence in perception:
            out.append(claim(sentence, PERCEPTION_CLAIM, files))
            continue
        if _STATE_RE.search(sentence):
            out.append(claim(sentence, STATE_CLAIM, files))
            continue
        if honesty.is_completion_claim(sentence):
            bare = re.match(r"^\s*(?:done|finished|complete|ready|all set)\b", sentence, re.I)
            out.append(claim(sentence, COMPLETION_CLAIM if bare else SUCCESS_CLAIM, files))
    return out


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Verdict:
    ok: bool
    unbacked: tuple[Claim, ...] = ()
    reasons: tuple[str, ...] = ()
    cited: tuple[str, ...] = field(default=())

    def __bool__(self) -> bool:
        return self.ok

    @property
    def first_kind(self) -> str:
        return self.unbacked[0].kind if self.unbacked else ""


def _correlates(row: ActionEvidence, claim: Claim) -> bool:
    """The row is about the claim's deed: same named thing (when the claim
    names one) and a compatible operation class (when the claim names one).
    A row with no target cannot back a claim that names a file - "I created
    bar.txt" needs a row that touched bar.txt, not any success."""
    if claim.target and not _same_target(row.target, claim.target):
        return False
    if claim.operation:
        rop = row_operation(row.capability)
        if rop and rop != claim.operation:
            # write/read are the only pair where one may stand for the
            # other's sibling: "I saved it" after files_edit is fine; a
            # READ never stands for a deed and a deed never stands for a read.
            return False
        if not rop and claim.operation in ("write", "delete", "undo") and not row.touches_files \
                and row.executor in (FRIDAY_DIRECT, MCP):
            return False
    return True


def _backs_success(row: ActionEvidence, claim: Claim) -> bool:
    """A SUCCEEDED row backs "I did it" when:

    * Friday's own call (FRIDAY_DIRECT / MCP): the runtime cannot construct
      an unverified success (`contracts.succeeded` requires a Verification),
      so SUCCEEDED is enough - but a READ never backs a deed;
    * another executor (HERMES / CLAUDE_CODE / OBJECTIVE_WORKER): only a row
      with a read-back (`verified`). A run finishing is not a file existing
      - the worker's tool rows carry the read-backs, the run row does not;
    * and in every case the row is about the SAME thing and the same kind
      of deed the sentence names (`_correlates`).
    """
    if row.status != SUCCEEDED:
        return False
    from friday import ownership
    if row.executor in (FRIDAY_DIRECT, MCP):
        if ownership.is_read_only(row.capability) and claim.operation != "read":
            return False                    # a read never backs "I created"
    elif not row.verified:
        return False
    if not _correlates(row, claim):
        return False
    if claim.about_files:
        return row.touches_files or row.executor in (HERMES, CLAUDE_CODE, OBJECTIVE_WORKER)
    return True


def _backs_failure(row: ActionEvidence, claim: Claim) -> bool:
    if row.status not in (FAILED, CANCELLED, PARTIAL):
        return False
    if not _correlates(row, claim):
        return False
    if claim.about_files:
        return row.touches_files or row.executor in (HERMES, CLAUDE_CODE, OBJECTIVE_WORKER)
    return True


def audit_claims(text: str, evidence: list[ActionEvidence], *,
                 snapshots: dict[str, CapabilityStateSnapshot] | None = None,
                 now: float | None = None, history: list[ActionEvidence] | None = None) -> Verdict:
    """Every claim in `text` against the rows this turn may cite.

    `evidence` is THIS turn's rows (tagged with the turn, or finished by any
    executor since it began). `history` is older rows; only a claim that
    explicitly speaks of an earlier time (`Claim.historical`) may cite
    them - a present-tense "I created bar.txt" is not backed by last
    turn's write, however recent (negative control 3).
    """
    claims = classify_claims(text)
    if not claims:
        return Verdict(ok=True, reasons=("no claims",))
    snapshots = snapshots or {}
    history = history or []
    unbacked: list[Claim] = []
    reasons: list[str] = []
    cited: list[str] = []
    ran = tuple(r.capability for r in evidence if r.status in TERMINAL)
    for claim in claims:
        pool = list(evidence) + (history if claim.historical else [])
        named = f" about {claim.target!r}" if claim.target else ""
        if claim.kind in (SUCCESS_CLAIM, COMPLETION_CLAIM):
            hits = [r for r in pool if _backs_success(r, claim)]
            if hits:
                cited.extend(r.describe() for r in hits[:3]); continue
            near = [r for r in pool if r.status == SUCCEEDED and not _correlates(r, claim)]
            attempted = [r for r in pool if r.status == SUCCEEDED and _correlates(r, claim) and not _backs_success(r, claim)]
            why = (f" ({len(near)} success(es) about something else)" if near
                   else f" ({len(attempted)} succeeded without read-back)" if attempted else " (nothing ran)")
            reasons.append(f"{claim.kind}{named}: nothing verified backs it" + why)
            unbacked.append(claim)
        elif claim.kind == FAILURE_CLAIM:
            hits = [r for r in pool if _backs_failure(r, claim)]
            if hits:
                cited.extend(r.describe() for r in hits[:3]); continue
            other = [r for r in pool if r.status in (FAILED, CANCELLED, PARTIAL) and not _correlates(r, claim)]
            reasons.append(f"FAILURE_CLAIM{named}: no matching attempt failed this turn"
                           + (f" ({len(other)} failure(s) about something else)" if other
                              else f" (ran: {sorted(set(ran))})" if ran else " (nothing ran)"))
            unbacked.append(claim)
        elif claim.kind == ATTEMPT_CLAIM:
            hits = [r for r in pool if _correlates(r, claim) and (not claim.about_files or r.touches_files
                    or r.executor in (HERMES, CLAUDE_CODE, OBJECTIVE_WORKER))]
            if hits:
                cited.extend(r.describe() for r in hits[:3]); continue
            reasons.append(f"ATTEMPT_CLAIM{named}: nothing matching was attempted this turn")
            unbacked.append(claim)
        elif claim.kind == STATE_CLAIM:
            fresh = [s for s in snapshots.values() if s.fresh(now) and s.authoritative]
            if fresh:
                cited.extend(f"snapshot:{s.source}@{int(s.collected_at)}" for s in fresh[:3]); continue
            stale = [s.source for s in snapshots.values() if not s.fresh(now)]
            weak = [s.source for s in snapshots.values() if s.fresh(now) and not s.authoritative]
            reasons.append("STATE_CLAIM: no fresh authoritative snapshot"
                           + (f" (stale: {stale})" if stale else "")
                           + (f" (not authoritative: {weak})" if weak else "")
                           + ("" if stale or weak else " (never read)"))
            unbacked.append(claim)
        elif claim.kind == PERCEPTION_CLAIM:
            if not honesty.unbacked_perception(claim.sentence, ran):
                continue
            reasons.append("PERCEPTION_CLAIM: no read this turn could have produced it")
            unbacked.append(claim)
    return Verdict(ok=not unbacked, unbacked=tuple(unbacked), reasons=tuple(reasons), cited=tuple(cited))


def in_process_rows(ran: tuple[str, ...] | list[str], acted: tuple[str, ...] | list[str],
                    *, turn_id: str = "") -> list[ActionEvidence]:
    """Synthetic rows from a process's own per-turn tuples (`_ran_this_turn`,
    `_acted_this_turn`), for the gap the durable ledger cannot cover: the
    ledger write itself failed (disk full, locked file). A capability in
    those tuples returned without raising in THIS process this turn, which
    is exactly what a SUCCEEDED FRIDAY_DIRECT row records. Used to fill in
    capabilities the ledger has no row for - never to override a row."""
    now = time.time()
    out = []
    for cap in list(ran or ()) + [c for c in (acted or ()) if c not in (ran or ())]:
        out.append(ActionEvidence(action_id=f"mem:{turn_id}:{cap}:{len(out)}", executor=FRIDAY_DIRECT,
                                  capability=cap, status=SUCCEEDED, started_at=now, finished_at=now,
                                  turn_id=turn_id, evidence_type="in_process", evidence_ref="",
                                  verified=False))
    return out


def merge_evidence(rows: list[ActionEvidence], fallback: list[ActionEvidence]) -> list[ActionEvidence]:
    """`rows` plus any fallback capability with no row of its own."""
    have = {r.capability for r in rows}
    return list(rows) + [f for f in fallback if f.capability not in have]


#: What to say instead, per class. Never invents progress.
CORRECTIONS = {
    SUCCESS_CLAIM: ("I have not actually done that, boss - I said it without doing it. "
                    "Nothing was touched. Ask me again and I will carry it out."),
    COMPLETION_CLAIM: ("I have not actually done that, boss - I said it without doing it. "
                       "Nothing was touched. Ask me again and I will carry it out."),
    FAILURE_CLAIM: ("Correction, boss - I did not actually try that this turn, so I have no "
                    "failure to report. Ask me again and I will attempt it and tell you what "
                    "really happens."),
    ATTEMPT_CLAIM: ("Correction, boss - I did not actually attempt that this turn. "
                    "Ask me again and I will."),
    STATE_CLAIM: ("I haven't checked their current state yet, boss - I was about to recite it "
                  "from memory. Ask me again and I will read it first."),
    PERCEPTION_CLAIM: ("I have not actually looked, boss - I described something I never checked. "
                       "Nothing was captured. Ask me again and I will take the snapshot first."),
}

#: Spoken when the audit ITSELF could not run (ledger unreadable, disk
#: full, a bug) and the sentence carried a claim. Fail-closed: a claim the
#: gate could not check is not delivered as if it had been checked. Plain
#: conversation - no claim at all - is never held for this.
AUDIT_UNAVAILABLE = ("I can't verify what I was about to tell you, boss - my action record is "
                     "unreadable right now, so I am not going to claim it. Ask me again in a moment.")


def correction_for(verdict: Verdict) -> str:
    return CORRECTIONS.get(verdict.first_kind, CORRECTIONS[SUCCESS_CLAIM])


def hold_if_claim(text: str) -> str | None:
    """What to say instead when the audit crashed: the AUDIT_UNAVAILABLE
    correction if `text` carries any claim, else None (let it through).
    `classify_claims` is pure text and shares no failure mode with the
    ledger, so it is safe to call from the audit's own except-path."""
    try:
        return AUDIT_UNAVAILABLE if classify_claims(text or "") else None
    except Exception:                                    # noqa: BLE001 - the classifier is the last line
        return AUDIT_UNAVAILABLE
