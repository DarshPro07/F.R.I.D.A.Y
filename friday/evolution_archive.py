"""
The Evolution Archive: what FRIDAY learned from trying to improve itself.

EVO-002. Every self-evolution experiment leaves one durable record - the parent
and candidate versions, the hypothesis, the baseline and candidate measurements,
the evaluation evidence, the decision and the version to roll back to - and a
failed candidate is kept exactly like a promoted one, so FRIDAY does not pay to
rediscover an optimisation that already lost.

  * A rejected experiment is found, by a canonical fingerprint over (parent
    version, hypothesis, scope), BEFORE it runs again: `find_rejected` answers,
    `require_untried` refuses. The fingerprint ignores case, spacing and the
    order of the scope, and ignores the candidate version label, which is just
    a name for the attempt. Assumed: the same hypothesis against the same parent
    and scope is the same experiment; revisit if a materially different
    implementation of one hypothesis should be allowed a second run.
  * The archive is not memory. Canonical memory holds facts about the person,
    their objectives and workspaces; an archive record is a speculative
    engineering finding and must never surface when the person's memory is
    recalled. So the records live in their own append-only file, outside the
    store that holds `memories`, and this module imports nothing from the memory
    stack. The memory stack never reads this file; `tests/test_evolution_archive.py`
    drives FRIDAY's real recall paths to prove it.

The file is JSON lines, one experiment per line. Every read and write holds an
OS-level lock on a sidecar lock file, so two processes cannot interleave a
write or read half a line; a holder that never lets go makes the others fail
with `ArchiveError` after `lock_timeout` seconds rather than hang.
A line that does not parse is an `ArchiveError` naming the line: an archive that
silently skipped a damaged record could re-run an experiment it had already
rejected, so it fails closed. There is no automatic repair: a line torn outside
the archive's control (power loss mid-write) keeps it closed until an operator
fixes or removes the named line.
Everything a caller supplies is data: it is validated, stored as JSON and never
interpreted.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from friday.config import DATA_DIR

try:                      # Windows: byte-range lock on the lock file
    import msvcrt
    fcntl = None
except ImportError:       # POSIX: advisory flock on the lock file
    msvcrt = None
    import fcntl

PROMOTE = "PROMOTE"
REJECT = "REJECT"
#: The experiment step was refused before it ran (EVOLAB-002: an evolution
#: allowance was used up). It is archived, but the experiment did not lose, so
#: it never counts as a rejection and never lifts one.
REFUSED = "REFUSED"
DECISIONS = (PROMOTE, REJECT, REFUSED)

#: One lock for the process: threads and archives on one file must not interleave.
#: The cross-process half is an OS file lock on `<root>/.archive.lock` (see
#: `EvolutionArchive._locked`): FRIDAY runs the MCP server and the voice agent
#: as two processes that may both write this archive.
_WRITE_LOCK = threading.RLock()

#: Seconds a reader or writer waits for another process before it gives up.
LOCK_TIMEOUT = 30.0


class ArchiveError(ValueError):
    """A record is malformed, or the archive file is damaged."""


class AlreadyRejected(ArchiveError):
    """The experiment was tried and rejected; `record` is the archived one."""

    def __init__(self, record: dict) -> None:
        super().__init__(
            f"experiment already rejected as {record['id']} on {record['recorded_at']}: "
            f"{record['hypothesis']!r} against {record['parent_version']!r}")
        self.record = record


def _text(name: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArchiveError(f"{name} must be a non-empty string")
    return value.strip()


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _scope(value) -> list[str]:
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ArchiveError("scope must be a list of names")
    names = sorted({_norm(_text("scope entry", v)) for v in value})
    if not names:
        raise ArchiveError("scope must name at least one component")
    return names


def fingerprint(*, parent_version: str, hypothesis: str, scope) -> str:
    """The canonical identity of an experiment, independent of wording noise."""
    canonical = json.dumps(
        {"parent_version": _text("parent_version", parent_version),
         "hypothesis": _norm(_text("hypothesis", hypothesis)),
         "scope": _scope(scope)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _measurements(name: str, value) -> dict:
    if not isinstance(value, dict) or not value:
        raise ArchiveError(f"{name} must be a non-empty mapping of measurements")
    for key, number in value.items():
        if not isinstance(key, str) or not key:
            raise ArchiveError(f"{name} keys must be non-empty strings")
        if isinstance(number, bool) or not isinstance(number, (int, float)) \
                or not math.isfinite(number):
            raise ArchiveError(f"{name}[{key!r}] must be a finite number")
    return dict(value)


def _evidence(value) -> dict:
    if not isinstance(value, dict) or not value:
        raise ArchiveError("evaluation must be a non-empty mapping of evidence")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ArchiveError(f"evaluation is not plain JSON data: {exc}") from exc
    return dict(value)


class EvolutionArchive:
    """Append-only archive of evolution experiments in `<root>/experiments.jsonl`."""

    def __init__(self, root: Path | str | None = None, *,
                 lock_timeout: float = LOCK_TIMEOUT) -> None:
        self.root = Path(root) if root is not None else DATA_DIR / "evolution_archive"
        self.path = self.root / "experiments.jsonl"
        self.lock_path = self.root / ".archive.lock"
        self.lock_timeout = float(lock_timeout)

    @contextmanager
    def _locked(self):
        """Exclusive access to the archive across threads AND processes."""
        deadline = time.monotonic() + self.lock_timeout
        if not _WRITE_LOCK.acquire(timeout=max(self.lock_timeout, 0)):
            raise ArchiveError(f"archive busy: {self.path} (in-process lock)")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self.lock_path, "a+b") as handle:
                while True:
                    try:
                        if msvcrt is not None:
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise ArchiveError(
                                f"archive busy: {self.path} is locked by another "
                                f"process (waited {self.lock_timeout:g}s)") from exc
                        time.sleep(0.02)
                try:
                    yield
                finally:
                    if msvcrt is not None:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            _WRITE_LOCK.release()

    # -- reading -----------------------------------------------------------

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self._locked():
            return self._read()

    def _read(self) -> list[dict]:
        """Parse the file; the caller holds the lock."""
        if not self.path.exists():
            return []
        records = []
        with open(self.path, "rb") as handle:
            for number, raw in enumerate(handle, start=1):
                try:
                    row = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ArchiveError(
                        f"{self.path} line {number} is not a valid record: {exc}") from exc
                if not isinstance(row, dict) or not row.get("id") or not row.get("fingerprint"):
                    raise ArchiveError(f"{self.path} line {number} is not an experiment record")
                records.append(row)
        return records

    def get(self, experiment_id: str) -> dict | None:
        for row in self._load():
            if row["id"] == experiment_id:
                return row
        return None

    def query(self, text: str = "", *, decision: str | None = None) -> list[dict]:
        """Records matching `text` (case-insensitive, any field a person would
        search by) and, if given, `decision`; newest first."""
        needle = _norm(text or "")
        found = []
        for row in reversed(self._load()):
            if decision is not None and row.get("decision") != decision:
                continue
            haystack = _norm(" ".join(str(row.get(k, "")) for k in (
                "id", "objective", "parent_version", "candidate_version",
                "hypothesis", "rollback_version")) + " " + " ".join(row.get("scope", [])))
            if needle in haystack:
                found.append(row)
        return found

    def find_rejected(self, *, parent_version: str, hypothesis: str, scope) -> dict | None:
        """The archived rejection of this experiment, or None. The latest record
        of the experiment decides, so a later promotion lifts an earlier
        rejection; a REFUSED record says nothing about the experiment."""
        key = fingerprint(parent_version=parent_version, hypothesis=hypothesis, scope=scope)
        latest = None
        for row in self._load():
            if row["fingerprint"] == key and row.get("decision") != REFUSED:
                latest = row
        return latest if latest and latest.get("decision") == REJECT else None

    def require_untried(self, *, parent_version: str, hypothesis: str, scope) -> None:
        """Call before running an experiment: raises AlreadyRejected if it lost before."""
        hit = self.find_rejected(parent_version=parent_version, hypothesis=hypothesis,
                                 scope=scope)
        if hit is not None:
            raise AlreadyRejected(hit)

    # -- writing -----------------------------------------------------------

    def record(self, *, objective: str, parent_version: str, candidate_version: str,
               hypothesis: str, scope, baseline: dict, candidate: dict,
               evaluation: dict, decision: str, rollback_version: str) -> dict:
        """Append one experiment, rejected or promoted, and return it."""
        if decision not in DECISIONS:
            raise ArchiveError(f"decision must be one of {DECISIONS}, not {decision!r}")
        row = {
            "id": f"EXP-{uuid.uuid4().hex[:12]}",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "objective": _text("objective", objective),
            "parent_version": _text("parent_version", parent_version),
            "candidate_version": _text("candidate_version", candidate_version),
            "hypothesis": _text("hypothesis", hypothesis),
            "scope": _scope(scope),
            "baseline": _measurements("baseline", baseline),
            "candidate": _measurements("candidate", candidate),
            "evaluation": _evidence(evaluation),
            "decision": decision,
            "rollback_version": _text("rollback_version", rollback_version),
        }
        row["fingerprint"] = fingerprint(parent_version=row["parent_version"],
                                         hypothesis=row["hypothesis"], scope=row["scope"])
        line = (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        with self._locked():
            self._read()      # never append to a damaged archive
            with open(self.path, "ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        return row
