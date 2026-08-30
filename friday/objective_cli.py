"""
Phase 3 CLI: start and drive objective runs without the MCP server.

Two faces of one process:

  * ``python -m friday.objective_cli --objective "..."``   (mode B, the child)
    plans the objective, compiles it, and drives it with a
    ContinuousTaskExecutor until the run is terminal, printing a final
    summary. This is what the demo and the agent job spawn.

  * ``python -m friday.objective_cli --objective "..."`` is reached via the
    mode A parent, which parses the objective, plans it, and spawns the child
    as a detached, windowless subprocess so the caller exits immediately and
    the job keeps driving on its own. (Both modes share the same store, so
    the lease arbitrates if another executor is also alive.)

Control-plane flags (``--status/--list/--pause/--resume/--cancel/--history``)
give the agent job - or an operator - a command line over a run without the
MCP server, and ``--port-*`` flags exercise the engine dispatch in-process
for tests.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import subprocess
import sys
import time

from friday import capabilities, contracts as c
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.policy import PolicyEngine
from friday.store import DEFAULT_DB, Store
from friday.toolsets import objectives as OT

_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

MAX_DRIVE_SECONDS = 240
DRIVE_POLL_SECONDS = 2


def _db() -> Store:
    return Store(os.getenv("ADA_DB") or DEFAULT_DB)


# ---------------------------------------------------------------------------
# The capability port: capability id -> (run, **arguments) -> ActionResult
# ---------------------------------------------------------------------------

def build_dispatch():
    """The same dispatch an engine uses: capability ids to implementations.

    Synchronous and async toolset functions are both handled; the executor
    classifies any raised exception. Objective-capability ids dispatch to
    the toolset's own port, so a run may also pause, resume or cancel other
    runs - never itself as a task, which compile forbids.

    This used to be five hand-written entries, and that was the second Friday:
    conversation reached 132 capabilities, an objective reached 5, and asking a
    durable run to play music raised `LookupError: no such capability` about
    something the same process had registered. The table is derived from the
    registry now - see `friday/capability_runtime.py` for why calling 132
    capabilities by id is not the same thing as showing a model 132 schemas.
    """
    from friday import capability_runtime

    runtime = capability_runtime.CapabilityRuntime()
    control = OT.capability_port()

    async def dispatch(capability_id: str, arguments: dict) -> dict:
        run = c.Run.create(
            json.dumps(arguments, default=str)[:240], capability=capability_id)

        if capability_id in control:
            result = control[capability_id](run, **arguments)
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                raise RuntimeError(f"{capability_id} returned no result")
        else:
            # Everything else goes through the runtime, which resolves the
            # id against the registry the same way conversation does, so a
            # capability the process has registered is one a run can call.
            result = runtime.execute(capability_id, arguments, run=run)
            if inspect.isawaitable(result):
                result = await result

        run.transition("completed" if run.all_succeeded else "partial",
                       None if run.all_succeeded else (result.error or "not verified"))
        try:
            OT.store().save_run(run)
        except Exception:
            pass
        return result.to_dict()

    return dispatch


# ---------------------------------------------------------------------------
# The child driver
# ---------------------------------------------------------------------------

def _plan(objective: str) -> list[dict]:
    plan = OT.plan_objective(objective, capabilities.as_dicts())
    if not plan["tasks"] and not plan["notes"]:
        raise SystemExit(f"could not plan any steps from: {objective}")
    return plan["tasks"]


async def _drive(objective: str) -> None:
    store = _db()
    dispatch = build_dispatch()
    executor = ContinuousTaskExecutor(store, dispatch,
                                      executor_id=f"cli-{os.getpid()}")
    try:
        created = O.compile_objective(
            store, request=objective, tasks=_plan(objective),
            manifest=capabilities.as_dicts(), objective_summary=objective)
        run_id = created["run_id"]
    except O.CompileError as exc:
        print(f"compile error: {exc}")
        return
    if not await executor.start(run_id):
        print(f"could not start run {run_id}")
        return
    deadline = time.monotonic() + MAX_DRIVE_SECONDS
    while time.monotonic() < deadline:
        run = store.objective_run(run_id)
        if run is None or run["status"] in O.RUN_TERMINAL:
            break
        await asyncio.sleep(DRIVE_POLL_SECONDS)
    run = store.objective_run(run_id)
    executor.stop()
    if run is None:
        print(f"run {run_id} vanished")
        return
    if run["status"] not in O.RUN_TERMINAL:
        print(f"timed out: run {run_id} still {run['status']}")
    summary = run.get("summary") or {}
    print(f"run {run_id} {run['status']}: "
          f"{summary.get('succeeded', 0)} succeeded, "
          f"{summary.get('failed', 0)} failed, "
          f"{summary.get('interrupted', 0)} interrupted")


# ---------------------------------------------------------------------------
# Control-plane flags
# ---------------------------------------------------------------------------

def _status(run_id: str) -> None:
    store = _db()
    found = O.active_run(store, run_id=run_id)
    if found is None:
        print("no objective run")
        return
    print(f"{found['run_id']} {found['status']} "
          f"({found['objective_summary']})")
    for row in store.objective_tasks(found["run_id"]):
        print(f"  {row['task_id']} {row['status']} "
              f"{row['capability']} "
              f"{row.get('failure_kind') or ''} "
              f"{row.get('evidence') or ''}".rstrip())


def _list() -> None:
    store = _db()
    for row in store.objective_runs(limit=10):
        print(f"{row['run_id']} {row['status']} "
              f"{row['objective_summary']}")


def _pause(run_id: str) -> None:
    store = _db()
    found = O.active_run(store, run_id=run_id)
    if found is None:
        print("no objective run")
        return
    O.pause_run(store, run_id=found["run_id"],
                reason="operator request", executor_id=f"cli-{os.getpid()}")
    print(f"paused {found['run_id']}")


def _resume(run_id: str) -> None:
    store = _db()
    found = None
    if run_id:
        found = store.objective_run(run_id)
    else:
        for row in store.objective_runs(limit=100):
            if row["status"] == O.RUN_PAUSED:
                found = row
                break
    if found is None:
        print("no paused objective run")
        return
    O.resume_run(store, run_id=found["run_id"],
                 reason="operator request", executor_id=f"cli-{os.getpid()}")
    print(f"resumed {found['run_id']}")


def _cancel(run_id: str) -> None:
    store = _db()
    found = O.active_run(store, run_id=run_id)
    if found is None:
        print("no objective run")
        return
    O.cancel_run(store, run_id=found["run_id"],
                 reason="operator request", executor_id=f"cli-{os.getpid()}")
    print(f"cancelled {found['run_id']}")


def _history(run_id: str) -> None:
    store = _db()
    found = O.active_run(store, run_id=run_id)
    if found is None:
        print("no objective run")
        return
    for row in store.objective_events(found["run_id"], limit=50):
        print(f"{row['at']} {row['event']} "
              f"{row.get('task_id') or ''} "
              f"{json.dumps(row.get('detail') or {}, default=str)}")


# ---------------------------------------------------------------------------
# Port-in-process flags (tests)
# ---------------------------------------------------------------------------

def _port(flag: str) -> None:
    from friday.policy import default_engine
    fns = OT.capability_port()
    name = {"--port-start": "objective_start",
            "--port-pause": "objective_pause",
            "--port-resume": "objective_resume",
            "--port-cancel": "objective_cancel",
            "--port-status": "objective_status"}[flag]
    run = c.Run.create(f"port {name}", capability=name)
    if name == "objective_start":
        result = fns[name](run, "check the system", engine=default_engine)
    else:
        result = fns[name](run, engine=default_engine)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    print(json.dumps(result.to_dict(), default=str, indent=2))


# ---------------------------------------------------------------------------
# Mode A parent: spawn a detached child that drives the objective
# ---------------------------------------------------------------------------

def _spawn(objective: str) -> None:
    plan = OT.plan_objective(objective, capabilities.as_dicts())
    if not plan["tasks"] and not plan["notes"]:
        raise SystemExit(f"could not plan any steps from: {objective}")
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "friday.objective_cli",
         "--objective", objective],
        cwd=os.getcwd(), env=env, **_NO_WINDOW)
    print(f"started run driver (pid {proc.pid}) for: {objective}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="friday.objective_cli")
    parser.add_argument("--objective", default="",
                        help="objective text to plan, compile and drive")
    parser.add_argument("--status", nargs="?", const="", default=None,
                        help="report the named run (or the active one)")
    parser.add_argument("--list", action="store_true", help="recent runs")
    parser.add_argument("--pause", nargs="?", const="", default=None,
                        help="pause the named run (or the active one)")
    parser.add_argument("--resume", nargs="?", const="", default=None,
                        help="resume the named paused run")
    parser.add_argument("--cancel", nargs="?", const="", default=None,
                        help="cancel the named run (or the active one)")
    parser.add_argument("--history", nargs="?", const="", default=None,
                        help="event ledger of the named run")
    parser.add_argument("--port-start", action="store_true")
    parser.add_argument("--port-pause", action="store_true")
    parser.add_argument("--port-resume", action="store_true")
    parser.add_argument("--port-cancel", action="store_true")
    parser.add_argument("--port-status", action="store_true")
    args = parser.parse_args(argv)

    if args.objective:
        asyncio.run(_drive(args.objective))
        return
    if args.status is not None:
        _status(args.status)
        return
    if args.list:
        _list()
        return
    if args.pause is not None:
        _pause(args.pause)
        return
    if args.resume is not None:
        _resume(args.resume)
        return
    if args.cancel is not None:
        _cancel(args.cancel)
        return
    if args.history is not None:
        _history(args.history)
        return
    for flag in ("--port-start", "--port-pause", "--port-resume",
                 "--port-cancel", "--port-status"):
        if getattr(args, flag[2:].replace("-", "_")):
            _port(flag)
            return
    parser.print_usage()
    raise SystemExit("no action given (use --objective, a control flag, "
                     "or a --port-* flag)")


if __name__ == "__main__":
    main()
