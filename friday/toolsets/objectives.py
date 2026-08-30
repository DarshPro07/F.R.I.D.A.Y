"""
Objectives toolset (Phase 3): the multi-step run book.

One objective is a durable, compiled task graph that Friday executes on its
own - no "continue" required. This module provides:

  * the deterministic planner (`plan_objective`): natural language -> tasks
  * the toolset entry points the MCP adapter and the CLI both use

Everything here is store-level and lease-free: starting a run writes durable
rows and schedules an immediate wake for whatever driver loop is alive;
pausing, resuming and cancelling flip statuses that the driver loop, the
per-task re-check and the watchdog observe at the next task boundary. The
same asymmetry as power: hard to start (it is compiled and validated),
trivial to stop.

The planner is deliberately not the LLM. The model's plan is validated by
`compile_objective` and is still the honest path for complex work; the
planner exists so a single spoken sentence can become a run without any
generation at all, and so the demo works end to end in one voice turn. It is
also honest about what it cannot do: a clause it cannot map to a real
capability becomes a task with the unmapped capability, which compile
persists as immediately FAILED/CAPABILITY_MISSING - a recorded failure,
never a hallucinated success.
"""

from __future__ import annotations

import json
import os
import re

from friday import capabilities, contracts as c
from friday import objectives as O
from friday.capability_router import _phrase_score
from friday.policy import PolicyEngine, default_engine
from friday.store import DEFAULT_DB, Store
from friday.toolsets.system import APPROVAL_PREFIX


EXECUTION_SCOPE = "local_machine"

#: The capability a planner clause lands on when no rule maps it and no
#: manifest entry scores. Compile persists it as an immediate
#: FAILED/CAPABILITY_MISSING task, so the run ends PARTIAL and the failure
#: is narrated - never silently dropped, never hallucinated.
UNMAPPED_CAPABILITY = O.UNMAPPED_CAPABILITY

_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
    return _store


def reset_store(new: Store | None = None) -> None:
    global _store
    if _store is not None and new is not _store:
        try:
            _store.close()
        except Exception:
            pass
    _store = new


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
# The planner
# ---------------------------------------------------------------------------

#: A clause is separated by commas or "then". NOT by bare "and": a compound
#: like "create and clean up a temporary note" is one clause and is handled
#: before any splitting happens. The comma alternative matches only the
#: comma - consuming the whitespace after it would leave "then" stranded
#: with no separator left to match it.
_SPLIT_CLAUSES = re.compile(r",|\s+then\s+|\bthen\b", re.I)

_OPEN_APP = re.compile(r"^(?:open|start|launch|run)\s+(.+)$", re.I)
_SEARCH = re.compile(r"^(?:find|search\s+for?|look\s+up|research)\s+(.+)$", re.I)
#: Deterministic health check. "system" and "computer" are included so a
#: "check the system" phrase cannot fall through to the scoring fallback and
#: collide with an unrelated tool whose examples also mention the word
#: "system" (e.g. audio_master_volume's "set the system volume...").
_CHECK_HEALTH = re.compile(
    r"^(?:check|how\s+is)\b.*\b(?:healthy|health|system|computer)\b", re.I)
_NOTE_CREATE = re.compile(
    r"^(?:create|make|write)\s+(?:(?:a|an|some|the)\s+)?"
    r"(?:temporary\s+)?note\b", re.I)
#: "create and clean up a temporary note": the note object comes at the end.
#: (The note-first form, "create a temporary note and clean it up", is
#: covered by the generic "X and Y" fallback below - each half plans on its
#: own terms.)
_COMPOUND_NOTE_END = re.compile(
    r"^(?P<create>create|make|write)\s+and\s+"
    r"(?P<action>clean\s+up|delete|remove|clear|discard|tidy\s+up)\s+"
    r"(?P<note>.+)$", re.I)
_CLEANUP = re.compile(
    r"^(?:clean\s+up|delete|remove|clear|discard|tidy\s+up)\b", re.I)
_NOTIFY = re.compile(
    r"^(?:tell\s+me\s+when|let\s+me\s+know\s+when|notify\s+me\s+when)\b"
    r".*\b(?:finished|done|complete)", re.I)

#: Single-word remainders after "open/start/launch/run" that are verbs, not
#: applications. Without this guard, "open" followed by a bare verb (from a
#: compound split) would become an app named "Create".
_BARE_VERBS = frozenset("""
create make write check find search look clean delete remove clear tell list
open start launch run summarize summarise browse research verify inspect
""".split())


def plan_objective(objective: str, manifest: list[dict]) -> dict:
    """
    LEGACY. Superseded by `friday/planner.py`.

    Kept because its tests document exactly how clause-splitting fails, and
    because the comparison is worth having: this is what produced 205 tasks
    from one spoken request, "Friday" as a task, and "keep going" as a memory
    write. `objective_start` does not call it any more and there is no
    fallback path back to it.

    Turn a natural-language objective into a task plan.

    Returns ``{"tasks": [...], "notes": [...], "unmapped": [...]}`` where
    each task is ``{capability, arguments, reason}``; ``compile_objective``
    assigns ids and chain dependencies in plan order (t1, t2, ...).
    """
    clauses = _split_clauses(objective or "")
    tasks: list[dict] = []
    notes: list[dict] = []
    unmapped: list[str] = []
    #: How many tasks each clause produced, and whether the speaker said this
    #: clause *follows* the last one. Dependencies come from these two rather
    #: than from the list index. See `_assign_dependencies`.
    groups: list[int] = []
    sequenced: list[bool] = []
    for clause, follows in clauses:
        before = len(tasks)
        sequenced.append(follows)
        planned = _plan_clause(clause, manifest)
        if planned is None:
            # The clause maps to nothing. It still becomes a task - the
            # unmapped marker, persisted by compile as an immediate
            # FAILED/CAPABILITY_MISSING - so the run records the failure
            # instead of silently dropping a step.
            unmapped.append(clause)
            tasks.append({
                "capability": UNMAPPED_CAPABILITY,
                "arguments": {"clause": clause},
                "reason": "no capability matches this step",
            })
        else:
            tasks.extend(planned["tasks"])
            notes.extend(planned["notes"])
        groups.append(len(tasks) - before)

    _assign_dependencies(tasks, groups, sequenced)
    return {"tasks": tasks, "notes": notes, "unmapped": unmapped}


#: The same separators, captured rather than discarded.
#:
#: `_SPLIT_CLAUSES` consumes "then", so by the time clauses existed the
#: sequencing was gone and "check the system, then open Paint" arrived shaped
#: exactly like "check the system, open Paint". Two tests caught that
#: immediately, which is the whole argument for keeping them.
_SPLIT_CAPTURING = re.compile(r"(,|\s+then\s+|\bthen\b)", re.I)
_THEN = re.compile(r"\bthen\b", re.I)


def _split_clauses(objective: str) -> list[tuple[str, bool]]:
    """Clauses, each with whether the speaker said it follows the last one."""
    clauses: list[tuple[str, bool]] = []
    follows = False
    for position, token in enumerate(_SPLIT_CAPTURING.split(objective)):
        if position % 2:                        # a separator
            follows = follows or bool(_THEN.search(token or ""))
            continue
        text = (token or "").strip()
        if not text:
            continue
        clauses.append((text, follows))
        follows = False
    return clauses


def _assign_dependencies(tasks: list[dict], groups: list[int],
                         sequenced: list[bool]) -> None:
    """
    Within a clause, chain. Across clauses, independent - unless "then".

    This used to be one line:

        task["dependencies"] = [f"t{index - 1}"] if index > 1 else []

    - every task waiting on whatever happened to be listed before it. So
    checking the system became a prerequisite for opening Paint, and a request
    with four unrelated parts executed as a single thread where any one failure
    stranded everything after it. The executor beneath has supported a real
    graph the whole time: it promotes a task only when every dependency
    succeeded and skips the dependents of a failure. The capability was bought
    and thrown away one line before it was used.

    The rule that replaces it comes from the sentence itself. "Check my system,
    open Paint, and find a story" is three independent errands and the speaker
    means them in no particular order. "Create and clean up a temporary note"
    is one errand in two steps, and the second genuinely cannot run before the
    first - so tasks from the same clause still chain.

    And "then" is the speaker saying sequence out loud. "Check the system,
    then open Paint" is not two errands in any order; it is one after the
    other, and the first version of this rule flattened it because the clause
    splitter eats the word it depends on. Two existing tests failed on exactly
    that sentence, which is a better outcome than shipping it.
    """
    position = 0
    previous_last = None            # 1-based id of the last task of the last clause
    for group, size in enumerate(groups):
        for offset in range(size):
            index = position + offset
            # t-ids are 1-based and assigned in list order by compile.
            if offset:
                tasks[index]["dependencies"] = [f"t{index}"]
            elif sequenced[group] and previous_last is not None:
                tasks[index]["dependencies"] = [f"t{previous_last}"]
            else:
                tasks[index]["dependencies"] = []
        if size:
            previous_last = position + size
        position += size


def _plan_clause(clause: str, manifest: list[dict]) -> dict | None:
    """Plan one clause; None means the planner cannot map it honestly."""
    if _NOTIFY.search(clause):
        return {"tasks": [], "notes": [{"kind": "notify", "clause": clause}]}

    compound = _COMPOUND_NOTE_END.match(clause)
    if compound:
        note = compound.group("note").strip()
        return {"tasks": [
            {"capability": "files_create",
             "arguments": {"content":
                           f"{compound.group('create')} {note}".strip()},
             "reason": clause},
            {"capability": UNMAPPED_CAPABILITY,
             "arguments": {"clause":
                           f"{compound.group('action')} {note}".strip()},
             "reason": "cleanup is not a capability yet"},
        ], "notes": []}

    if _CHECK_HEALTH.match(clause):
        return {"tasks": [
            {"capability": "system_get_info", "arguments": {},
             "reason": clause}], "notes": []}

    opened = _OPEN_APP.match(clause)
    if opened and " and " not in clause:
        name = opened.group(1).strip()
        if name and name.lower() not in _BARE_VERBS:
            title = " ".join(word.capitalize() for word in name.split())
            return {"tasks": [
                {"capability": "apps_open", "arguments": {"name": title},
                 "reason": clause}], "notes": []}

    searched = _SEARCH.match(clause)
    if searched:
        query = searched.group(1).strip()
        return {"tasks": [
            {"capability": "web_search", "arguments": {"query": query},
             "reason": clause}], "notes": []}

    if _NOTE_CREATE.match(clause):
        return {"tasks": [
            {"capability": "files_create", "arguments": {"content": clause},
             "reason": clause}], "notes": []}

    if _CLEANUP.match(clause):
        return {"tasks": [
            {"capability": UNMAPPED_CAPABILITY,
             "arguments": {"clause": clause},
             "reason": "cleanup is not a capability yet"}], "notes": []}

    if " and " in clause:
        halves = [half.strip() for half in re.split(r"\s+and\s+", clause)]
        if len(halves) == 2:
            left = _plan_clause(halves[0], manifest)
            right = _plan_clause(halves[1], manifest)
            if left is not None and right is not None:
                return {"tasks": left["tasks"] + right["tasks"],
                        "notes": left["notes"] + right["notes"]}

    best, best_score = None, 0
    for entry in manifest:
        if (entry.get("id") or "").startswith("objective_"):
            continue
        score = _phrase_score(clause, entry.get("intent_examples") or ())
        if score > best_score:
            best, best_score = entry["id"], score
    if best is not None and best_score >= 4:
        return {"tasks": [{"capability": best, "arguments": {},
                           "reason": clause}], "notes": []}
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def objective_start(run: c.Run, objective: str, *, tasks: str = "",
                    replace: bool = False,
                    engine: PolicyEngine = default_engine):
    """Start a new objective run: compile, persist, wake.

    ``objective`` is the natural-language request; ``tasks`` is optional
    explicit JSON (``[{capability, arguments, dependencies}]``) which, when
    given, is validated and used instead of the planner. An existing active
    run is refused unless ``replace`` is true (the old run is cancelled with
    a reason trail first). The run is compiled here - validation is refusal,
    exactly like compile - and the driver loop picks it up on its next tick.
    """
    tool_id = "objectives.start"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    text = (objective or "").strip()
    if not text and not tasks.strip():
        return run.record(c.failed(started, "objective is empty"))

    db = store()
    active = O.active_run(db)
    if active is not None and active["run_id"] != run.run_id:
        if not replace:
            return run.record(c.failed(
                started,
                f"an objective is already active: {active['run_id']} "
                f"({active['status']}). Cancel it first, or pass "
                f"replace=true."))
        O.cancel_run(db, run_id=active["run_id"],
                     reason="replaced by a new objective",
                     executor_id="objectives.start")

    manifest = capabilities.as_dicts()
    if tasks.strip():
        try:
            specs = json.loads(tasks)
        except ValueError as exc:
            return run.record(c.failed(
                started, f"tasks is not valid JSON: {exc}"))
        if not isinstance(specs, list) or not all(
                isinstance(spec, dict) for spec in specs):
            return run.record(c.failed(
                started,
                "tasks must be a JSON list of {capability, arguments, "
                "dependencies}"))
        known = {entry["id"] for entry in manifest}
        unknown = [spec.get("capability") for spec in specs
                   if spec.get("capability") not in known]
        if unknown:
            return run.record(c.failed(
                started, f"unknown capability {unknown[0]!r}"))
    else:
        from friday import planner as SP
        from friday import planner_model as SPM
        # The semantic planner, not the clause splitter above: one
        # request is read as goals with an operation and a target each,
        # and validated before anything is persisted.
        semantic = SPM.plan_objective(text)
        complaints = SP.validate(semantic)
        if complaints:
            return run.record(c.failed(
                started, "the plan did not validate: " + "; ".join(complaints)))
        specs = SP.task_specs(semantic)
        if not specs:
            return run.record(c.failed(
                started,
                "nothing in that was a request to do something"
                + (f" (unplaceable: {semantic.unresolved[:3]})"
                   if semantic.unresolved else "")))

    try:
        created = O.compile_objective(
            db, request=text, tasks=specs, manifest=manifest,
            objective_summary=text)
    except O.CompileError as exc:
        return run.record(c.failed(started, str(exc)))

    payload = {
        "run_id": created["run_id"],
        "status": created["status"],
        "task_count": len(specs),
        "tasks": [{
            "capability": spec["capability"],
            "dependencies": list(spec.get("dependencies", ())),
        } for spec in specs],
    }
    return run.record(c.succeeded(
        started, output=_scoped(payload),
        verification=c.Verification(
            method="run_persisted",
            evidence=f"run {created['run_id']} persisted with status "
                     f"{created['status']} and {len(specs)} tasks")))


def objective_status(run: c.Run, run_id: str = "",
                     *, engine: PolicyEngine = default_engine):
    """The named run, or the most recent active one: status and task rows."""
    tool_id = "objectives.status"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    found = O.active_run(store(), run_id=run_id.strip())
    if found is None:
        return run.record(c.failed(
            started, f"no objective run {run_id!r}" if run_id.strip()
            else "no objective has been started"))
    tasks = [{
        "task_id": row["task_id"],
        "capability": row["capability"],
        "status": row["status"],
        "attempts": row["attempts"],
        "evidence": row.get("evidence") or "",
        "error": row.get("failure_kind") or "",
    } for row in store().objective_tasks(found["run_id"])]
    payload = {
        "run_id": found["run_id"],
        "status": found["status"],
        "objective_summary": found["objective_summary"],
        "next_wake": found["next_wake"],
        "summary": found.get("summary") or {},
        "tasks": tasks,
    }
    return run.record(c.succeeded(
        started, output=_scoped(payload),
        verification=c.Verification(
            method="store_read",
            evidence=f"read run {found['run_id']} with {len(tasks)} tasks")))


def objective_list(run: c.Run, *, limit: int = 10,
                   engine: PolicyEngine = default_engine):
    """Recent runs, most recent first: id, status, what each is doing."""
    tool_id = "objectives.list"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    rows = store().objective_runs(limit=max(1, min(int(limit), 100)))
    payload = [{
        "run_id": row["run_id"],
        "status": row["status"],
        "objective_summary": row["objective_summary"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "summary": row.get("summary") or {},
    } for row in rows]
    return run.record(c.succeeded(
        started, output=payload,
        verification=c.Verification(
            method="store_read", evidence=f"read {len(rows)} runs")))


def objective_pause(run: c.Run, run_id: str = "", *, reason: str = "",
                    engine: PolicyEngine = default_engine):
    """Pause the named run (or the most recent active one).

    The run enters a legitimate wait: no wake is scheduled, so neither the
    driver loop nor the watchdog touches it until an explicit resume. The
    in-flight task, if any, finishes; nothing is interrupted.
    """
    tool_id = "objectives.pause"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    db = store()
    found = O.active_run(db, run_id=run_id.strip())
    if found is None:
        return run.record(c.failed(
            started, f"no objective run {run_id!r}" if run_id.strip()
            else "no objective has been started"))
    if found["status"] == O.RUN_PAUSED:
        return run.record(c.failed(started, "already paused"))
    if not O.pause_run(db, run_id=found["run_id"],
                       reason=reason.strip() or "user request",
                       executor_id="objectives.pause"):
        return run.record(c.failed(
            started, f"cannot pause {found['run_id']} "
            f"({found['status']})"))
    return run.record(c.succeeded(
        started, output=_scoped({"run_id": found["run_id"],
                                 "status": O.RUN_PAUSED}),
        verification=c.Verification(
            method="store_status_flip",
            evidence=f"run {found['run_id']} paused")))


def objective_resume(run: c.Run, run_id: str = "", *, reason: str = "",
                     engine: PolicyEngine = default_engine):
    """Resume the named paused run (or the most recent paused one)."""
    tool_id = "objectives.resume"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    db = store()
    if run_id.strip():
        found = store().objective_run(run_id.strip())
    else:
        found = None
        for row in store().objective_runs(limit=100):
            if row["status"] == O.RUN_PAUSED:
                found = row
                break
    if found is None:
        return run.record(c.failed(
            started, f"no paused objective run {run_id!r}"
            if run_id.strip() else "no paused objective"))
    if found["status"] != O.RUN_PAUSED:
        return run.record(c.failed(
            started, f"{found['run_id']} is {found['status']}, not paused"))
    if not O.resume_run(db, run_id=found["run_id"],
                        reason=reason.strip() or "user request",
                        executor_id="objectives.resume"):
        return run.record(c.failed(
            started, f"cannot resume {found['run_id']}"))
    return run.record(c.succeeded(
        started, output=_scoped({"run_id": found["run_id"],
                                 "status": O.RUN_RUNNING}),
        verification=c.Verification(
            method="store_status_flip",
            evidence=f"run {found['run_id']} resumed")))


def objective_cancel(run: c.Run, run_id: str = "", *, reason: str = "",
                     engine: PolicyEngine = default_engine):
    """Cancel the named run (or the most recent active one).

    Every unfinished task is interrupted with an evidence trail and the run
    reaches RUN_CANCELLED with a summary. Stopping wins over driving: no
    lease is taken.
    """
    tool_id = "objectives.cancel"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    db = store()
    found = O.active_run(db, run_id=run_id.strip())
    if found is None:
        return run.record(c.failed(
            started, f"no objective run {run_id!r}" if run_id.strip()
            else "no objective has been started"))
    if not O.cancel_run(db, run_id=found["run_id"],
                        reason=reason.strip() or "user request",
                        executor_id="objectives.cancel"):
        return run.record(c.failed(
            started, f"cannot cancel {found['run_id']} "
            f"({found['status']})"))
    return run.record(c.succeeded(
        started, output=_scoped({"run_id": found["run_id"],
                                 "status": O.RUN_CANCELLED}),
        verification=c.Verification(
            method="store_status_flip",
            evidence=f"run {found['run_id']} cancelled")))


def objective_history(run: c.Run, run_id: str = "", *, limit: int = 25,
                      engine: PolicyEngine = default_engine):
    """The event ledger of the named run (or the most recent one)."""
    tool_id = "objectives.history"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    db = store()
    found = O.active_run(db, run_id=run_id.strip())
    if found is None:
        return run.record(c.failed(
            started, f"no objective run {run_id!r}" if run_id.strip()
            else "no objective has been started"))
    events = db.objective_events(found["run_id"],
                                 limit=max(1, min(int(limit), 500)))
    payload = [{
        "event": row["event"],
        "task_id": row.get("task_id"),
        "detail": row.get("detail") or {},
        "at": row["at"],
    } for row in events]
    return run.record(c.succeeded(
        started, output=payload,
        verification=c.Verification(
            method="store_read", evidence=f"read {len(events)} events")))


# ---------------------------------------------------------------------------
# Port: what the CLI and the engine may call
# ---------------------------------------------------------------------------

def capability_port() -> dict[str, object]:
    """Capability name -> callable, for the CLI and engine dispatch."""
    return {
        "objective_start": objective_start,
        "objective_status": objective_status,
        "objective_list": objective_list,
        "objective_pause": objective_pause,
        "objective_resume": objective_resume,
        "objective_cancel": objective_cancel,
        "objective_history": objective_history,
    }
