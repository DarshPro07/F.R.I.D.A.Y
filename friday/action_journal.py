"""ActionJournal: what Friday changed, what it was before, and how to put it
back - durable, so "undo that" works after the function that did it has
returned.

ML-09/10 (Mark-LIV brief §9-10). `reversible.attempt` already covers the
set-and-restore case inside one call (volume for a test, then back). What
it cannot do is answer a request that arrives later: the file was moved
ten minutes ago by a function that has since returned, and the only record
of the old path was a local variable. This module is that record.

Principles, each of which is a test:

* The before-state is CAPTURED, never guessed. If it cannot be captured
  reliably the entry is journaled with reversible=False and says why -
  a wrong "undo" is worse than none.
* Reversal is checked against the CURRENT state before it runs. If the
  destination has been replaced by something Friday did not write (hash
  differs), the undo is refused as a conflict - the same rule
  `reversible._restore` applies to a volume knob. Overwriting the user's
  later work to "restore" Friday's earlier state is not an undo.
* Reversal is verified by reading the state back, and the entry records
  the outcome. An undo that reports success without a read-back is the
  false-claim failure mode this whole PRD exists to remove.
* Before-content is kept in a bounded backup store, never in memory and
  never in the row itself; above the size cap the write is journaled as
  irreversible rather than holding a copy Friday cannot afford.
* Every entry carries the objective/run id so an objective can be rolled
  back as a group and nothing outside it is touched.

Scope in this change: the four mutating file operations (create, write,
edit, move) - the ones a person most often wants back. Copy and recycle
have their own reversal semantics (brief §10) and are listed as the next
extension in the docstring of `reverse`, not half-built here.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

#: Above this, a file's prior content is not copied into the backup store
#: and the overwrite is journaled irreversible. Large media is what this is
#: for: a 2 GB video "written" by an edit is not something to hold twice.
MAX_BACKUP_BYTES = int(os.getenv("FRIDAY_JOURNAL_MAX_BACKUP_BYTES", str(64 * 1024 * 1024)))

# operations
FILE_CREATE, FILE_WRITE, FILE_EDIT, FILE_MOVE = "file.create", "file.write", "file.edit", "file.move"

# entry states
RECORDED, REVERSED, REVERSAL_FAILED, CONFLICT, IRREVERSIBLE, EXPIRED = (
    "RECORDED", "REVERSED", "REVERSAL_FAILED", "CONFLICT", "IRREVERSIBLE", "EXPIRED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS action_journal (
    action_id     TEXT PRIMARY KEY,
    objective_id  TEXT NOT NULL DEFAULT '',
    run_id        TEXT NOT NULL DEFAULT '',
    group_id      TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    capability    TEXT NOT NULL,
    operation     TEXT NOT NULL,
    target        TEXT NOT NULL,
    before_json   TEXT NOT NULL DEFAULT '{}',
    after_json    TEXT NOT NULL DEFAULT '{}',
    reversible    INTEGER NOT NULL DEFAULT 0,
    reason        TEXT NOT NULL DEFAULT '',
    backup_path   TEXT NOT NULL DEFAULT '',
    state         TEXT NOT NULL DEFAULT 'RECORDED',
    reversed_at   REAL,
    evidence      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_action_journal_run ON action_journal(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_action_journal_obj ON action_journal(objective_id, created_at);
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_state(path: Path) -> dict:
    """What can be said about a path without reading it whole: exists,
    size, sha256 (bounded), mtime. sha is omitted above the cap - a state
    without a hash cannot be conflict-checked, and that is recorded."""
    if not path.exists():
        return {"exists": False}
    if path.is_dir():
        return {"exists": True, "is_dir": True}
    st = path.stat()
    out = {"exists": True, "size": st.st_size, "mtime": st.st_mtime}
    if st.st_size <= MAX_BACKUP_BYTES:
        out["sha256"] = _sha(path.read_bytes())
    return out


@dataclass
class Entry:
    action_id: str
    objective_id: str
    run_id: str
    group_id: str
    created_at: float
    capability: str
    operation: str
    target: str
    before: dict
    after: dict
    reversible: bool
    reason: str = ""
    backup_path: str = ""
    state: str = RECORDED
    reversed_at: float | None = None
    evidence: str = ""

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


@dataclass
class Reversal:
    action_id: str
    state: str                 # REVERSED | REVERSAL_FAILED | CONFLICT | IRREVERSIBLE
    evidence: str = ""
    verified: bool = False

    @property
    def ok(self) -> bool:
        return self.state == REVERSED and self.verified


# --------------------------------------------------------------------------
# the journal
# --------------------------------------------------------------------------

class ActionJournal:
    def __init__(self, path: str | Path | None = None, *, backups: str | Path | None = None) -> None:
        if path is None:
            from friday.config import DATA_DIR
            path = Path(DATA_DIR) / "action_journal.sqlite3"
        self.path = Path(path)
        self.backups = Path(backups) if backups else self.path.parent / "action_journal_backups"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.backups.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self):
        from friday.dbconn import ledger_connection
        return ledger_connection(self.path)

    # -- recording -----------------------------------------------------------

    def record(self, *, capability: str, operation: str, target: str, before: dict, after: dict,
               reversible: bool, reason: str = "", backup_path: str = "",
               objective_id: str = "", run_id: str = "", group_id: str = "") -> Entry:
        e = Entry(action_id=uuid4().hex[:16], objective_id=objective_id, run_id=run_id,
                  group_id=group_id or run_id, created_at=time.time(), capability=capability,
                  operation=operation, target=str(target), before=before, after=after,
                  reversible=reversible, reason=reason, backup_path=backup_path,
                  state=RECORDED if reversible else IRREVERSIBLE)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO action_journal (action_id, objective_id, run_id, group_id, created_at, "
                "capability, operation, target, before_json, after_json, reversible, reason, "
                "backup_path, state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (e.action_id, e.objective_id, e.run_id, e.group_id, e.created_at, e.capability,
                 e.operation, e.target, json.dumps(e.before, sort_keys=True),
                 json.dumps(e.after, sort_keys=True), int(e.reversible), e.reason,
                 e.backup_path, e.state))
        return e

    # -- file helpers: capture BEFORE the mutation, record AFTER -------------

    def before_file_write(self, target: Path) -> tuple[dict, str, bool, str]:
        """Capture a file's prior state ahead of an overwrite/edit/create.

        Returns (before_state, backup_path, reversible, reason). A file that
        did not exist is reversible by deletion (no backup needed). An
        existing file is backed up by content hash; above the cap it is not,
        and the change is irreversible - said so, not guessed."""
        target = Path(target)
        before = _file_state(target)
        if not before["exists"]:
            return before, "", True, "did not exist; undo = remove"
        if before.get("is_dir"):
            return before, "", False, "target is a directory"
        if "sha256" not in before:
            return before, "", False, f"prior content larger than the backup cap ({MAX_BACKUP_BYTES} bytes)"
        backup = self.backups / f"{before['sha256']}.bin"
        if not backup.exists():
            tmp = backup.with_suffix(".tmp")
            shutil.copyfile(target, tmp)
            os.replace(tmp, backup)
        return before, str(backup), True, ""

    def record_file_write(self, target: Path, before: dict, backup_path: str, reversible: bool,
                          reason: str, *, operation: str = FILE_WRITE, **ids) -> Entry:
        after = _file_state(Path(target))
        return self.record(capability="files", operation=operation, target=str(target),
                           before=before, after=after, reversible=reversible, reason=reason,
                           backup_path=backup_path, **ids)

    def record_file_move(self, source: Path, destination: Path, *, before_source: dict, **ids) -> Entry:
        """A move's before-state is the source's state captured BEFORE the
        move (the caller has it - the file is gone from there afterwards).
        Reversible iff the destination can be hashed for the conflict check."""
        after = _file_state(Path(destination))
        reversible = bool(after.get("exists")) and "sha256" in after
        reason = "" if reversible else "destination cannot be hashed for the conflict check"
        return self.record(capability="files", operation=FILE_MOVE, target=str(destination),
                           before={"source": str(source), **before_source},
                           after=after, reversible=reversible, reason=reason, **ids)

    # -- queries -------------------------------------------------------------

    def get(self, action_id: str) -> Entry | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM action_journal WHERE action_id=?", (action_id,)).fetchone()
        return self._entry(row) if row else None

    def recent(self, *, limit: int = 20, run_id: str = "", objective_id: str = "") -> list[Entry]:
        sql, args = "SELECT * FROM action_journal", []
        where = []
        if run_id:
            where.append("run_id=?"); args.append(run_id)
        if objective_id:
            where.append("objective_id=?"); args.append(objective_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(limit)
        with self._connect() as conn:
            return [self._entry(r) for r in conn.execute(sql, args)]

    def last_reversible(self, **scope) -> Entry | None:
        """The entry "undo that" means: the most recent one still RECORDED."""
        for e in self.recent(limit=200, **scope):
            if e.state == RECORDED and e.reversible:
                return e
        return None

    @staticmethod
    def _entry(row) -> Entry:
        d = dict(row)
        return Entry(action_id=d["action_id"], objective_id=d["objective_id"], run_id=d["run_id"],
                     group_id=d["group_id"], created_at=d["created_at"], capability=d["capability"],
                     operation=d["operation"], target=d["target"],
                     before=json.loads(d["before_json"] or "{}"), after=json.loads(d["after_json"] or "{}"),
                     reversible=bool(d["reversible"]), reason=d["reason"], backup_path=d["backup_path"],
                     state=d["state"], reversed_at=d["reversed_at"], evidence=d["evidence"])

    # -- reversal ------------------------------------------------------------

    def reverse(self, action_id: str) -> Reversal:
        """Put one action back, if it is still safe to.

        Safe means: the target is in the state Friday left it (hash matches
        `after`). Anything else is a CONFLICT and nothing is touched. After
        the reversal the target is read back and compared to `before`; only
        a match is REVERSED. The entry's state is updated either way.

        Not yet reversible here (next extension, brief §10): file.copy
        (remove the copy only if it is byte-identical to what Friday wrote)
        and file.recycle (restore from the bin, platform-specific).
        """
        e = self.get(action_id)
        if e is None:
            return Reversal(action_id, REVERSAL_FAILED, "no such action")
        if not e.reversible or e.state == IRREVERSIBLE:
            return self._finish(e, IRREVERSIBLE, e.reason or "recorded as irreversible")
        if e.state != RECORDED:
            return self._finish(e, e.state, f"already {e.state.lower()}", update=False)
        target = Path(e.target)
        current = _file_state(target)
        # conflict check: is it still what Friday left?
        if _differs(current, e.after):
            return self._finish(e, CONFLICT,
                                f"{target.name} is not as Friday left it (now {_brief(current)}, "
                                f"was {_brief(e.after)}); not touched")
        try:
            if e.operation in (FILE_CREATE, FILE_WRITE, FILE_EDIT):
                if not e.before.get("exists"):
                    target.unlink(missing_ok=True)
                else:
                    shutil.copyfile(e.backup_path, target)
            elif e.operation == FILE_MOVE:
                src = Path(e.before["source"])
                if src.exists():
                    return self._finish(e, CONFLICT, f"{src} exists again; not overwritten")
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(src))
                target = src
            else:
                return self._finish(e, IRREVERSIBLE, f"no reversal for {e.operation}")
        except OSError as exc:
            return self._finish(e, REVERSAL_FAILED, f"{type(exc).__name__}: {exc}")
        # read-back: is it now what it was before?
        restored = _file_state(target)
        expected = {k: v for k, v in e.before.items() if k in ("exists", "sha256", "size", "is_dir")}
        if e.operation == FILE_MOVE:
            expected = {k: v for k, v in e.before.items() if k in ("exists", "sha256", "size")}
        if _differs(restored, expected):
            return self._finish(e, REVERSAL_FAILED,
                                f"reversal ran but read-back is {_brief(restored)}, expected {_brief(expected)}")
        return self._finish(e, REVERSED, f"read back {_brief(restored)} == before", verified=True)

    def reverse_group(self, group_id: str) -> list[Reversal]:
        """Reverse every RECORDED entry in a group, newest first, stopping at
        the first conflict/failure so a half-undone group is visible rather
        than silently skipped over."""
        out = []
        with self._connect() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT action_id FROM action_journal WHERE group_id=? AND state='RECORDED' "
                "ORDER BY created_at DESC, rowid DESC", (group_id,))]
        for aid in ids:
            r = self.reverse(aid)
            out.append(r)
            if not r.ok:
                break
        return out

    def _finish(self, e: Entry, state: str, evidence: str, *, verified: bool = False,
                update: bool = True) -> Reversal:
        if update:
            with self._connect() as conn:
                conn.execute("UPDATE action_journal SET state=?, evidence=?, reversed_at=? WHERE action_id=?",
                             (state, evidence, time.time() if state == REVERSED else None, e.action_id))
        return Reversal(e.action_id, state, evidence, verified=verified)


def _differs(current: dict, expected: dict) -> bool:
    """Compare on what both sides know. Existence always; hash when both
    have one; size when both have one and no hash is available."""
    if bool(current.get("exists")) != bool(expected.get("exists")):
        return True
    if not expected.get("exists"):
        return False
    if "sha256" in current and "sha256" in expected:
        return current["sha256"] != expected["sha256"]
    if "size" in current and "size" in expected:
        return current["size"] != expected["size"]
    return False


def _brief(state: dict) -> str:
    if not state.get("exists"):
        return "absent"
    if state.get("is_dir"):
        return "a directory"
    return f"{state.get('size', '?')} bytes sha {str(state.get('sha256', '?'))[:8]}"
