"""
Changing a run that is already under way.

`classify_input` has returned MODIFICATION since it was written, and the
handler did this:

    # MODIFICATION and SIDE_CONVERSATION both leave the run alone.

Which was the right call at the time and is a bad answer to say out loud: the
boss says "skip the World Monitor part", Friday says nothing about it, and the
World Monitor opens anyway two minutes later. The failure is not that the edit
was refused - it is that the refusal was silent, so there was no way to tell
"I cannot do that yet" from "I did it".

Three edits, all of them to work that has not started:

    skip      a queued task is marked SKIPPED, and its dependents cascade
    amend     a queued task's arguments change
    append    a new task runs after everything currently terminal

And one rule that shapes all three:

    **Modification changes future work. It does not rewrite history.**

If Paint has already opened, "actually don't open Paint" cannot un-open it,
and pretending otherwise would put a false statement in the trace - the one
thing this codebase is built not to do. The completed task stays SUCCEEDED
with its evidence intact, and the answer says so. Whether to then close Paint
is a *new* action under its own policy, not a retraction of an old one.

Races are handled by refusing them. A task the executor has already claimed is
RUNNING, and rewriting the arguments of a capability that is mid-call would
change what it does halfway through - so that returns TOO_LATE and names what
is already happening. The check and the write are one statement, so a task
that becomes RUNNING between reading it and editing it loses the race rather
than being edited underneath the executor.

Every edit is written to `objective_events` before anything else moves. That
table is the append-only continuation trace and already survives a restart, so
the graph version is derived from it rather than cached anywhere: a resumed
process reads the same number the editing process wrote.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from friday import objectives as O
from friday.store import Store

#: The event name every graph edit is recorded under.
GRAPH_EDIT = "graph_edited"

#: What a task must be in for its future to be editable. RUNNING is missing on
#: purpose, and so is everything terminal.
EDITABLE = (O.TaskStatus.QUEUED, O.TaskStatus.READY)

SKIPPED_BY_USER = "the boss asked for it to be skipped"


@dataclass(frozen=True)
class Edit:
    """What happened, in enough detail to say it back and to audit it."""

    outcome: str                     # applied | too_late | not_found | refused
    reason: str
    task_id: str = ""
    graph_version: int = 0
    old_state: str = ""
    new_state: str = ""
    affected: tuple[str, ...] = field(default_factory=tuple)

    @property
    def applied(self) -> bool:
        return self.outcome == "applied"


APPLIED, TOO_LATE, NOT_FOUND, REFUSED = (
    "applied", "too_late", "not_found", "refused")


def graph_version(store: Store, run_id: str) -> int:
    """
    Which version of this graph is authoritative, read from the database.

    Version 1 is the graph as compiled; each recorded edit is the next. Kept
    in the event trace rather than in a column so it cannot drift from the
    edits that produced it, and so a process that resumes mid-run reads the
    same number the process that made the edit wrote.
    """
    events = store.objective_events(run_id)
    return 1 + sum(1 for event in events if event["event"] == GRAPH_EDIT)


def _record(store: Store, run_id: str, *, task_id: str, action: str,
            old_state: str, new_state: str, reason: str,
            said: str, detail: dict | None = None) -> int:
    """Write the edit down first. Nothing moves before this returns."""
    version = graph_version(store, run_id) + 1
    store.append_objective_event(
        run_id, GRAPH_EDIT, task_id=task_id,
        detail={
            "graph_version": version,
            "action": action,
            "old_state": old_state,
            "new_state": new_state,
            "reason": reason,
            # What the boss actually said. The trace has to be answerable
            # months later, and "the arguments changed" without the sentence
            # that changed them is not an account of anything.
            "source_turn": said,
            **(detail or {}),
        })
    return version


# ---------------------------------------------------------------------------
# Finding the task somebody means
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "the", "a", "an", "part", "step", "bit", "that", "this", "it", "please",
    "skip", "dont", "don't", "do", "not", "instead", "use", "change", "also",
    "and", "then", "when", "youre", "you're", "done", "for", "to", "of", "my",
})


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _STOPWORDS and len(w) > 2}


def find_task(store: Store, run_id: str, description: str) -> dict | None:
    """
    Which task "the World Monitor part" means.

    Matched on the capability id and the arguments, because that is what a
    task actually is - there are no task titles to match against, and
    inventing one at compile time would be a second name for the same thing
    that could disagree with it.
    """
    wanted = _words(description)
    if not wanted:
        return None

    best, best_score = None, 0
    for task in store.objective_tasks(run_id):
        haystack = _words(
            task["capability"].replace("_", " ") + " "
            + " ".join(str(v) for v in (task["arguments"] or {}).values()))
        score = len(wanted & haystack)
        # A later task wins a tie: "skip the summary" during a run that
        # summarises twice means the one that has not happened.
        if score and score >= best_score:
            best, best_score = task, score
    return best


# ---------------------------------------------------------------------------
# The three edits
# ---------------------------------------------------------------------------


def skip_task(store: Store, run_id: str, description: str, *,
              said: str = "") -> Edit:
    """
    "Skip the World Monitor part."

    The task is marked SKIPPED and the executor's own cascade takes care of
    anything that depended on it - the same path a failed dependency takes, so
    there is one implementation of what "this did not happen" means downstream.
    """
    task = find_task(store, run_id, description)
    if task is None:
        return Edit(NOT_FOUND,
                    f"nothing in this run matches {description!r}")

    status = task["status"]
    if status == O.TaskStatus.RUNNING:
        return Edit(TOO_LATE,
                    f"{task['capability']} is already running - it cannot be "
                    f"skipped now", task_id=task["task_id"], old_state=status)
    if status in O.TASK_TERMINAL:
        return Edit(TOO_LATE,
                    f"{task['capability']} has already finished ({status}); "
                    f"skipping it now would not undo it",
                    task_id=task["task_id"], old_state=status)

    version = _record(store, run_id, task_id=task["task_id"], action="skip",
                      old_state=status, new_state=O.TaskStatus.SKIPPED,
                      reason=SKIPPED_BY_USER, said=said or description)

    # One statement, so a task that becomes RUNNING between the check above
    # and this line loses rather than being skipped out from under the
    # executor.
    changed = store.update_objective_task_if(
        task["task_id"], expect=EDITABLE,
        status=O.TaskStatus.SKIPPED, blocked_by=SKIPPED_BY_USER)
    if not changed:
        return Edit(TOO_LATE,
                    f"{task['capability']} started while I was skipping it",
                    task_id=task["task_id"], old_state=status)

    dependents = tuple(
        other["task_id"] for other in store.objective_tasks(run_id)
        if task["task_id"] in (other["dependencies"] or [])
        and other["status"] not in O.TASK_TERMINAL)

    return Edit(APPLIED, f"skipping {task['capability']}",
                task_id=task["task_id"], graph_version=version,
                old_state=status, new_state=O.TaskStatus.SKIPPED,
                affected=dependents)


def amend_task(store: Store, run_id: str, description: str,
               changes: dict, *, said: str = "") -> Edit:
    """
    "Use BBC instead for the research."

    Only a task that has not started. Evidence already recorded belongs to
    what actually ran, and editing the arguments of a finished task would
    leave a result that no longer matches the request that produced it.
    """
    task = find_task(store, run_id, description)
    if task is None:
        return Edit(NOT_FOUND,
                    f"nothing in this run matches {description!r}")
    if not changes:
        return Edit(REFUSED, "no change was given", task_id=task["task_id"])

    status = task["status"]
    if status == O.TaskStatus.RUNNING:
        return Edit(TOO_LATE,
                    f"{task['capability']} is running now - changing its "
                    f"arguments mid-call would change what it is doing "
                    f"halfway through", task_id=task["task_id"],
                    old_state=status)
    if status in O.TASK_TERMINAL:
        return Edit(TOO_LATE,
                    f"{task['capability']} has already finished ({status}) "
                    f"and its result belongs to the arguments it actually ran "
                    f"with", task_id=task["task_id"], old_state=status)

    updated = {**(task["arguments"] or {}), **changes}
    version = _record(store, run_id, task_id=task["task_id"], action="amend",
                      old_state=status, new_state=status,
                      reason=f"changed {sorted(changes)}",
                      said=said or description,
                      detail={"old_arguments": task["arguments"],
                              "new_arguments": updated})

    changed = store.update_objective_task_if(
        task["task_id"], expect=EDITABLE,
        arguments=json.dumps(updated, default=str))
    if not changed:
        return Edit(TOO_LATE,
                    f"{task['capability']} started while I was changing it",
                    task_id=task["task_id"], old_state=status)

    return Edit(APPLIED,
                f"{task['capability']} will use {sorted(changes)} instead",
                task_id=task["task_id"], graph_version=version,
                old_state=status, new_state=status)


def append_task(store: Store, run_id: str, capability: str,
                arguments: dict | None = None, *, said: str = "") -> Edit:
    """
    "Also save a short summary when you're done."

    The new task depends on every task that is not yet terminal, so "when
    you're done" means what it says. Depending on nothing would race the work
    it is supposed to follow; depending on the whole graph including finished
    tasks would be noise.
    """
    run = store.objective_run(run_id)
    if run is None or run["status"] in O.RUN_TERMINAL:
        return Edit(REFUSED,
                    "that run is over - this would be a new objective")

    tasks = store.objective_tasks(run_id)
    outstanding = tuple(t["task_id"] for t in tasks
                        if t["status"] not in O.TASK_TERMINAL)

    task_id = f"t{len(tasks) + 1}-added"
    version = _record(store, run_id, task_id=task_id, action="append",
                      old_state="", new_state=O.TaskStatus.QUEUED,
                      reason=f"appended {capability}", said=said or capability,
                      detail={"dependencies": list(outstanding)})

    store.save_objective_task(
        task_id=task_id, run_id=run_id, capability=capability,
        arguments=json.dumps(arguments or {}, default=str),
        dependencies=json.dumps(list(outstanding)),
        status=O.TaskStatus.QUEUED)

    return Edit(APPLIED,
                f"{capability} added, after {len(outstanding)} outstanding "
                f"task(s)", task_id=task_id, graph_version=version,
                new_state=O.TaskStatus.QUEUED, affected=outstanding)
