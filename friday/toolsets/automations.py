"""
Automations: a trigger that actually fires, a step graph, and a result you can
check the morning after.

This is section 14, built rather than ported. The donor
(`Mark-L/actions/automation.py`, CC BY-NC, so architecture only and no source)
had the right first instinct - workflows are *data*, not code, so a new
automation is a row rather than a deploy - and four things this needs:

  a trigger        it wrote `"trigger": "manual"` into the file and read it
                   back only to print it. Nothing dispatched on it. Here the
                   field is load-bearing: a scheduled automation registers a
                   Windows task and the task name is stored, so "is this
                   armed?" is answerable against `schtasks /Query` rather than
                   against our own JSON.
  a graph          it ran a flat list. A step here declares `needs`, and a step
                   whose dependency failed is *skipped and recorded as
                   skipped*, not run into the same failure. That is the whole
                   difference between a graph and a list.
  a retry policy   it had none.
  no shell         it had a `{"shell": "..."}` step, which turns "run
                   automation X" into an arbitrary-command primitive the moment
                   the model can choose X. There is no shell step here and
                   there will not be one. Steps may only name capabilities from
                   TOOLS below, every one of which applies the policy engine
                   itself.

The scheduling architecture is the one `reminders.py` already proved: the
operating system owns the schedule, so an automation survives Friday exiting,
the machine sleeping and a reboot. An asyncio timer would not, and an
automation that only runs while you are watching it is not automation.

Steps run in-process rather than through the MCP server on purpose. An
automation that fires at 08:00 must not depend on the voice agent being up.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from friday import config
from friday import contracts as c
from friday import dag
from friday.policy import PolicyEngine, default_engine
from friday.store import Store
from friday.toolsets import memory, music, research, system, vision, web
from friday.toolsets.system import APPROVAL_PREFIX

logger = logging.getLogger("friday.automations")

EXECUTION_SCOPE = "local_machine"
TASK_PREFIX = "ADAAutomation"

#: The security boundary, and deliberately a short list.
#:
#: A step may name one of these and nothing else. Every entry is a normal
#: Friday capability that applies the policy engine itself, so an automation
#: cannot reach past what the same call would be allowed to do when the boss
#: asks for it out loud. Adding an entry here is a decision to let unattended
#: code run it at 3am with nobody watching, which is a higher bar than "the
#: tool exists".
#:
#: Absent on purpose: anything that writes to the filesystem, closes apps,
#: executes commands, or spends money.
TOOLS: dict[str, object] = {
    "web.search": web.web_search,
    "web.news": web.web_news,
    "web.fetch": web.web_fetch,
    "web.answer": research.web_answer,
    "web.deep_research": research.web_deep_research,
    "memory.remember": memory.memory_remember,
    "memory.search": memory.memory_search,
    "music.play": music.music_play,
    "music.play_mood": music.music_play_mood,
    "music.stop": music.music_stop,
    "system.get_info": system.system_get_info,
    "vision.screen_capture": vision.screen_capture,
}

#: Per-step ceiling. A retry loop with no ceiling is an outage amplifier.
MAX_RETRIES = 3
MAX_STEPS = 20

_STORE: Store | None = None


def store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store()
    return _STORE


def reset_store(new: Store | None = None) -> None:
    """Point the module at another database. Tests use this."""
    global _STORE
    _STORE = new


class AutomationError(ValueError):
    """The definition is wrong in a way that must be refused at save time."""


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


# ---------------------------------------------------------------------------
# Validation - all of it at save time
# ---------------------------------------------------------------------------

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
STEP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def validate_steps(steps) -> list[dict]:
    """
    Refuse a broken graph now rather than at 3am.

    Returns the steps in topological order, so a definition that survives this
    is one the runner can execute without thinking about ordering again.
    """
    if not isinstance(steps, list) or not steps:
        raise AutomationError("an automation needs at least one step")
    if len(steps) > MAX_STEPS:
        raise AutomationError(f"too many steps ({len(steps)} > {MAX_STEPS})")

    seen: dict[str, dict] = {}
    for raw in steps:
        if not isinstance(raw, dict):
            raise AutomationError(f"a step must be an object, got {type(raw).__name__}")
        if "shell" in raw or "command" in raw:
            raise AutomationError(
                "shell steps do not exist here - a step may only name a "
                f"capability from: {', '.join(sorted(TOOLS))}"
            )
        step_id = str(raw.get("id", "")).strip()
        if not STEP_ID_RE.match(step_id):
            raise AutomationError(
                f"step id {step_id!r} must be lowercase letters, digits, - or _")
        if step_id in seen:
            raise AutomationError(f"two steps share the id {step_id!r}")

        tool = str(raw.get("tool", "")).strip()
        if tool not in TOOLS:
            raise AutomationError(
                f"step {step_id!r} names {tool!r}, which is not an automatable "
                f"capability. Known: {', '.join(sorted(TOOLS))}")

        args = raw.get("args") or {}
        if not isinstance(args, dict):
            raise AutomationError(f"step {step_id!r}: args must be an object")
        _check_args(step_id, tool, args)

        retries = int(raw.get("retries", 0))
        if not 0 <= retries <= MAX_RETRIES:
            raise AutomationError(
                f"step {step_id!r}: retries must be 0..{MAX_RETRIES}")

        needs = raw.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        if not isinstance(needs, list):
            raise AutomationError(f"step {step_id!r}: needs must be a list")

        seen[step_id] = {"id": step_id, "tool": tool, "args": args,
                         "needs": [str(n) for n in needs], "retries": retries}

    for step in seen.values():
        for need in step["needs"]:
            if need not in seen:
                raise AutomationError(
                    f"step {step['id']!r} needs {need!r}, which does not exist")
            if need == step["id"]:
                raise AutomationError(f"step {step['id']!r} needs itself")

    return _topological(seen)


def _check_args(step_id: str, tool: str, args: dict) -> None:
    """Bind the arguments against the real signature, so a typo fails now."""
    signature = inspect.signature(TOOLS[tool])
    allowed = {
        name for name, param in signature.parameters.items()
        if name not in ("run", "engine")
        and param.kind is not inspect.Parameter.VAR_KEYWORD
    }
    takes_anything = any(param.kind is inspect.Parameter.VAR_KEYWORD
                         for param in signature.parameters.values())
    unknown = set() if takes_anything else set(args) - allowed
    if unknown:
        raise AutomationError(
            f"step {step_id!r}: {tool} takes no argument(s) "
            f"{', '.join(sorted(unknown))}. It takes: {', '.join(sorted(allowed)) or 'none'}")
    required = [
        name for name, param in signature.parameters.items()
        if name not in ("run", "engine") and param.default is inspect.Parameter.empty
        and param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
    ]
    # A placeholder counts as supplied: it is filled at run time.
    missing = [name for name in required if name not in args]
    if missing:
        raise AutomationError(
            f"step {step_id!r}: {tool} needs {', '.join(missing)}")


def _topological(steps: dict[str, dict]) -> list[dict]:
    """Dependency order. The algorithm lives in friday.dag; both users share it."""
    try:
        order = dag.topological({sid: list(step["needs"])
                                 for sid, step in steps.items()})
    except dag.CycleError as exc:
        raise AutomationError(str(exc)) from exc
    return [steps[sid] for sid in order]


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def validate_trigger(trigger) -> dict:
    if not isinstance(trigger, dict):
        raise AutomationError("trigger must be an object")
    kind = str(trigger.get("kind", "")).strip().lower()
    if kind == "manual":
        return {"kind": "manual"}
    if kind == "daily":
        at = str(trigger.get("at", "")).strip()
        if not TIME_RE.match(at):
            raise AutomationError(
                f"daily trigger needs at: 'HH:MM' in 24-hour time, got {at!r}")
        return {"kind": "daily", "at": at}
    if kind == "interval":
        minutes = int(trigger.get("minutes", 0))
        if not 5 <= minutes <= 1440:
            raise AutomationError(
                "interval trigger needs minutes between 5 and 1440; below five "
                "minutes this is a polling loop wearing a scheduler's clothes")
        return {"kind": "interval", "minutes": minutes}
    raise AutomationError(
        f"unknown trigger kind {kind!r}; known: manual, daily, interval")


def task_name_for(name: str) -> str:
    return f"{TASK_PREFIX}_{name}"


def _trigger_xml(trigger: dict) -> str:
    """The <Triggers> block. Start tomorrow-safe: today at HH:MM, repeating."""
    if trigger["kind"] == "daily":
        hour, minute = trigger["at"].split(":")
        start = datetime.now().replace(
            hour=int(hour), minute=int(minute), second=0, microsecond=0)
        return (
            "  <Triggers><CalendarTrigger>\n"
            f'    <StartBoundary>{start.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>\n'
            "    <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n"
            "    <Enabled>true</Enabled>\n"
            "  </CalendarTrigger></Triggers>\n"
        )
    start = datetime.now() + timedelta(minutes=1)
    return (
        "  <Triggers><TimeTrigger>\n"
        f'    <StartBoundary>{start.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>\n'
        "    <Repetition>\n"
        f'      <Interval>PT{int(trigger["minutes"])}M</Interval>\n'
        "      <StopAtDurationEnd>false</StopAtDurationEnd>\n"
        "    </Repetition>\n"
        "    <Enabled>true</Enabled>\n"
        "  </TimeTrigger></Triggers>\n"
    )


def _task_xml(trigger: dict, command: str, arguments: str, description: str) -> str:
    safe = description.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Task Scheduler starts a process in System32. Saying so explicitly costs
    # one element and removes a whole class of "it ran, but somewhere else".
    working = Path(__file__).resolve().parent.parent.parent
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        f"  <RegistrationInfo><Description>{safe}</Description></RegistrationInfo>\n"
        + _trigger_xml(trigger) +
        "  <Actions><Exec>\n"
        f"    <Command>{command}</Command>\n"
        f"    <Arguments>{arguments}</Arguments>\n"
        f"    <WorkingDirectory>{working}</WorkingDirectory>\n"
        "  </Exec></Actions>\n"
        "  <Settings>\n"
        # An automation that is still running when its next tick arrives must
        # not stack a second copy on top of the first.
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>\n"
        "    <Enabled>true</Enabled>\n"
        "  </Settings>\n"
        "  <Principals><Principal>\n"
        "    <LogonType>InteractiveToken</LogonType>\n"
        "    <RunLevel>LeastPrivilege</RunLevel>\n"
        "  </Principal></Principals>\n"
        "</Task>"
    )


def _pythonw() -> str:
    exe = Path(sys.executable)
    windowless = exe.parent / "pythonw.exe"
    return str(windowless if windowless.exists() else exe)


def task_exists(task_name: str) -> bool:
    """Ask the scheduler, rather than trusting our own record of it."""
    if sys.platform != "win32":
        return False
    return subprocess.run(
        ["schtasks", "/Query", "/TN", task_name],
        capture_output=True, text=True,
    ).returncode == 0


def _delete_task(task_name: str) -> bool:
    if sys.platform != "win32":
        return False
    return subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True, text=True,
    ).returncode == 0


def _register_task(name: str, trigger: dict, description: str) -> str:
    """Register the OS task and confirm it exists. Returns the task name."""
    task_name = task_name_for(name)
    root = Path(__file__).resolve().parent.parent.parent
    arguments = f'-m friday.toolsets.automations --fire "{name}"'
    xml = _task_xml(trigger, _pythonw(), arguments, description or f"ADA: {name}")

    xml_path = root / ".ada" / f"{task_name}.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(xml, encoding="utf-16")
    try:
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AutomationError(
                f"schtasks refused the task: {(result.stderr or result.stdout).strip()[:200]}")
        if not task_exists(task_name):
            raise AutomationError(
                f"schtasks reported success but {task_name!r} is not registered")
    finally:
        xml_path.unlink(missing_ok=True)
    return task_name


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_.-]+)\s*\}\}", re.IGNORECASE)


def resolve(value, context: dict):
    """
    Fill `{{vars.x}}` and `{{steps.<id>.<key>}}` from what has run so far.

    An unresolved placeholder is left as written rather than replaced with an
    empty string: a step that silently searched for "" would report success
    having done nothing, which is the exact shape of failure this project
    refuses to ship.
    """
    if isinstance(value, dict):
        return {k: resolve(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, context) for v in value]
    if not isinstance(value, str):
        return value

    def swap(match):
        cursor = context
        for part in match.group(1).split("."):
            if isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
            else:
                return match.group(0)
        return cursor if isinstance(cursor, str) else json.dumps(cursor)

    return PLACEHOLDER.sub(swap, value)


async def _call(tool: str, run: c.Run, args: dict,
                engine: PolicyEngine) -> c.ActionResult:
    fn = TOOLS[tool]
    result = fn(run, engine=engine, **args)
    if inspect.isawaitable(result):
        result = await result
    return result


async def execute(name: str, *, variables: dict | None = None,
                  fired_by: str = "manual",
                  engine: PolicyEngine = default_engine) -> dict:
    """
    Run one automation to completion and persist every step's outcome.

    Returns the run record. Never raises for a step failure - a failed step is
    a recorded outcome, not an exception, because the point of the table is
    that the morning after is answerable.
    """
    definition = store().get_automation(name)
    if definition is None:
        raise AutomationError(f"no automation named {name!r}")
    if not definition["enabled"]:
        raise AutomationError(f"automation {name!r} is disabled")

    steps = validate_steps(definition["steps"])  # cheap, and the table can rot
    run_id = c.new_run_id()
    runtime = config.runtime_paths()
    runtime["task_name"] = definition.get("task_name") or ""
    store().start_automation_run(run_id, name, fired_by, runtime)

    context = {"vars": dict(variables or {}), "steps": {}}
    records: list[dict] = []
    failed: set[str] = set()

    for step in steps:
        blocked = sorted(set(step["needs"]) & failed)
        if blocked:
            records.append({"id": step["id"], "tool": step["tool"],
                            "status": "skipped", "attempts": 0,
                            "error": f"depends on {', '.join(blocked)}, which did not succeed"})
            failed.add(step["id"])
            continue

        args = resolve(step["args"], context)
        run = c.Run.create(f"automation {name}: {step['id']}", capability="automation")
        attempts, outcome, error = 0, None, None
        started = time.monotonic()
        for attempt in range(step["retries"] + 1):
            attempts = attempt + 1
            try:
                outcome = await _call(step["tool"], run, args, engine)
            except Exception as exc:            # a tool that raised is a failure,
                error = f"{type(exc).__name__}: {exc}"   # not a crashed automation
                outcome = None
            else:
                error = outcome.error
                if outcome.status == c.SUCCEEDED:
                    break
            if attempt < step["retries"]:
                await asyncio.sleep(min(2 ** attempt, 8))

        ok = outcome is not None and outcome.status == c.SUCCEEDED
        if ok:
            context["steps"][step["id"]] = dict(outcome.output or {})
        else:
            failed.add(step["id"])
        records.append({
            "id": step["id"], "tool": step["tool"],
            "status": outcome.status if outcome is not None else c.FAILED,
            "attempts": attempts, "error": None if ok else error,
            "evidence": (outcome.verification.evidence
                         if ok and outcome.verification else None),
            "took_ms": int((time.monotonic() - started) * 1000),
        })

    succeeded = [r for r in records if r["status"] == c.SUCCEEDED]
    if len(succeeded) == len(records):
        status = c.SUCCEEDED
    elif succeeded:
        status = c.PARTIAL
    else:
        status = c.FAILED
    store().finish_automation_run(
        run_id, status=status, steps=records,
        error=None if status == c.SUCCEEDED else
        "; ".join(f"{r['id']}: {r['error']}" for r in records if r["error"]))

    return {"run_id": run_id, "name": name, "status": status,
            "fired_by": fired_by, "steps": records}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def automations_create(
    run: c.Run, name: str, trigger: str, steps: str, *, description: str = "",
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Define an automation. `trigger` and `steps` are JSON.

    Verification is the registered scheduled task, queried back from the OS -
    not the row we just wrote.
    """
    tool_id = "automations.create"
    denied = _gate(run, tool_id, engine)
    if denied is not None:
        return denied
    result = run.record(c.started(run.run_id, tool_id))

    try:
        name = (name or "").strip().lower()
        if not NAME_RE.match(name):
            raise AutomationError(
                f"name {name!r} must be lowercase letters, digits, - or _")
        wanted = validate_trigger(
            json.loads(trigger) if isinstance(trigger, str) else trigger)
        ordered = validate_steps(
            json.loads(steps) if isinstance(steps, str) else steps)
    except (AutomationError, json.JSONDecodeError, ValueError) as exc:
        return run.record(c.failed(result, str(exc)))

    existing = store().get_automation(name)
    if existing and existing.get("task_name"):
        _delete_task(existing["task_name"])       # replace, never accumulate

    task_name = None
    if wanted["kind"] != "manual":
        try:
            task_name = _register_task(name, wanted, description)
        except AutomationError as exc:
            return run.record(c.failed(result, str(exc)))

    store().save_automation(name, trigger=wanted, steps=ordered,
                            description=description, task_name=task_name)

    if task_name:
        method, evidence = "schtasks_query", (
            f"task {task_name!r} confirmed registered via schtasks /Query; "
            f"{len(ordered)} step(s)")
    else:
        method, evidence = "stored_definition", (
            f"stored with {len(ordered)} step(s); trigger is manual so "
            f"no scheduled task exists, and none is claimed")
    return run.record(c.succeeded(
        result,
        output=_scoped({"name": name, "trigger": wanted, "task_name": task_name,
                        "steps": [s["id"] for s in ordered]}),
        verification=c.Verification(method=method, evidence=evidence),
    ))


def automations_list(run: c.Run, *,
                     engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Every automation, and whether the OS agrees it is armed."""
    tool_id = "automations.list"
    denied = _gate(run, tool_id, engine)
    if denied is not None:
        return denied
    result = run.record(c.started(run.run_id, tool_id))

    rows = []
    for row in store().automations():
        armed = bool(row["task_name"]) and task_exists(row["task_name"])
        rows.append({
            "name": row["name"], "description": row["description"],
            "trigger": row["trigger"], "steps": [s.get("id") for s in row["steps"]],
            "enabled": row["enabled"], "task_name": row["task_name"],
            # The disagreement is worth surfacing: a row saying "daily" whose
            # task was deleted in Task Scheduler is not a daily automation.
            "armed": armed,
            "orphaned": bool(row["task_name"]) and not armed,
        })
    return run.record(c.succeeded(
        result, output=_scoped({"automations": rows, "count": len(rows)}),
        verification=c.Verification(
            method="store_read_plus_schtasks_query",
            evidence=f"{len(rows)} automation(s) read from the store; each "
                     f"scheduled one checked against schtasks"),
    ))


async def automations_run(
    run: c.Run, name: str, *, variables: str = "",
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Fire an automation now. `variables` is JSON, and may be empty."""
    tool_id = "automations.run"
    denied = _gate(run, tool_id, engine)
    if denied is not None:
        return denied
    result = run.record(c.started(run.run_id, tool_id))

    try:
        supplied = json.loads(variables) if (variables or "").strip() else {}
        if not isinstance(supplied, dict):
            raise AutomationError("variables must be a JSON object")
        record = await execute(name, variables=supplied, fired_by="hand",
                               engine=engine)
    except (AutomationError, json.JSONDecodeError) as exc:
        return run.record(c.failed(result, str(exc)))

    done = [s for s in record["steps"] if s["status"] == c.SUCCEEDED]
    evidence = (f"run {record['run_id']}: {len(done)}/{len(record['steps'])} "
                f"step(s) verified - "
                + ", ".join(f"{s['id']}={s['status']}" for s in record["steps"]))
    if record["status"] == c.SUCCEEDED:
        return run.record(c.succeeded(
            result, output=_scoped(record),
            verification=c.Verification(method="per_step_results",
                                        evidence=evidence)))
    if record["status"] == c.PARTIAL:
        return run.record(c.partial(
            result, f"not every step succeeded - {evidence}",
            output=_scoped(record)))
    return run.record(c.failed(
        result, "; ".join(f"{s['id']}: {s['error']}" for s in record["steps"]
                          if s["error"])[:400] or "every step failed"))


def automations_history(
    run: c.Run, *, name: str = "", limit: int = 10,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """What the automations actually did, per step."""
    tool_id = "automations.history"
    denied = _gate(run, tool_id, engine)
    if denied is not None:
        return denied
    result = run.record(c.started(run.run_id, tool_id))

    rows = store().automation_history(name.strip() or None, limit=max(1, limit))
    return run.record(c.succeeded(
        result, output=_scoped({"runs": rows, "count": len(rows)}),
        verification=c.Verification(
            method="store_read",
            evidence=f"{len(rows)} recorded run(s) read from automation_runs"),
    ))


def automations_delete(run: c.Run, name: str, *,
                       engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Remove an automation and disarm its scheduled task."""
    tool_id = "automations.delete"
    denied = _gate(run, tool_id, engine)
    if denied is not None:
        return denied
    result = run.record(c.started(run.run_id, tool_id))

    existing = store().get_automation(name)
    if existing is None:
        return run.record(c.failed(result, f"no automation named {name!r}"))

    task_name = existing.get("task_name")
    if task_name:
        _delete_task(task_name)
        if task_exists(task_name):
            return run.record(c.failed(
                result, f"{name!r} was not deleted: its scheduled task "
                        f"{task_name!r} is still registered"))
    store().delete_automation(name)
    return run.record(c.succeeded(
        result, output=_scoped({"name": name, "task_name": task_name}),
        verification=c.Verification(
            method="schtasks_query" if task_name else "store_delete",
            evidence=(f"row deleted; task {task_name!r} confirmed gone via "
                      f"schtasks /Query" if task_name else
                      "row deleted; it had no scheduled task")),
    ))


# ---------------------------------------------------------------------------
# The scheduled entry point
# ---------------------------------------------------------------------------


def _fire(name: str) -> int:
    """
    What the OS task runs. Records its own outcome, then exits.

    Logging goes to a file because this runs under pythonw.exe, which has no
    console: a run that dies before it can write to the database would
    otherwise leave no trace at all, anywhere. "Friday works while I am away"
    cannot be built on a process that fails invisibly.
    """
    log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO, filename=str(log_dir / "automations.log"),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    except OSError:
        logging.basicConfig(level=logging.INFO)
    logger.info("fired: %r (cwd=%s)", name, Path.cwd())
    try:
        record = asyncio.run(execute(name, fired_by="schedule"))
    except Exception:
        logger.exception("automation %r could not run", name)
        return 1
    logger.info("automation %r: %s", name, record["status"])
    return 0 if record["status"] == c.SUCCEEDED else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run one automation.")
    parser.add_argument("--fire", required=True, help="automation name")
    sys.exit(_fire(parser.parse_args().fire))
