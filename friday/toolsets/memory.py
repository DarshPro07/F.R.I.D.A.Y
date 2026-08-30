
"""
Memory toolset (Phase 1D): durable facts with provenance.

The store and schema already existed from the Phase 1 foundation; this wires
them to capabilities and enforces the rule that matters:

    an INFERENCE must never come back sounding like a FACT.

Every row carries kind, source and confidence, and recall returns a
`spoken_form` derived from them — "You told me X" for a FACT, "I worked out
that X" for an INFERENCE, hedged further when confidence is low. Without that,
the distinction exists in the database and evaporates the moment the agent
speaks, which is where it actually matters.

Contrast Mark-L, whose memory is a 2200-character JSON blob trimmed by
deleting the oldest entries, with `pop_last_session()` destroying a summary on
read. Here nothing is deleted — superseded rows are marked — and reading a
recap twice gives the same answer twice.
"""

from __future__ import annotations

import os

import re

from friday import contracts as c

from friday.policy import PolicyEngine, default_engine

from friday.store import (
    DEFAULT_DB, FACT, INFERENCE, MEMORY_KINDS, PATTERN, PREFERENCE, Store,
)

from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "agent_runtime"  # the database, not the user's filesystem

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

_OPENERS = {
    FACT: "You told me",
    PREFERENCE: "You prefer",
    PATTERN: "I've noticed",
    INFERENCE: "I worked out",
}


def spoken_form(row: dict) -> str:
    """
    How a memory may be said out loud, derived from kind and confidence.

    This is the whole point of storing kind: a FACT the user stated and an
    INFERENCE the agent guessed are different claims about the world, and
    speaking them identically erases the difference exactly where it counts.
    """
    kind = row.get("kind", FACT)
    confidence = float(row.get("confidence", 1.0))
    subject, value = row.get("subject", "?"), row.get("value", "?")
    opener = _OPENERS.get(kind, "I have it recorded that")

    if kind == INFERENCE or confidence < 0.7:
        hedge = "I'm not certain, but " if confidence < 0.5 else ""
        return f"{hedge}{opener} {subject} is {value} (inferred, {confidence:.0%} confident)"
    return f"{opener} {subject} is {value}"


def _row_view(row: dict) -> dict:
    return {
        "subject": row["subject"], "value": row["value"], "kind": row["kind"],
        "source": row["source"], "confidence": row["confidence"],
        "scope": row["scope"], "recorded_at": row["created_at"],
        "spoken_form": spoken_form(row),
    }


def memory_remember(
    run: c.Run, subject: str, value: str, *, kind: str = FACT,
    source: str = "user stated it", confidence: float = 1.0,
    scope: str = "user", engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Store a durable memory, then read it back to prove it persisted."""
    tool_id = "memory.remember"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (subject or "").strip() or not (value or "").strip():
        return run.record(c.failed(started, "subject and value are both required"))
    if kind not in MEMORY_KINDS:
        return run.record(c.failed(
            started, f"unknown memory kind {kind!r}; known: {list(MEMORY_KINDS)}"
        ))

    try:
        row_id = store().remember(
            subject.strip(), value.strip(), kind=kind, source=source,
            confidence=confidence, scope=scope, run_id=run.run_id,
        )
    except ValueError as exc:
        return run.record(c.failed(started, str(exc)))

    # A write that did not raise is not evidence the row is durable.
    persisted = store().recall(subject.strip())
    if not persisted or persisted[0]["value"] != value.strip():
        return run.record(c.partial(
            started, "stored but could not read the memory back",
            output=_scoped({"subject": subject}),
        ))

    return run.record(c.succeeded(
        started,
        output=_scoped({"id": row_id, **_row_view(persisted[0])}),
        side_effects=(f"memory row {row_id} written",),
        verification=c.Verification(
            method="memory_readback",
            evidence=f"row {row_id}: {subject!r} = {value!r} "
                     f"[{kind}, source={source!r}, confidence={confidence}] "
                     f"read back from {store().path}",
        ),
    ))


def memory_recall(
    run: c.Run, subject: str, *, limit: int = 10,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    tool_id = "memory.recall"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    subject = (subject or "").strip()
    rows = store().recall(subject)
    match_type = "exact"

    if not rows:
        # An exact-match-only recall is the wrong contract for a caller that
        # does not know our key names. The agent asked for "Arc Reactor
        # language" while the row was keyed "Project Arc Reactor.language",
        # found nothing, and correctly reported nothing was recorded - honest
        # but useless. Fall back to a keyword match on the most distinctive
        # words, and say which path answered.
        terms = [w for w in re.split(r"[\s._-]+", subject) if len(w) > 2]
        seen: dict[int, dict] = {}
        for term in terms:
            for row in store().search_memories(term, limit=limit):
                seen[row["id"]] = row
        # Rank by how many of the query's terms the row mentions.
        def score(row: dict) -> int:
            haystack = f"{row['subject']} {row['value']}".lower()
            return sum(1 for t in terms if t.lower() in haystack)

        rows = sorted(seen.values(), key=score, reverse=True)
        rows = [r for r in rows if score(r) > 0][:limit]
        match_type = "fuzzy"

    if not rows:
        return run.record(c.failed(started, f"nothing recorded for {subject!r}"))

    views = [_row_view(r) for r in rows]
    kinds = sorted({v["kind"] for v in views})
    return run.record(c.succeeded(
        started,
        output=_scoped({"subject": subject, "match_type": match_type,
                        "count": len(views), "memories": views}),
        verification=c.Verification(
            method=f"memory_query:{match_type}",
            evidence=f"{len(views)} row(s) for {subject!r} via {match_type} match "
                     f"({', '.join(kinds)}); first source: {views[0]['source']!r}",
        ),
    ))


def memory_search(
    run: c.Run, query: str, *, limit: int = 20,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    tool_id = "memory.search"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (query or "").strip():
        return run.record(c.failed(started, "empty query"))

    rows = store().search_memories(query.strip(), limit=limit)
    if not rows:
        return run.record(c.failed(started, f"no memories match {query!r}"))

    views = [_row_view(r) for r in rows]
    return run.record(c.succeeded(
        started,
        output=_scoped({"query": query, "count": len(views), "memories": views}),
        verification=c.Verification(
            method="memory_search",
            evidence=f"{len(views)} row(s) matching {query!r}; "
                     f"subjects: {[v['subject'] for v in views[:5]]}",
        ),
    ))


def memory_forget(
    run: c.Run, subject: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Supersede a memory. Rows are marked, never deleted - history stays."""
    tool_id = "memory.forget"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    subject = (subject or "").strip()
    before = len(store().recall(subject))
    if before == 0:
        return run.record(c.failed(started, f"nothing recorded for {subject!r}"))

    marked = store().forget(subject)
    after = len(store().recall(subject))
    if after != 0:
        return run.record(c.partial(
            started, f"marked {marked} row(s) but {after} remain active",
            output=_scoped({"subject": subject}),
        ))

    return run.record(c.succeeded(
        started,
        output=_scoped({"subject": subject, "superseded": marked,
                        "retained_in_history": True}),
        side_effects=(f"{marked} memory row(s) superseded",),
        verification=c.Verification(
            method="memory_superseded",
            evidence=f"{marked} row(s) for {subject!r} marked superseded; "
                     f"0 active remain, history retained",
        ),
    ))


def project_record_decision(
    run: c.Run, project: str, decision: str, *, rationale: str | None = None,
    source: str = "user stated it", engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    tool_id = "memory.record_decision"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (project or "").strip() or not (decision or "").strip():
        return run.record(c.failed(started, "project and decision are required"))

    row_id = store().record_decision(
        project.strip(), decision.strip(), source=source,
        rationale=rationale, run_id=run.run_id,
    )
    persisted = store().decisions(project.strip())
    if not persisted:
        return run.record(c.partial(started, "recorded but could not read back"))

    return run.record(c.succeeded(
        started,
        output=_scoped({"id": row_id, "project": project, "decision": decision,
                        "rationale": rationale, "total_decisions": len(persisted)}),
        side_effects=(f"decision {row_id} recorded for {project}",),
        verification=c.Verification(
            method="decision_readback",
            evidence=f"decision {row_id} for {project!r}: {decision!r}; "
                     f"{len(persisted)} decision(s) now on record",
        ),
    ))


def project_context(run: c.Run, project: str, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """
    Everything durable about a project: memories, decisions, open questions.

    The questions were the missing third. Friday could say what had been
    settled about the lighthouse game and not what it was still waiting on -
    and the open ones are the more useful half, because they are what is
    blocking the work rather than what is already behind it.
    """
    tool_id = "memory.project_context"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    project = (project or "").strip()
    memories = [_row_view(r) for r in store().search_memories(project, limit=50)]
    decisions = store().decisions(project)
    questions = [{"question": row["question"], "why": row.get("why") or "",
                  "asked_at": row["asked_at"]}
                 for row in store().open_questions(project)]

    if not memories and not decisions and not questions:
        return run.record(c.failed(started, f"nothing recorded about {project!r}"))

    evidence = (f"{len(memories)} memor(ies) and {len(decisions)} decision(s) recorded for "
                f"{project!r}")
    if questions:
        evidence += f", {len(questions)} question(s) still open"

    return run.record(c.succeeded(
        started,
        output=_scoped({"project": project, "memories": memories,
                        "decisions": decisions,
                        "open_questions": questions,
                        "counts": {"memories": len(memories),
                                   "decisions": len(decisions),
                                   "open_questions": len(questions)}}),
        verification=c.Verification(method="project_query", evidence=evidence),
    ))


def projects_list(run: c.Run, *, limit: int = 20, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """
    Every project Friday is holding, newest activity first.

    "What am I working on?" had no capability behind it at all - the storage
    was there, `store.projects()` was there, and nothing in the registry
    reached either. So the answer came from whatever the model remembered of
    the conversation, which is the failure durable memory exists to prevent.

    Activity is what orders them, not creation date. A project touched an hour
    ago is the one being asked about; one started in March and untouched since
    is history.
    """
    tool_id = "memory.projects_list"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    found = []
    for row in store().projects():
        name = row["name"]
        decisions = store().decisions(name)
        questions = store().open_questions(name)
        touched = max([row["created_at"]]
                      + [d["created_at"] for d in decisions]
                      + [q["asked_at"] for q in questions])
        found.append({
            "project": name,
            "summary": row.get("summary") or "",
            "decisions": len(decisions),
            "open_questions": len(questions),
            "last_touched": touched,
        })
    found.sort(key=lambda item: item["last_touched"], reverse=True)
    found = found[:limit]

    if not found:
        return run.record(c.failed(started, "no projects on record yet"))
    return run.record(c.succeeded(
        started,
        output=_scoped({"projects": found, "count": len(found)}),
        verification=c.Verification(
            method="project_query",
            evidence=f"{len(found)} project(s), most recently "
                     f"{found[0]['project']}"),
    ))


def project_resume(run: c.Run, project: str, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """
    Everything needed to pick a project up again, in one call.

    "Continue Halo" is the journey this exists for, and it has to work after a
    restart, from a new conversation, with no history at all - which is the
    whole reason any of this is durable. Reconstructed from the store rather
    than from what was said: decisions that were settled, questions still
    open, work still in flight, and what to do next.

    `next_step` is derived rather than stored, and the order is the argument:
    an unanswered question blocks everything after it, so it comes before
    unfinished work, which comes before starting something new. Guessing the
    next step when a question is open is how Friday would build the wrong
    thing confidently.
    """
    tool_id = "memory.project_resume"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    project = (project or "").strip()
    known = {row["name"] for row in store().projects()}
    if project not in known:
        near = [name for name in known
                if project.lower() in name.lower() or name.lower() in project.lower()]
        if len(near) == 1:
            project = near[0]
        else:
            return run.record(c.failed(
                started,
                f"no project called {project!r}"
                + (f"; did you mean one of {sorted(near)}?" if near else "")))

    decisions = store().decisions(project)
    questions = store().open_questions(project)
    runs = [row for row in store().executor_runs(project=project, limit=10)]
    working = [row for row in runs
               if (row.get("status") or "").upper()
               not in ("COMPLETED", "FAILED", "CANCELLED")]

    # A blocking question is one the work cannot proceed past; an assumption
    # is a question the run answered for itself and is proceeding on, which
    # is worth saying out loud because it may be wrong. Both are open
    # questions, so the two lists overlap with `questions` and not with each
    # other only by convention - a question that is both is blocking, and
    # the assumption note is dropped from the next step below.
    blocking = [q for q in questions if q.get("blocking")]
    assumed = [q for q in questions if q.get("assumption")]

    if blocking:
        step = (f"answer {len(blocking)} blocking question(s), starting with: "
                f"{blocking[0]['question']}")
    elif working:
        step = f"{len(working)} development run(s) still going"
    elif decisions:
        step = "nothing is blocked; the next move is to build"
    else:
        step = "nothing decided yet; start by working out what this is"
    if assumed and not blocking:
        step += f" (proceeding on {len(assumed)} assumption(s) - say if any is wrong)"

    return run.record(c.succeeded(
        started,
        output=_scoped({
            "project": project,
            "decisions": [{"decision": d["decision"],
                           "rationale": d.get("rationale") or "",
                           "source": d["source"]} for d in decisions],
            "blocking_questions": [{"question": q["question"],
                                    "why": q.get("why") or ""} for q in blocking],
            "assumptions": [{"question": q["question"],
                             "assumption": q["assumption"],
                             "because": q.get("assumption_reason") or ""}
                            for q in assumed],
            "open_questions": [{"question": q["question"],
                                "why": q.get("why") or ""} for q in questions],
            "runs": [{"run_id": r.get("run_id"), "status": r.get("status"),
                      "task": (r.get("task") or "")[:120]} for r in runs],
            "next_step": step,
            "counts": {"decisions": len(decisions),
                       "open_questions": len(questions),
                       "blocking": len(blocking),
                       "assumptions": len(assumed),
                       "runs_in_flight": len(working)},
            "ready_to_build": not blocking,
        }),
        verification=c.Verification(
            method="project_query",
            evidence=f"{project}: {len(decisions)} decision(s), "
                     f"{len(blocking)} blocking question(s), "
                     f"{len(assumed)} assumption(s), "
                     f"{len(working)} run(s) in flight"),
    ))


def session_recap(
    run: c.Run, *, limit: int = 3, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    "Where were we?" — recent conversations and runs.

    Non-destructive: asking twice gives the same answer. Mark-L's equivalent
    popped the entry, so the second person to ask got nothing.
    """
    tool_id = "memory.session_recap"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    conversations = store().recent_conversations(limit=limit)
    runs = store().recent_runs(limit=limit * 3)
    if not conversations and not runs:
        return run.record(c.failed(started, "no previous sessions on record"))

    return run.record(c.succeeded(
        started,
        output=_scoped({
            "conversations": conversations,
            "recent_runs": [{"run_id": r["run_id"], "request": r["request"],
                             "state": r["state"], "updated_at": r["updated_at"]}
                            for r in runs],
        }),
        verification=c.Verification(
            method="session_query",
            evidence=f"{len(conversations)} conversation(s) and {len(runs)} "
                     f"run(s) on record in {store().path}",
        ),
    ))


def record_utterance(
    run: c.Run, raw: str, *, normalized: str | None = None,
    reason: str | None = None, evidence: str | None = None,
    confidence: float | None = None, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Store what was heard and, separately, what it was corrected to (§14).

    The raw utterance is never overwritten: "start plot code" stays on record
    alongside "start Claude Code", with the reason for the correction.
    """
    tool_id = "memory.record_utterance"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (raw or "").strip():
        return run.record(c.failed(started, "raw utterance is required"))

    row_id = store().record_utterance(
        raw, normalized=normalized, reason=reason, evidence=evidence,
        confidence=confidence, run_id=run.run_id,
    )
    persisted = store().get_utterance(row_id)
    if not persisted or persisted["raw"] != raw:
        return run.record(c.partial(started, "stored but could not read back"))

    return run.record(c.succeeded(
        started,
        output=_scoped({"id": row_id, "raw": persisted["raw"],
                        "normalized": persisted["normalized"],
                        "correction_reason": persisted["correction_reason"]}),
        verification=c.Verification(
            method="utterance_readback",
            evidence=f"utterance {row_id}: raw={raw!r} preserved"
                     + (f", normalized={normalized!r}" if normalized else ""),
        ),
    ))


# ---------------------------------------------------------------------------
# Speaking a memory honestly
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
