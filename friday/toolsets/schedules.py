"""
Schedules and conditional monitoring (PRD v3.1 FR-041, FR-042).

    FR-041  persisted one-time and recurring OBJECTIVES with budgets,
            permissions, delivery channel and result history; a schedule
            survives restart and records every execution.
    FR-042  condition-based checks that notify only when the condition is
            met (the no-noise rule).

This builds on what exists rather than adding a second scheduler:

  * the Windows Task Scheduler registration in `toolsets/automations.py`
    (survives restart and reboot; verified by `schtasks /Query`);
  * the objective engine (`objectives.compile_objective` +
    `continuous.ContinuousTaskExecutor`) for the actual work, so a
    scheduled objective has the same ledger, evidence gate and policy as
    one asked for out loud (FR-041 "same identity, policy and ledger");
  * `objective_deliveries` for handing the result to the live session.

A schedule row holds: the objective text or explicit task graph, the
trigger (once / daily / interval), budgets (retry / tokens / time),
permissions (which policy tools may be pre-approved for the run - never
CONFIRM-tier ones), the delivery channel, and an optional condition. Each
firing writes a `schedule_runs` row with the objective run id, the
condition verdict, whether anything was delivered, and the outcome.

Conditions are declarative and evaluated by code, not a model:

    {"kind": "task_output", "task": "t1", "path": "hits", "op": ">", "value": 0}
    {"kind": "task_status", "task": "t1", "op": "==", "value": "SUCCEEDED"}
    {"kind": "any_failed"}          {"kind": "always"}

When a condition is false the firing is recorded as `suppressed` and
nothing is delivered - the record is the proof that the check ran.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.store import Store

logger = logging.getLogger("friday.schedules")

TASK_PREFIX = "FridaySchedule"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
TRIGGER_KINDS = ("once", "daily", "interval", "manual")
CONDITION_KINDS = ("always", "task_output", "task_status", "any_failed")
OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: _num(a) > _num(b),
    ">=": lambda a, b: _num(a) >= _num(b),
    "<": lambda a, b: _num(a) < _num(b),
    "<=": lambda a, b: _num(a) <= _num(b),
    "contains": lambda a, b: str(b) in str(a),
    "exists": lambda a, b: a is not None,
}
DELIVERY_CHANNELS = ("session", "toast", "none")
#: Tools a schedule may pre-approve for its run. ASK-tier only; the policy
#: engine refuses CONFIRM / DENY categories in `approve_for_session`, and
#: on top of that an UNATTENDED run may not pre-approve anything at risk
#: tier R2 or above (trust.tier_of_tool): nobody is there to answer for it.
MAX_PREAPPROVALS = 8
MAX_UNATTENDED_TIER = "R1"


class ScheduleError(ValueError):
    pass


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ScheduleError(f"condition compares {v!r} as a number")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_trigger(trigger) -> dict:
    if not isinstance(trigger, dict):
        raise ScheduleError("trigger must be an object")
    kind = str(trigger.get("kind", "")).strip().lower()
    if kind == "manual":
        return {"kind": "manual"}
    if kind == "once":
        at = str(trigger.get("at", "")).strip()
        try:
            when = datetime.fromisoformat(at)
        except ValueError:
            raise ScheduleError(f"once trigger needs at: ISO datetime, got {at!r}")
        if when <= datetime.now():
            raise ScheduleError(f"once trigger {at!r} is in the past")
        return {"kind": "once", "at": when.isoformat(timespec="minutes")}
    if kind == "daily":
        at = str(trigger.get("at", "")).strip()
        if not TIME_RE.match(at):
            raise ScheduleError(f"daily trigger needs at: 'HH:MM', got {at!r}")
        return {"kind": "daily", "at": at}
    if kind == "interval":
        minutes = int(trigger.get("minutes", 0))
        if not 5 <= minutes <= 1440:
            raise ScheduleError("interval trigger needs minutes between 5 and 1440")
        return {"kind": "interval", "minutes": minutes}
    raise ScheduleError(f"unknown trigger kind {kind!r}; known: {', '.join(TRIGGER_KINDS)}")


def validate_condition(condition) -> dict:
    if condition in (None, "", {}):
        return {"kind": "always"}
    if not isinstance(condition, dict):
        raise ScheduleError("condition must be an object")
    kind = str(condition.get("kind", "")).strip().lower()
    if kind not in CONDITION_KINDS:
        raise ScheduleError(f"unknown condition kind {kind!r}; known: {', '.join(CONDITION_KINDS)}")
    if kind in ("always", "any_failed"):
        return {"kind": kind}
    task = str(condition.get("task", "")).strip()
    if not task:
        raise ScheduleError(f"{kind} condition needs task: the task id (t1, t2, ...) or capability")
    op = str(condition.get("op", "==")).strip()
    if op not in OPS:
        raise ScheduleError(f"unknown op {op!r}; known: {', '.join(OPS)}")
    out = {"kind": kind, "task": task, "op": op, "value": condition.get("value")}
    if kind == "task_output":
        out["path"] = str(condition.get("path", "")).strip()
    return out


def validate_budgets(budgets) -> dict:
    budgets = budgets or {}
    if not isinstance(budgets, dict):
        raise ScheduleError("budgets must be an object")
    out = {"retry_budget": int(budgets.get("retry_budget", 3)),
           "cost_budget_tokens": int(budgets.get("cost_budget_tokens", 0)),
           "time_budget_s": int(budgets.get("time_budget_s", 900))}
    if not 0 <= out["retry_budget"] <= 10:
        raise ScheduleError("retry_budget must be 0..10")
    if out["cost_budget_tokens"] < 0 or out["time_budget_s"] < 0:
        raise ScheduleError("budgets cannot be negative")
    return out


def validate_permissions(permissions, engine: PolicyEngine) -> list[str]:
    """Tools the run may use without asking. Only ASK-tier categories can
    be pre-approved; the policy engine refuses CONFIRM/DENY/NON_APPROVABLE
    ones, and that refusal is surfaced here at definition time."""
    permissions = permissions or []
    if isinstance(permissions, str):
        permissions = [p for p in permissions.split(",") if p.strip()]
    if not isinstance(permissions, list):
        raise ScheduleError("permissions must be a list of tool ids")
    if len(permissions) > MAX_PREAPPROVALS:
        raise ScheduleError(f"at most {MAX_PREAPPROVALS} pre-approved tools")
    from friday import trust as T
    from friday.policy import PolicyError
    probe = PolicyEngine()
    cleaned = []
    for tool_id in permissions:
        tool_id = str(tool_id).strip()
        try:
            probe.approve_for_session(tool_id)
        except PolicyError as exc:
            raise ScheduleError(f"permission {tool_id!r} cannot be granted to a schedule: {exc}")
        tier = T.tier_of_tool(tool_id)
        if tier > MAX_UNATTENDED_TIER:
            raise ScheduleError(
                f"permission {tool_id!r} cannot be granted to a schedule: risk tier {tier} "
                f"needs someone present; an unattended run may pre-approve up to {MAX_UNATTENDED_TIER}")
        cleaned.append(tool_id)
    return cleaned


def validate_delivery(delivery) -> str:
    delivery = str(delivery or "session").strip().lower()
    if delivery not in DELIVERY_CHANNELS:
        raise ScheduleError(f"delivery must be one of {', '.join(DELIVERY_CHANNELS)}")
    return delivery


# ---------------------------------------------------------------------------
# OS registration (Windows Task Scheduler), same mechanism as automations
# ---------------------------------------------------------------------------

def task_name_for(name: str) -> str:
    return f"{TASK_PREFIX}_{name}"


def _trigger_xml(trigger: dict) -> str:
    if trigger["kind"] == "once":
        when = datetime.fromisoformat(trigger["at"])
        return ("  <Triggers><TimeTrigger>\n"
                f'    <StartBoundary>{when.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>\n'
                "    <Enabled>true</Enabled>\n"
                "  </TimeTrigger></Triggers>\n")
    if trigger["kind"] == "daily":
        hour, minute = trigger["at"].split(":")
        start = datetime.now().replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
        return ("  <Triggers><CalendarTrigger>\n"
                f'    <StartBoundary>{start.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>\n'
                "    <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n"
                "    <Enabled>true</Enabled>\n"
                "  </CalendarTrigger></Triggers>\n")
    start = datetime.now() + timedelta(minutes=1)
    return ("  <Triggers><TimeTrigger>\n"
            f'    <StartBoundary>{start.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>\n'
            "    <Repetition>\n"
            f'      <Interval>PT{int(trigger["minutes"])}M</Interval>\n'
            "      <StopAtDurationEnd>false</StopAtDurationEnd>\n"
            "    </Repetition>\n"
            "    <Enabled>true</Enabled>\n"
            "  </TimeTrigger></Triggers>\n")


def task_xml(trigger: dict, command: str, arguments: str, description: str) -> str:
    safe = description.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    working = Path(__file__).resolve().parent.parent.parent
    limit = "PT2H" if trigger["kind"] == "once" else "PT30M"
    return ('<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            f"  <RegistrationInfo><Description>{safe}</Description></RegistrationInfo>\n"
            + _trigger_xml(trigger) +
            "  <Actions><Exec>\n"
            f"    <Command>{command}</Command>\n"
            f"    <Arguments>{arguments}</Arguments>\n"
            f"    <WorkingDirectory>{working}</WorkingDirectory>\n"
            "  </Exec></Actions>\n"
            "  <Settings>\n"
            "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
            "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
            "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
            "    <StartWhenAvailable>true</StartWhenAvailable>\n"
            f"    <ExecutionTimeLimit>{limit}</ExecutionTimeLimit>\n"
            "    <Enabled>true</Enabled>\n"
            "  </Settings>\n"
            "  <Principals><Principal>\n"
            "    <LogonType>InteractiveToken</LogonType>\n"
            "    <RunLevel>LeastPrivilege</RunLevel>\n"
            "  </Principal></Principals>\n"
            "</Task>")


def _pythonw() -> str:
    exe = Path(sys.executable)
    quiet = exe.with_name("pythonw.exe")
    return str(quiet if quiet.exists() else exe)


def task_exists(task_name: str) -> bool:
    result = subprocess.run(["schtasks", "/Query", "/TN", task_name],
                            capture_output=True, text=True)
    return result.returncode == 0


def delete_task(task_name: str) -> bool:
    result = subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"],
                            capture_output=True, text=True)
    return result.returncode == 0


def register_task(name: str, trigger: dict, description: str) -> str:
    """Register with the OS and confirm by querying it back."""
    task_name = task_name_for(name)
    root = Path(__file__).resolve().parent.parent.parent
    arguments = f'-m friday.toolsets.schedules --fire "{name}"'
    xml = task_xml(trigger, _pythonw(), arguments, description or f"Friday schedule: {name}")
    xml_path = root / ".ada" / f"{task_name}.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(xml, encoding="utf-16")
    try:
        result = subprocess.run(["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise ScheduleError(f"schtasks refused the task: {(result.stderr or result.stdout).strip()[:200]}")
        if not task_exists(task_name):
            raise ScheduleError(f"schtasks reported success but {task_name!r} is not registered")
    finally:
        xml_path.unlink(missing_ok=True)
    return task_name


# ---------------------------------------------------------------------------
# Condition evaluation (code, not a model)
# ---------------------------------------------------------------------------

def _task_for(condition: dict, tasks: list[dict]) -> dict | None:
    want = condition["task"]
    for i, t in enumerate(tasks, start=1):
        if t.get("task_id", "").endswith(f"-{want}") or f"t{i}" == want or t.get("capability") == want:
            return t
    return None


def _dig(value, path: str):
    for part in [p for p in path.split(".") if p]:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit():
            value = value[int(part)] if int(part) < len(value) else None
        else:
            return None
    return value


def evaluate_condition(condition: dict, tasks: list[dict]) -> tuple[bool, str]:
    """(met, why). Never raises for a bad path: an unmet condition with the
    reason attached is the safe outcome for a monitor."""
    kind = condition.get("kind", "always")
    if kind == "always":
        return True, "no condition"
    if kind == "any_failed":
        failed = [t["capability"] for t in tasks if t.get("status") in ("FAILED", "BLOCKED", "SKIPPED")]
        return (bool(failed), f"failed: {failed}" if failed else "nothing failed")
    task = _task_for(condition, tasks)
    if task is None:
        return False, f"task {condition['task']!r} not in the run"
    op = OPS[condition["op"]]
    if kind == "task_status":
        actual = task.get("status")
    else:
        result = task.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                pass
        actual = _dig(result, condition.get("path", ""))
        if isinstance(result, dict) and actual is None and "output" in result:
            actual = _dig(result["output"], condition.get("path", ""))
    try:
        met = bool(op(actual, condition.get("value")))
    except ScheduleError as exc:
        return False, str(exc)
    return met, f"{kind} {condition.get('path') or ''} {actual!r} {condition['op']} {condition.get('value')!r}"


# ---------------------------------------------------------------------------
# Running one firing
# ---------------------------------------------------------------------------

async def fire(name: str, *, store: Store, dispatch, fired_by: str = "schedule",
               engine: PolicyEngine | None = None, wait_s: float = 900.0) -> dict:
    """Compile the schedule's objective, run it through the real engine,
    evaluate the condition, deliver (or suppress), record everything."""
    from friday import capabilities
    from friday import objectives as O
    from friday.continuous import ContinuousTaskExecutor
    from friday.toolsets import objectives as OT

    row = store.get_schedule(name)
    if row is None:
        raise ScheduleError(f"no schedule named {name!r}")
    if not row["enabled"]:
        raise ScheduleError(f"schedule {name!r} is disabled")

    firing_id = c.new_run_id()
    store.start_schedule_run(firing_id, name, fired_by)
    engine = engine or PolicyEngine()
    for tool_id in row["permissions"]:
        try:
            engine.approve_for_session(tool_id)
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
            logger.warning("schedule %s: permission %s not honoured: %s", name, tool_id, exc)

    tasks = row["tasks"]
    manifest = capabilities.as_dicts()
    if not tasks:
        plan = OT.plan_objective(row["objective"], manifest)
        tasks = plan["tasks"]
    else:
        # Explicit task graphs may name capabilities the planner does not
        # advertise (a fabric operation, a test double); compile still
        # validates the GRAPH, and an id nobody can dispatch fails at run
        # time as CAPABILITY_MISSING - visibly, in the ledger.
        known = {m["id"] for m in manifest}
        manifest = manifest + [{"id": t["capability"], "description": "declared by the schedule"}
                               for t in tasks if t.get("capability") not in known]
    outcome = {"firing_id": firing_id, "name": name, "fired_by": fired_by}
    try:
        created = O.compile_objective(
            store, request=row["objective"], tasks=tasks,
            manifest=manifest, objective_summary=row["objective"])
        run_id = created["run_id"]
        store.touch_objective_run(
            run_id, source_channel=f"schedule:{name}",
            retry_budget=row["budgets"]["retry_budget"],
            cost_budget_tokens=row["budgets"]["cost_budget_tokens"],
            time_budget_s=row["budgets"]["time_budget_s"],
            approvals=json.dumps(row["permissions"]))
        executor = ContinuousTaskExecutor(store, dispatch, executor_id=f"schedule-{name}")
        executor.stop()
        deadline = asyncio.get_event_loop().time() + wait_s
        await executor.start(run_id)
        while store.objective_run(run_id)["status"] not in O.RUN_TERMINAL:
            if asyncio.get_event_loop().time() > deadline:
                break
            await asyncio.sleep(0.1)
        final = store.objective_run(run_id)
        task_rows = store.objective_tasks(run_id)
        met, why = evaluate_condition(row["condition"], task_rows)
        delivered_via = ""
        if met and row["delivery"] != "none":
            message = _message_for(name, final, task_rows, why)
            if row["delivery"] == "session":
                store.create_objective_delivery(run_id, message)
                delivered_via = "session_queue"
            elif row["delivery"] == "toast":
                delivered_via = "toast" if _toast(name, message) else "toast_failed"
        outcome.update({"run_id": run_id, "status": final["status"],
                        "condition_met": met, "condition_detail": why,
                        "delivered": bool(delivered_via), "delivered_via": delivered_via,
                        "suppressed": (not met) and row["delivery"] != "none"})
        store.finish_schedule_run(firing_id, run_id=run_id, status=final["status"],
                                  condition_met=met, condition_detail=why,
                                  delivered_via=delivered_via)
        if row["trigger"]["kind"] == "once":
            store.save_schedule(name, **{**_definition(row), "enabled": False})
            if row.get("task_name"):
                delete_task(row["task_name"])
    except Exception as exc:  # noqa: BLE001
        store.finish_schedule_run(firing_id, run_id=outcome.get("run_id"), status="ERROR",
                                  condition_met=False, condition_detail=f"{type(exc).__name__}: {exc}",
                                  delivered_via="")
        outcome.update({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"})
    return outcome


def _definition(row: dict) -> dict:
    return {"objective": row["objective"], "tasks": row["tasks"], "trigger": row["trigger"],
            "budgets": row["budgets"], "permissions": row["permissions"],
            "delivery": row["delivery"], "condition": row["condition"],
            "description": row.get("description", ""), "task_name": row.get("task_name")}


def _message_for(name: str, run: dict, tasks: list[dict], why: str) -> str:
    done = sum(1 for t in tasks if t.get("status") == "SUCCEEDED")
    head = f"Scheduled objective {name} {run['status'].lower()}: {done} of {len(tasks)} steps."
    if why and why != "no condition":
        head += f" Condition met ({why})."
    return head


def _toast(name: str, message: str) -> bool:
    try:
        from friday.toolsets import reminders as R
        script = R._fire_script(0, message, "") if hasattr(R, "_fire_script") else ""
        if not script:
            return False
        return subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=60).returncode == 0
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Tools (run-first; MCP faces in friday/tools/schedule_control.py)
# ---------------------------------------------------------------------------

def store() -> Store:
    from friday.toolsets.objectives import store as objective_store
    return objective_store()


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    from friday.toolsets.system import APPROVAL_PREFIX
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    started = c.started(run.run_id, tool_id)
    return run.record(started.finish(
        status=c.CANCELLED, error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]"))


def schedules_create(run: c.Run, name: str, objective: str, trigger: str, *,
                     tasks: str = "", budgets: str = "", permissions: str = "",
                     delivery: str = "session", condition: str = "", description: str = "",
                     engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Persist a scheduled objective and register it with the OS."""
    tool_id = "schedules.create"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    try:
        name = (name or "").strip().lower()
        if not NAME_RE.match(name):
            raise ScheduleError(f"name {name!r} must be lowercase letters, digits, - or _")
        if not (objective or "").strip() and not (tasks or "").strip():
            raise ScheduleError("a schedule needs an objective (or explicit tasks)")
        loads = lambda s, d: (json.loads(s) if isinstance(s, str) and s.strip() else (s if not isinstance(s, str) else d))  # noqa: E731
        wanted = validate_trigger(loads(trigger, {}))
        explicit = loads(tasks, [])
        if explicit and not isinstance(explicit, list):
            raise ScheduleError("tasks must be a JSON list")
        budget = validate_budgets(loads(budgets, {}))
        perms = validate_permissions(loads(permissions, []) if permissions.strip().startswith("[") else permissions, engine)
        channel = validate_delivery(delivery)
        cond = validate_condition(loads(condition, {}))
    except (ScheduleError, json.JSONDecodeError, ValueError) as exc:
        return run.record(c.failed(started, str(exc)))

    db = store()
    existing = db.get_schedule(name)
    if existing and existing.get("task_name"):
        delete_task(existing["task_name"])
    task_name = None
    if wanted["kind"] != "manual":
        try:
            task_name = register_task(name, wanted, description)
        except ScheduleError as exc:
            return run.record(c.failed(started, str(exc)))
    db.save_schedule(name, objective=objective.strip(), tasks=explicit, trigger=wanted,
                     budgets=budget, permissions=perms, delivery=channel, condition=cond,
                     description=description, task_name=task_name)
    if task_name:
        method, evidence = "schtasks_query", f"task {task_name!r} confirmed registered via schtasks /Query"
    else:
        method, evidence = "stored_definition", "stored; manual trigger, no OS task claimed"
    return run.record(c.succeeded(
        started, output={"name": name, "trigger": wanted, "task_name": task_name,
                         "budgets": budget, "permissions": perms, "delivery": channel,
                         "condition": cond},
        verification=c.Verification(method=method, evidence=evidence)))


def schedules_list(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    tool_id = "schedules.list"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    rows = store().schedules()
    for row in rows:
        row["os_task_registered"] = bool(row.get("task_name")) and task_exists(row["task_name"])
    return run.record(c.succeeded(
        started, output={"schedules": rows, "count": len(rows)},
        verification=c.Verification(method="store_read", evidence=f"{len(rows)} schedule(s)")))


async def schedules_run(run: c.Run, name: str, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Fire a schedule now, in this process, through the real engine."""
    tool_id = "schedules.run"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    from friday.objective_cli import build_dispatch
    try:
        outcome = await fire(name, store=store(), dispatch=build_dispatch(), fired_by="hand")
    except ScheduleError as exc:
        return run.record(c.failed(started, str(exc)))
    if outcome.get("status") == "ERROR":
        return run.record(c.failed(started, outcome.get("error", "firing failed")))
    return run.record(c.succeeded(
        started, output=outcome,
        verification=c.Verification(
            method="objective_ledger",
            evidence=f"run {outcome['run_id']} {outcome['status']}; condition_met={outcome['condition_met']}; "
                     f"delivered={outcome['delivered']}")))


def schedules_history(run: c.Run, *, name: str = "", limit: int = 20,
                      engine: PolicyEngine = default_engine) -> c.ActionResult:
    tool_id = "schedules.history"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    rows = store().schedule_history(name.strip() or None, limit=max(1, int(limit)))
    return run.record(c.succeeded(
        started, output={"runs": rows, "count": len(rows)},
        verification=c.Verification(method="store_read", evidence=f"{len(rows)} firing(s) read")))


def schedules_delete(run: c.Run, name: str, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    tool_id = "schedules.delete"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    db = store()
    row = db.get_schedule(name.strip().lower())
    if row is None:
        return run.record(c.failed(started, f"no schedule named {name!r}"))
    removed_os = delete_task(row["task_name"]) if row.get("task_name") else None
    db.delete_schedule(row["name"])
    still = task_exists(row["task_name"]) if row.get("task_name") else False
    if still:
        return run.record(c.partial(started, f"row removed but OS task {row['task_name']!r} is still registered"))
    return run.record(c.succeeded(
        started, output={"name": row["name"], "os_task_removed": removed_os},
        verification=c.Verification(method="schtasks_query",
                                    evidence="OS task absent after delete" if row.get("task_name")
                                    else "no OS task existed")))


# ---------------------------------------------------------------------------
# What the OS task runs
# ---------------------------------------------------------------------------

def _fire_from_os(name: str) -> int:
    log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, filename=str(log_dir / "schedules.log"),
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    except OSError:
        logging.basicConfig(level=logging.INFO)
    from friday.objective_cli import build_dispatch
    try:
        outcome = asyncio.run(fire(name, store=store(), dispatch=build_dispatch(), fired_by="schedule"))
    except Exception:
        logger.exception("schedule %r could not fire", name)
        return 1
    logger.info("schedule %r: %s", name, outcome)
    return 0 if outcome.get("status") not in ("ERROR", "FAILED") else 1


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fire one Friday schedule.")
    parser.add_argument("--fire", required=True)
    sys.exit(_fire_from_os(parser.parse_args().fire))
