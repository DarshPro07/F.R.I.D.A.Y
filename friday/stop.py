"""
The out-of-band emergency stop (PRD v7 STOP-001, section 53).

STOP must be deterministic and independent of the model - "the model does
not get to refuse STOP" - so nothing here asks one. Pressing it (the CLI, a
spoken phrase matched before any model hears it, the control room) does four
things, none of which needs anybody's cooperation:

    latch    one row in the store every FRIDAY process shares; the capability
             runtime and the objective executor refuse new actions while it
             is set, in whichever process they run
    revoke   every network grant, audited
    park     every running objective, PAUSED with the reason
    stop     in every process a watcher sees the latch, runs that process's
             own stop hooks and kills the worker processes it owns, by PID -
             a worker that ignores a polite request is not asked twice

Resuming is the person's explicit act (`resume`), and the latch survives a
restart until then. objective-engine owns the latch; each service registers
its own hook, so nothing here reaches into another service's internals.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Callable

logger = logging.getLogger("friday-stop")

_HOOKS: dict[str, Callable[[str], object]] = {}
_OWNED: set[int] = set()
#: Latches (by their `latched_at`) this process has already acted on, so the
#: process that pressed STOP does not run its hooks again when its watcher
#: sees the same latch.
_HANDLED: set[str] = set()
_LOCK = threading.Lock()
_WATCHER: "Watcher | None" = None

#: The whole utterance is the stop and nothing else: "stop the music" is not
#: STOP, and neither is a sentence that merely contains the words.
_PHRASE = re.compile(
    r"^\W*(?:friday\W+)?(?:emergency\s+stop|stop\s+everything|abort\s+everything)\W*$",
    re.IGNORECASE)


#: The person's explicit release, just as narrow.
_RESUME = re.compile(r"^\W*(?:friday\W+)?resume\s+everything\W*$", re.IGNORECASE)


def is_stop_phrase(text: str) -> bool:
    return bool(_PHRASE.match(text or ""))


def is_resume_phrase(text: str) -> bool:
    return bool(_RESUME.match(text or ""))


def refusal() -> dict | None:
    """The answer every conversational tool gives while STOP is latched, or
    None when it is not. Tools return it instead of acting."""
    stopped = latched()
    if not stopped:
        return None
    return {"status": "not_permitted",
            "error": f"STOPPED: {stopped['reason']} - nothing new starts "
                     f"until the person resumes"}


def register_hook(name: str, fn: Callable[[str], object]) -> None:
    """A service's own share of the stop, run in the process that registered it."""
    with _LOCK:
        _HOOKS[name] = fn


def own_process(pid: int) -> None:
    """A worker process this process started; STOP kills it and its children."""
    with _LOCK:
        _OWNED.add(int(pid))


def disown_process(pid: int) -> None:
    with _LOCK:
        _OWNED.discard(int(pid))


#: STOP's own handle on this install's database, by path.
_STORE: dict = {"path": None, "store": None}


def _process_store():
    """
    The latch lives in this install's database file, and STOP opens its own
    handle on it rather than borrowing a toolset's store object: the stop
    must not depend on how any other part of FRIDAY holds its data. One
    handle per process, reopened if the database path changes.
    """
    import os

    from friday.store import DEFAULT_DB, Store

    path = str(os.getenv("ADA_DB") or DEFAULT_DB)
    with _LOCK:
        if _STORE["path"] != path:
            previous = _STORE["store"]
            _STORE.update(path=path, store=Store(path))
            if previous is not None:
                previous.close()
        return _STORE["store"]


def latched(store=None) -> dict | None:
    """The latched stop, or None."""
    return (store or _process_store()).stop_latched()


def _kill_owned() -> list[int]:
    import psutil

    with _LOCK:
        pids = sorted(_OWNED)
    killed = []
    for pid in pids:
        try:
            process = psutil.Process(pid)
            family = process.children(recursive=True) + [process]
        except psutil.Error:
            disown_process(pid)                     # already gone
            continue
        for member in family:
            try:
                member.kill()
            except psutil.Error:
                pass                                # gone between the two calls
        psutil.wait_procs(family, timeout=5)
        killed.append(pid)
        disown_process(pid)
    return killed


def run_local_hooks(reason: str) -> dict:
    """This process's share: each registered hook, then every owned worker,
    killed. One failing hook is reported and does not spare the rest."""
    with _LOCK:
        hooks = list(_HOOKS.items())
    results: dict = {}
    for name, fn in hooks:
        try:
            fn(reason)
            results[name] = "stopped"
        except Exception as exc:                    # noqa: BLE001 - reported below
            logger.exception("stop hook %s failed", name)
            results[name] = f"failed: {type(exc).__name__}: {exc}"
    results["killed"] = _kill_owned()
    return results


def emergency_stop(reason: str, *, actor: str, store=None) -> dict:
    """Stop everything: latch, revoke, park, and this process's hooks now -
    every other process's hooks through its watcher."""
    from friday import objectives as O
    from friday import trust
    from friday.contracts import now_iso

    db = store or _process_store()
    db.stop_latch(reason=reason, actor=actor, now=now_iso())
    row = db.stop_latched() or {}
    revoked = O.revoke_all_network_grants(db, reason=f"STOP: {reason}", actor=actor)
    paused = O.pause_all_running(db, reason=reason)
    with _LOCK:
        fresh = row.get("latched_at", "") not in _HANDLED
        _HANDLED.add(row.get("latched_at", ""))
    local = run_local_hooks(reason) if fresh else {"killed": []}
    trust.audit().record(actor=actor, action="friday.stop", tier=trust.R3,
                         decision="STOPPED", target="everything",
                         detail={"reason": reason, "paused": paused,
                                 "grants_revoked": revoked, "hooks": local})
    logger.warning("STOP by %s: %s (paused %d, grants %d, killed %s)",
                   actor, reason, len(paused), revoked, local["killed"])
    return {"reason": reason, "grants_revoked": revoked, "paused": paused,
            "hooks": local, "killed": local["killed"]}


def resume(*, actor: str, store=None) -> bool:
    """The person clears the stop. Parked objectives stay parked until each is
    resumed; this only lets new actions start again."""
    from friday import trust

    cleared = (store or _process_store()).stop_clear()
    if cleared:
        trust.audit().record(actor=actor, action="friday.stop", tier=trust.R3,
                             decision="RESUMED", target="everything")
    return cleared


class Watcher:
    """Polls the latch. When a new one appears - pressed in this process or
    any other - runs this process's hooks once."""

    def __init__(self, store=None, interval: float = 1.0) -> None:
        self._store = store or _process_store
        self.interval = interval
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "Watcher":
        self._thread = threading.Thread(target=self._loop, name="friday-stop-watch",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stopping.wait(self.interval):
            try:
                row = self._store().stop_latched()
            except Exception:                        # noqa: BLE001 - logged; try again
                logger.exception("the stop watcher could not read the latch")
                continue
            if not row:
                continue
            with _LOCK:
                fresh = row["latched_at"] not in _HANDLED
                _HANDLED.add(row["latched_at"])
            if fresh:
                run_local_hooks(row["reason"])


def watch(interval: float = 1.0) -> Watcher:
    """Start this process's watcher, once. Called by each long-running process."""
    global _WATCHER
    with _LOCK:
        if _WATCHER is None:
            _WATCHER = Watcher(interval=interval).start()
        return _WATCHER


def reset_for_tests() -> None:
    global _WATCHER
    with _LOCK:
        watcher, _WATCHER = _WATCHER, None
        _HOOKS.clear()
        _OWNED.clear()
        _HANDLED.clear()
    if watcher is not None:
        watcher.stop()
