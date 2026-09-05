
"""
Files toolset (Phase 1C).

Every path goes through friday/fsjail.py before anything touches disk, and
every write is verified by reading back what landed - a write() that did not
raise is not evidence that the bytes are on disk, in the same way that a
Popen() that did not raise was not evidence that an app opened.

Reads are AUTO inside the configured roots; creates, writes, edits, copies,
moves and recycles are ASK (§11). `files_recycle` sends a file to the Recycle
Bin and verifies it left - it is not an irreversible delete, which is why it
sits with the writes rather than behind CONFIRM. Permanent deletion is not
this function under a flag; it would be a separate capability, because hiding
an irreversible operation behind a reversible word is how somebody loses work
they thought was recoverable.
"""

from __future__ import annotations

import fnmatch

import hashlib

import os

import shutil

import time

from pathlib import Path

from friday import confirmation
from friday import contracts as c

from friday.fsjail import FileJail, JailError, is_reparse_point

from friday.policy import PolicyEngine, default_engine

from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

#: The outcomes files_delete reports, so a caller reads a fact, not a status
#: code it has to interpret. RECYCLED can be undone; DELETED cannot.
RECYCLED = "recycled"
DELETED = "deleted"
FAILED = "failed"
NOT_FOUND = "not_found"
BLOCKED = "blocked"

#: Friday's own scratch outputs. A file the system created and owns here can be
#: cleaned without asking the boss to confirm each one -- a confirmation per
#: temp file is noise, and the directory is a fence around what that trust
#: covers. Everything outside it still needs the yes.
try:
    from friday.config import ARTIFACTS_DIR
except Exception:  # noqa: BLE001
    ARTIFACTS_DIR = None

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
RECYCLE = 'RECYCLE'

MAX_READ_CHARS = 40_000

MAX_SEARCH_HITS = 200

#: A search has to end whether or not it found anything.
#:
#: `MAX_SEARCH_HITS` caps hits, not work - and with `contains` set, almost
#: nothing becomes a hit, so the loop kept reading every text file under the
#: root. "Search my project for the word reactor" took **300.6 seconds**,
#: which is the MCP session ceiling, and it held the server long enough that
#: the next agent session's handshake timed out. One slow tool took the whole
#: process down with it.
SEARCH_SECONDS = 6.0

MAX_SEARCH_EXAMINED = 20_000

#: Directories that are never what he means by "my project", and are where
#: all the time went: .venv alone is tens of thousands of files. Pruned rather
#: than filtered, which is why this uses os.walk - rglob cannot skip a subtree
#: once it has decided to descend into it.
SKIP_DIRECTORIES = frozenset({
    ".venv", "venv", "env", "node_modules", "__pycache__", ".git", ".hg",
    ".svn", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea",
    ".vs", ".vscode", "dist", "build", "site-packages", ".next", ".nuxt",
    "target", "vendor", ".gradle", ".terraform", "coverage", ".cache",
})

TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".csv", ".html", ".css", ".xml", ".sql", ".sh",
    ".ps1", ".bat", ".rst", ".log", ".env.example",
})

_jail: FileJail | None = None


def jail() -> FileJail:
    global _jail
    if _jail is None:
        _jail = FileJail()
    return _jail


def reset_jail(new: FileJail | None = None) -> None:
    """Swap the jail (tests, or reconfiguration)."""
    global _jail
    _jail = new


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return run.record(c.started(run.run_id, tool_id).finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _safe(run: c.Run, started: c.ActionResult, raw: str) -> tuple[Path | None, c.ActionResult | None]:
    """Resolve a path through the jail, or return a FAILED result."""
    try:
        return jail().resolve(raw), None
    except JailError as exc:
        return None, run.record(c.failed(started, f"path refused: {exc}"))


def files_read(
    run: c.Run, path: str, *, max_chars: int = MAX_READ_CHARS,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    tool_id = "files.read"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    target, failure = _safe(run, started, path)
    if failure:
        return failure
    if not target.exists():
        return run.record(c.failed(started, f"no such file: {target}"))
    if target.is_dir():
        return run.record(c.failed(started, f"{target} is a directory - use files.list"))

    try:
        raw = target.read_bytes()
    except OSError as exc:
        return run.record(c.failed(started, f"could not read {target}: {exc}"))

    try:
        text = raw.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        text, binary = "", True

    if binary:
        # Say where to go next, when there is somewhere to go.
        #
        # Measured: asked "what does the pdf at <path> say", the model reached
        # for files_read - which is CORE, always visible, and needs no search -
        # got "binary", and told the boss "I can't read its contents
        # directly". It never searched for a PDF capability, because it
        # already had a plausible tool in front of it and the tool's answer
        # was a dead end. Ranking documents_extract first offline does nothing
        # when discovery is never run. A result that names its own successor
        # does.
        from friday import capabilities

        # Asked of the registry rather than known here. A generic tool that
        # imports the specialist and hard-codes the pairing is one pairing;
        # a generic tool that asks which capability declares this content is
        # the mechanism, and the next one - an image, a video - costs a line
        # in the registry rather than an edit here.
        specialist = capabilities.specialist_for(target.suffix)
        instead = (f" - use {specialist.id}, which reads this format"
                   if specialist else "")
        return run.record(c.partial(
            started,
            f"{target.name} is not UTF-8 text ({len(raw)} bytes){instead}",
            output=_scoped({"path": str(target), "bytes": len(raw),
                            "binary": True,
                            "try_instead": specialist.id if specialist else ""}),
        ))

    return run.record(c.succeeded(
        started,
        output=_scoped({"path": str(target), "text": text[:max_chars],
                        "chars": len(text), "lines": text.count("\n") + 1,
                        "truncated": len(text) > max_chars, "bytes": len(raw)}),
        verification=c.Verification(
            method="file_read",
            evidence=f"{target.name}: {len(raw)} bytes read, sha256:{_digest(raw)}",
        ),
    ))


def files_info(
    run: c.Run, path: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    tool_id = "files.info"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    target, failure = _safe(run, started, path)
    if failure:
        return failure
    if not target.exists():
        return run.record(c.failed(started, f"no such path: {target}"))

    stat = target.stat()
    return run.record(c.succeeded(
        started,
        output=_scoped({"path": str(target), "name": target.name,
                        "is_dir": target.is_dir(), "size_bytes": stat.st_size,
                        "modified_epoch": stat.st_mtime, "suffix": target.suffix}),
        verification=c.Verification(
            method="stat",
            evidence=f"{target.name}: {'dir' if target.is_dir() else 'file'}, "
                     f"{stat.st_size} bytes",
        ),
    ))


def files_list(
    run: c.Run, path: str = ".", *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    tool_id = "files.list"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if path == ".":
        path = str(jail().roots[0])
    target, failure = _safe(run, started, path)
    if failure:
        return failure
    if not target.is_dir():
        return run.record(c.failed(started, f"{target} is not a directory"))

    entries = []
    for child in sorted(target.iterdir()):
        try:
            entries.append({"name": child.name, "is_dir": child.is_dir(),
                            "size_bytes": child.stat().st_size if child.is_file() else None})
        except OSError:
            continue

    return run.record(c.succeeded(
        started,
        output=_scoped({"path": str(target), "count": len(entries), "entries": entries}),
        verification=c.Verification(
            method="dir_listing",
            evidence=f"{len(entries)} entr(ies) in {target}",
        ),
    ))


def files_roots(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Where ADA can and cannot look.

    Exists so "can you read my code?" has a real answer instead of a failure
    on the first path tried.
    """
    tool_id = "files.roots"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    described = jail().describe()
    return run.record(c.succeeded(
        started,
        output=_scoped({
            **described,
            "protected_everywhere": [
                ".env files", ".git directories", ".ssh keys",
                "private keys (*.pem, *.key, *.pfx)", "credentials files",
                ".aws, .npmrc, .pypirc",
            ],
            "how_to_add": "set ADA_FILE_ROOTS in .env, separated by ';'",
        }),
        verification=c.Verification(
            method="jail_configuration",
            evidence=f"{len(described['roots'])} readable root(s): "
                     f"{described['roots']}; {described['deny_patterns']} "
                     f"protected patterns refused inside them",
        ),
    ))


def files_search(
    run: c.Run, pattern: str = "*", *, root: str | None = None,
    contains: str | None = None, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Glob for files, optionally filtering by text content."""
    tool_id = "files.search"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    base, failure = _safe(run, started, root or str(jail().roots[0]))
    if failure:
        return failure
    if not base.is_dir():
        return run.record(c.failed(started, f"{base} is not a directory"))

    hits, scanned, skipped = [], 0, 0
    deadline = time.monotonic() + SEARCH_SECONDS
    stopped = ""
    needle = contains.lower() if contains else ""

    for directory, subdirectories, filenames in os.walk(base):
        # Prune in place: os.walk only skips what is removed from this list.
        subdirectories[:] = [d for d in subdirectories
                             if d not in SKIP_DIRECTORIES]
        if stopped:
            break
        for filename in filenames:
            if len(hits) >= MAX_SEARCH_HITS:
                stopped = f"the first {MAX_SEARCH_HITS} matches"
                break
            if scanned >= MAX_SEARCH_EXAMINED:
                stopped = f"{MAX_SEARCH_EXAMINED} files examined"
                break
            if time.monotonic() >= deadline:
                stopped = f"{SEARCH_SECONDS:.0f} seconds"
                break
            if not fnmatch.fnmatch(filename, pattern):
                continue
            candidate = Path(directory) / filename
            # Denylisted files must not appear even as names in results.
            try:
                jail().resolve(candidate)
            except JailError:
                skipped += 1
                continue
            scanned += 1
            if needle:
                if candidate.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                try:
                    if needle not in candidate.read_text(
                        encoding="utf-8", errors="ignore"
                    ).lower():
                        continue
                except OSError:
                    continue
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            hits.append({"path": str(candidate), "name": candidate.name,
                         "size_bytes": size})

    return run.record(c.succeeded(
        started,
        output=_scoped({"root": str(base), "pattern": pattern, "contains": contains,
                        "count": len(hits), "scanned": scanned,
                        "skipped_protected": skipped,
                        # Never a silent cap. A search that quietly stopped
                        # early reads exactly like one that found everything.
                        "complete": not stopped,
                        "stopped_at": stopped, "results": hits}),
        verification=c.Verification(
            method="glob_scan",
            evidence=f"{len(hits)} match(es) of {pattern!r} under {base}"
                     + (f" containing {contains!r}" if contains else "")
                     + (f"; {skipped} protected file(s) excluded" if skipped else "")
                     + (f"; stopped at {stopped} - there may be more"
                        if stopped else "; whole tree searched"),
        ),
    ))


def _write_and_verify(
    run: c.Run, started: c.ActionResult, target: Path, content: str, *,
    tool: str, existed: bool,
) -> c.ActionResult:
    payload = content.encode("utf-8")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        readback = target.read_bytes()
    except OSError as exc:
        return run.record(c.failed(started, f"write failed: {exc}"))

    if readback != payload:
        return run.record(c.partial(
            started,
            f"wrote {len(payload)} bytes but read back {len(readback)} - "
            "content on disk does not match",
            output=_scoped({"path": str(target)}),
        ))

    artifact = c.new_artifact(
        run_id=run.run_id, type="file", title=target.name,
        path_or_uri=str(target), producer=tool,
        verification=c.Verification(
            method="write_readback",
            evidence=f"{len(payload)} bytes, sha256:{_digest(payload)}",
        ),
        metadata={"overwrote_existing": existed},
    )
    return run.record(c.succeeded(
        started,
        output=_scoped({"path": str(target), "bytes": len(payload),
                        "overwrote_existing": existed}),
        artifacts=(artifact,),
        side_effects=(f"{'overwrote' if existed else 'created'} {target}",),
        verification=c.Verification(
            method="write_readback",
            evidence=f"{target.name}: {len(payload)} bytes written and read back "
                     f"identically, sha256:{_digest(payload)}",
        ),
    ))


def files_create(
    run: c.Run, path: str, content: str = "", *,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Create a new file. Refuses to overwrite - use files.write for that."""
    tool_id = "files.create"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    target, failure = _safe(run, started, path)
    if failure:
        return failure
    if target.exists():
        return run.record(c.failed(
            started, f"{target} already exists - use files.write to replace it"
        ))
    return _write_and_verify(run, started, target, content,
                             tool=tool_id, existed=False)


def files_write(
    run: c.Run, path: str, content: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Write a file, replacing it if present."""
    tool_id = "files.write"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    target, failure = _safe(run, started, path)
    if failure:
        return failure
    return _write_and_verify(run, started, target, content,
                             tool=tool_id, existed=target.exists())


def files_edit(
    run: c.Run, path: str, old: str, new: str, *,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Replace an exact string. Refuses if absent or ambiguous."""
    tool_id = "files.edit"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    target, failure = _safe(run, started, path)
    if failure:
        return failure
    if not target.is_file():
        return run.record(c.failed(started, f"no such file: {target}"))

    try:
        before = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return run.record(c.failed(started, f"could not read {target}: {exc}"))

    occurrences = before.count(old)
    if occurrences == 0:
        return run.record(c.failed(started, f"text not found in {target.name}"))
    if occurrences > 1:
        return run.record(c.failed(
            started,
            f"text appears {occurrences} times in {target.name} - "
            "provide a longer, unique snippet",
        ))

    after = before.replace(old, new, 1)
    result = _write_and_verify(run, started, target, after,
                               tool=tool_id, existed=True)
    if result.status != c.SUCCEEDED:
        return result

    # Verify semantically, not just that bytes landed.
    confirmed = target.read_text(encoding="utf-8")
    if new and new not in confirmed:
        return run.record(c.partial(
            started, "write succeeded but the replacement text is not present",
            output=_scoped({"path": str(target)}),
        ))
    return result


def _transfer(
    run: c.Run, tool_id: str, source: str, destination: str, *,
    move: bool, engine: PolicyEngine,
) -> c.ActionResult:
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    src, failure = _safe(run, started, source)
    if failure:
        return failure
    dst, failure = _safe(run, started, destination)
    if failure:
        return failure

    if not src.exists():
        return run.record(c.failed(started, f"no such source: {src}"))
    if src.is_dir():
        return run.record(c.failed(started, f"{src} is a directory - files only"))
    if dst.exists():
        return run.record(c.failed(started, f"{dst} already exists"))

    size = src.stat().st_size
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))
    except OSError as exc:
        return run.record(c.failed(started, f"{tool_id} failed: {exc}"))

    if not dst.exists():
        return run.record(c.failed(started, f"{tool_id} reported success but {dst} is absent"))
    if move and src.exists():
        return run.record(c.partial(
            started, f"copied to {dst} but {src} still exists - move incomplete",
            output=_scoped({"source": str(src), "destination": str(dst)}),
        ))

    evidence = f"{dst.name} exists, {dst.stat().st_size} bytes (source was {size})"
    if move:
        evidence += "; source no longer exists"

    artifact = c.new_artifact(
        run_id=run.run_id, type="file", title=dst.name, path_or_uri=str(dst),
        producer=tool_id,
        verification=c.Verification(method="destination_exists", evidence=evidence),
    )
    return run.record(c.succeeded(
        started,
        output=_scoped({"source": str(src), "destination": str(dst),
                        "size_bytes": dst.stat().st_size}),
        artifacts=(artifact,),
        side_effects=(f"{'moved' if move else 'copied'} {src} -> {dst}",),
        verification=c.Verification(
            method="destination_exists" + ("_source_absent" if move else ""),
            evidence=evidence,
        ),
    ))


def files_copy(run: c.Run, source: str, destination: str, *,
               engine: PolicyEngine = default_engine) -> c.ActionResult:
    return _transfer(run, "files.copy", source, destination, move=False, engine=engine)


def files_move(run: c.Run, source: str, destination: str, *,
               engine: PolicyEngine = default_engine) -> c.ActionResult:
    return _transfer(run, "files.move", source, destination, move=True, engine=engine)


def files_recycle(run: c.Run, path: str, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """
    Send a file to the Recycle Bin, where the boss can get it back.

    This module said for a long time that there was "deliberately no delete
    tool", and that was the right call while the only thing on offer was
    `os.remove` - a capability that destroys a file permanently, named after
    the thing a person means when they say "delete", which is not the same
    thing at all. On Windows a person who deletes a file expects it in the
    Recycle Bin, and expects to be able to change their mind.

    So this is a *recycle*, and it is named that. It goes through the shell's
    own mechanism, which means the file lands where the boss would look for
    it and can be restored the way they already know how.

    Permanent deletion is not this function under a flag. If it is ever
    needed it is a separate capability behind CONFIRM, because hiding an
    irreversible operation behind a reversible word is how somebody loses
    work they thought was recoverable.
    """
    tool_id = "files.recycle"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    target, failure = _safe(run, started, path)
    if failure:
        return failure

    if not target.exists():
        return run.record(c.failed(started, f"no such file: {target}"))
    if target.is_dir():
        return run.record(c.failed(
            started, f"{target} is a directory - files only, for now"))

    size = target.stat().st_size

    try:
        from send2trash import send2trash
    except ImportError:
        return run.record(c.started(run.run_id, tool_id).finish(
            status=c.NOT_CONFIGURED,
            error="send2trash is not installed, so nothing can be recycled safely; "
                  "refusing rather than deleting permanently instead"))

    try:
        send2trash(str(target))
    except Exception as exc:                                 # noqa: BLE001
        return run.record(c.failed(
            started, f"could not recycle {target.name}: {exc}"))

    # Read the disk back rather than trusting the call: a recycle that left
    # the file where it was is a partial result, not a success with a caveat.
    if target.exists():
        return run.record(c.partial(
            started,
            f"{target.name} was sent to the Recycle Bin and is still on disk",
            output=_scoped({"path": str(target), "recycled": False,
                            "deletion_mode": RECYCLE})))
    return run.record(c.succeeded(
        started,
        output=_scoped({"path": str(target), "recycled": True,
                        # The mode is part of the result because a caller
                        # reading "deleted" would draw the wrong conclusion
                        # about what can be undone; and it goes through
                        # _scoped like every other output here, so that a
                        # run's evidence says which machine it happened on.
                        "deletion_mode": RECYCLE,
                        "bytes": size, "restorable": True}),
        side_effects=(f"recycled {target.name}",),
        verification=c.Verification(
            method="path_absent_after_recycle",
            evidence=f"{target.name} ({size} bytes) is no longer at {target}; "
                     f"it went to the Recycle Bin rather than being destroyed, "
                     f"so it can be restored"),
    ))


def _under_artifacts(target: Path) -> bool:
    """True when target lives inside Friday's own artifacts directory."""
    base = ARTIFACTS_DIR
    if not base:
        return False
    try:
        target.resolve().relative_to(Path(base).resolve())
        return True
    except (ValueError, OSError):
        return False


def files_delete(run: c.Run, path: str, *, permanent: bool = False,
                 nonce: str | None = None,
                 engine: PolicyEngine = default_engine) -> c.ActionResult:
    """
    Delete a file. Recycle by default (undoable); permanent only behind a
    confirmation the boss spends once on this exact file.

    The order of the checks is the safety:

      1. the jail -- a path outside the roots, or a protected one (.env, keys),
         is refused before any question is asked;
      2. reparse points -- a junction or symlink is not followed and deleted as
         if it were the thing it points at;
      3. for a permanent delete WITH a nonce, the confirmation is spent before
         anything else, so a nonce that was already used, retargeted at a
         different file, or expired is refused as BLOCKED rather than mistaken
         for a missing file after the first delete succeeded;
      4. a Friday-owned artifact is exempt from the confirmation -- the boss
         does not confirm the deletion of the system's own scratch files;
      5. otherwise a permanent delete with no nonce asks, and does nothing.
    """
    tool_id = "files.delete_permanent" if permanent else "files.delete"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    target, failure = _safe(run, started, path)
    if failure:
        return run.record(c.started(run.run_id, tool_id).finish(
            status=c.CANCELLED,
            error=f"{APPROVAL_PREFIX}: path refused",
            output=_scoped({"result": BLOCKED, "path": path, "confirm": None})))

    if is_reparse_point(target):
        return run.record(started.finish(
            status=c.CANCELLED,
            error="a junction or symlink is not deleted through as if it were its target",
            output=_scoped({"result": BLOCKED, "path": str(target), "confirm": None})))

    exempt = _under_artifacts(target)

    # A permanent delete carrying a nonce spends it here, before existence is
    # even checked, so a reused nonce reads as BLOCKED, not NOT_FOUND.
    if permanent and nonce is not None and not exempt:
        spent = confirmation.book.consume(
            nonce, run_id=run.run_id, action=tool_id, target=str(target))
        if not spent.ok:
            return run.record(started.finish(
                status=c.CANCELLED,
                error=spent.reason,
                output=_scoped({"result": BLOCKED, "path": str(target), "confirm": None})))

    if not target.exists():
        return run.record(started.finish(
            status=c.OBSERVED,
            output=_scoped({"result": NOT_FOUND, "path": str(target), "confirm": None})))
    if target.is_dir():
        return run.record(started.finish(
            status=c.CANCELLED,
            error=f"{target} is a directory - files only",
            output=_scoped({"result": BLOCKED, "path": str(target), "confirm": None})))

    if not permanent:
        try:
            from send2trash import send2trash
        except ImportError:
            return run.record(started.finish(
                status=c.NOT_CONFIGURED,
                error="send2trash is not installed; refusing rather than deleting permanently",
                output=_scoped({"result": FAILED, "path": str(target), "confirm": None})))
        size = target.stat().st_size
        try:
            send2trash(str(target))
        except Exception as exc:  # noqa: BLE001
            return run.record(started.finish(
                status=c.FAILED,
                error=f"could not recycle {target.name}: {exc}",
                output=_scoped({"result": FAILED, "path": str(target), "confirm": None})))
        # Read the disk back rather than trust the call: a recycle that left
        # the file in place is a partial result, not a verified success.
        if target.exists():
            return run.record(c.partial(
                started,
                f"{target.name} was sent to the Recycle Bin but is still on disk",
                output=_scoped({"result": FAILED, "path": str(target),
                                "restorable": True, "confirm": None})))
        return run.record(c.succeeded(started, output=_scoped(
            {"result": RECYCLED, "mode": "recycle", "path": str(target),
             "bytes": size, "restorable": True, "confirm": None}),
            side_effects=(f"recycled {target.name}",),
            verification=c.Verification(method="path_absent_after_recycle",
                evidence=f"{target.name} ({size} bytes) went to the Recycle Bin and can be restored")))

    # Permanent, and either exempt or nonce already spent above. A bare
    # permanent delete (no nonce, not exempt) asks and does nothing.
    if not exempt and nonce is None:
        pending = confirmation.book.ask(
            run.run_id, tool_id, str(target),
            f"Permanently delete {target.name}? This cannot be undone.")
        return run.record(started.finish(
            status=c.CANCELLED,
            error=f"{APPROVAL_PREFIX}: permanent deletion needs your confirmation",
            output=_scoped({"result": None, "confirm": pending.to_dict(),
                            "path": str(target)})))

    size = target.stat().st_size
    try:
        target.unlink()
    except Exception as exc:  # noqa: BLE001
        return run.record(started.finish(
            status=c.FAILED,
            error=f"could not delete {target.name}: {exc}",
            output=_scoped({"result": FAILED, "path": str(target), "confirm": None})))
    # Same read-back on the destructive path: only call it gone if it is gone.
    if target.exists():
        return run.record(c.partial(
            started,
            f"{target.name} could not be removed; it is still on disk",
            output=_scoped({"result": FAILED, "path": str(target), "confirm": None})))
    return run.record(c.succeeded(started, output=_scoped(
        {"result": DELETED, "mode": "permanent", "path": str(target),
         "bytes": size, "restorable": False, "confirm": None}),
        side_effects=(f"permanently deleted {target.name}",),
        verification=c.Verification(method="path_absent_after_delete",
            evidence=f"{target.name} ({size} bytes) is permanently gone from {target}")))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Writes (ASK-gated). Each verifies by reading back.
# ---------------------------------------------------------------------------
