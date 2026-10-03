"""
SEC-005 (PRD v7 section 43): the filesystem boundary of a delegation, held
below Hermes.

Hermes cannot enforce an execution envelope on its own file tools, and the
exec bridge (`friday.tools.execution_bridge`) is spawned once per Hermes
profile with a fixed environment and no session id, so a per-session root
cannot ride in with it - and one the model supplied with each call would be a
request, not a boundary. So FRIDAY keeps the boundary where only it can write:
`HermesBridge.delegate` registers the envelope of the delegation it starts,
and the bridge reads the registry on every call.

A call is judged like any other FRIDAY file access: the path is made
canonical (relative paths start at the delegation's workspace), symlink,
junction and reparse-point targets are followed to where they really land,
UNC and device paths are refused, and then `toolsets.files.resolve_for` holds
it to the envelope's read roots, or its write roots for anything that changes
a file. Whatever is refused is logged.

Several delegations can be live on one gateway and the bridge cannot tell
their calls apart, so it allows only what ALL of them allow; and a second
delegation under a different workspace is refused when it registers. A
registry that cannot be read refuses every call: the boundary fails closed.
With nothing registered - a conversation, no envelope - the bridge is the
ordinary workspace jail it has always been.

The registry lives in FRIDAY's own state directory, which the jail-backed file
tools refuse (`fsjail._reject_own_state`). A program a worker runs is not
confined by file roots - an interpreter opens any path its user can - so while
a delegation holds the bridge no command runs at all (`refuse_commands`; owner
ruling 2026-10-03c). Commands come back only behind an OS sandbox (EXEC-SBX-001).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from friday import contracts as c
from friday.config import DATA_DIR
from friday.fsjail import JailError, is_reparse_point

logger = logging.getLogger(__name__)

ENV_DIR = "FRIDAY_DELEGATION_SCOPE_DIR"
REFUSALS = "refusals.jsonl"

#: An entry is dropped this long after its envelope ended: by then the
#: delegation was interrupted, and one left by a crashed process must not
#: refuse every later call forever.
STALE_AFTER = 900.0
#: An entry that cannot be read has no expiry to go by.
UNREADABLE_AFTER = 86_400.0
#: A listing stops here; the walk is over a tree the worker chose.
LISTING_LIMIT = 20_000


class ScopeError(JailError):
    """The registry cannot be read, so nothing can be allowed."""


@dataclass(frozen=True)
class Entry:
    key: str
    workspace: str
    owner: str
    envelope: c.Envelope


def _folder() -> Path:
    raw = os.environ.get(ENV_DIR, "").strip()
    return Path(raw) if raw else DATA_DIR / "delegation_scopes"


def _file(key: str) -> Path:
    return _folder() / (re.sub(r"[^A-Za-z0-9._-]", "_", key) + ".json")


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _load(path: Path) -> Entry:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Entry(key=str(raw["key"]), workspace=str(raw["workspace"]),
                 owner=str(raw.get("owner", "")),
                 envelope=c.Envelope.from_dict(raw["envelope"]))


def active() -> list[Entry]:
    """Every registered delegation; ScopeError when any cannot be read."""
    try:
        paths = sorted(_folder().glob("*.json"))
    except OSError as exc:
        raise ScopeError(f"the delegation scope registry cannot be listed: {exc}") from exc
    entries = []
    for path in paths:
        try:
            entries.append(_load(path))
        except (OSError, ValueError, KeyError, TypeError, c.ContractError) as exc:
            raise ScopeError(
                f"the delegation scope {path.name} cannot be read "
                f"({type(exc).__name__}); every file call is refused until it is "
                f"released") from exc
    return entries


def _prune() -> None:
    """Drop entries nothing is running under any more. Only a registering
    process prunes: a reader (the bridge) never deletes what it is held to."""
    from friday.hermes_bridge import owner_alive

    now = time.time()
    for path in _folder().glob("*.json"):
        try:
            entry = _load(path)
        except (OSError, ValueError, KeyError, TypeError, c.ContractError):
            try:
                if now - path.stat().st_mtime > UNREADABLE_AFTER:
                    path.unlink(missing_ok=True)
            except OSError:
                logger.warning("delegation_scope.prune could not drop %s", path.name)
            continue
        seconds_left = entry.envelope.seconds_left()
        lapsed = seconds_left is not None and -seconds_left > STALE_AFTER
        if lapsed or not owner_alive(entry.owner):
            path.unlink(missing_ok=True)


def register(key: str, envelope: c.Envelope, workspace: str) -> None:
    """
    Hold the delegation `key` to `envelope` from now until `release`.

    Raises `contracts.OutsideEnvelope` when the workspace lies outside the
    envelope, when the registry cannot be read, or when another delegation
    already holds the bridge under a different workspace.
    """
    from friday.hermes_bridge import process_owner

    if not workspace or not envelope.reads(workspace):
        raise c.OutsideEnvelope(
            f"the workspace {workspace or '(none)'} is outside this run's envelope")
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    _prune()
    try:
        held = active()
    except ScopeError as exc:
        raise c.OutsideEnvelope(str(exc)) from exc
    for other in held:
        if not _same(other.workspace, workspace):
            raise c.OutsideEnvelope(
                f"another delegation (objective {other.envelope.objective_id or '-'}) "
                f"holds the execution bridge in {other.workspace}; it serves one "
                f"workspace at a time")
    target = _file(key)
    scratch = target.with_suffix(".tmp")
    scratch.write_text(json.dumps({
        "key": key, "workspace": workspace, "owner": process_owner(),
        "envelope": envelope.to_dict()}), encoding="utf-8")
    os.replace(scratch, target)


def release(key: str) -> None:
    _file(key).unlink(missing_ok=True)


def _absolute(raw: str) -> bool:
    """Absolute under either path flavour, UNC and rooted-without-drive too."""
    windows = PureWindowsPath(raw)
    return bool(os.path.isabs(raw) or windows.is_absolute() or windows.drive
                or raw.startswith(("/", "\\")))


def _judge(held: list[Entry], raw: str, write: bool) -> Path:
    from friday.toolsets import files

    if not raw or not raw.strip():
        raise JailError("empty path")
    for entry in held:
        if entry.envelope.expired():
            raise JailError("this delegation's envelope has expired")
    workspace = held[0].workspace
    if any(not _same(entry.workspace, workspace) for entry in held):
        raise JailError("delegations under different workspaces are live; "
                        "the bridge cannot tell their calls apart")
    target = raw if _absolute(raw) else str(Path(workspace) / raw)
    path = None
    for entry in held:
        run = c.Run(run_id=entry.envelope.run_id, request="delegation file access",
                    envelope=entry.envelope)
        path = files.resolve_for(run, target, write=write)
    return path


def _refused(held: list[Entry], raw: str, write: bool, why: Exception) -> None:
    runs = ",".join(entry.envelope.run_id for entry in held) or "-"
    logger.warning("delegation_scope.refused runs=%s %s path=%.300s: %s",
                   runs, "write" if write else "read", raw, why)
    line = json.dumps({"at": time.time(), "runs": runs, "write": write,
                       "path": raw[:300], "reason": str(why)[:300]})
    try:
        with (_folder() / REFUSALS).open("a", encoding="utf-8") as log:
            log.write(line + "\n")
    except OSError as exc:
        logger.warning("delegation_scope.refusal could not be recorded: %s", exc)


def resolve(raw: str, *, write: bool = False) -> Path | None:
    """
    The real path `raw` names for the delegation(s) holding the bridge, or None
    when none is (the ordinary workspace jail applies). Raises `JailError`
    when the path is not the delegation's to read - or, with `write`, to change.
    """
    try:
        held = active()
    except ScopeError as exc:
        _refused([], raw, write, exc)
        raise
    if not held:
        return None
    try:
        return _judge(held, raw, write)
    except JailError as exc:
        _refused(held, raw, write, exc)
        raise


def refuse_commands() -> None:
    """
    Raise `JailError` when a command may not run: a delegation holds the bridge
    (expired or not), or the registry cannot be read. Returns when none does and
    the ordinary workspace jail applies. Called before the command is parsed, so
    nothing is launched and the refusal names no program that would get round it.
    """
    try:
        held = active()
    except ScopeError as exc:
        _refused([], "(command)", False, exc)
        raise
    if not held:
        return
    why = JailError(
        "no command can be run while this delegation is held to an execution "
        "envelope; its file access is the bridge's read, write, list and search "
        "tools, held to the envelope's roots")
    _refused(held, "(command)", False, why)
    raise why


def listing(directory: Path) -> list[str]:
    """
    The files under `directory` the delegation may read, relative to its
    workspace where they lie inside it. The walk does not enter a reparse
    point, and each file is judged like a read of it, so a link that leaves the
    roots lists nothing.
    """
    held = active()
    if not held:
        # released between the caller's resolve() and this walk
        raise JailError("the delegation holding this listing has ended")
    base = Path(held[0].workspace).resolve()
    names: list[str] = []
    for here, folders, filenames in os.walk(directory):
        folders[:] = [name for name in folders
                      if not is_reparse_point(Path(here) / name)]
        for name in filenames:
            path = Path(here) / name
            try:
                real = _judge(held, str(path), False)
            except JailError:
                continue
            try:
                names.append(str(real.relative_to(base)))
            except ValueError:
                names.append(str(real))
            if len(names) >= LISTING_LIMIT:
                return sorted(names)
    return sorted(names)
