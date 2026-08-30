"""
Watching the selective router work, without letting it work.

Every number the router has comes from a corpus this project wrote. Templates
reduce the circularity and do not remove it, and the store holds **four** real
utterances - all long compound dictations from one test. So the distribution
that decides whether local execution is safe does not exist yet, and the only
place it exists is the boss talking to Friday.

Shadow mode collects it. The router sees a copy of what Friday sees, predicts,
and is recorded. Friday does the work exactly as it does today.

    OFF      the router is not consulted at all
    SHADOW   it predicts, and cannot act
    DIRECT   it acts. Not reachable yet, and deliberately hard to reach

## Production is not ground truth

The tempting design records `AGREED` and `DISAGREED` against whatever Friday
did, and it is wrong. Gemini mis-routes too. A router trained to match it
would learn to reproduce its mistakes, and a `DISAGREED` count would look like
a defect report while being a disagreement between two fallible things.

So comparison and correctness are two different columns.

    comparison_status   what the two paths did.        Always available.
    label_source        where a claim of correctness came from. Often absent.

`PRODUCTION_ROUTE_ONLY` is a real label source and the weakest one. A
verified `ActionResult` is the strongest thing available without a person, and
an explicit correction from the boss - "no, I meant the music" - is the most
valuable, because it is the only place real language says *what it should have
been*.

## Telemetry is a privacy surface

A shadow log that quietly becomes a recording of everything the boss says is a
worse problem than the one it was built to solve. What is stored is a one-way
fingerprint and structured routing metadata: the shape of the request, what
was predicted, whether a referent existed. Never the sentence, never its
contents, never anything that could carry a secret.

## It cannot act

Not by convention - by construction. `Prediction` is a frozen record with no
runtime, no principal and no path to `CapabilityRuntime`. `attempted_to_act()`
is the counter that proves it, and it is asserted at zero.
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import re
import threading
from dataclasses import dataclass, field

from friday.contracts import now_iso


logger = logging.getLogger("friday-agent.shadow")

# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

OFF = "off"
SHADOW = "shadow"
DIRECT = "direct"
MODES = (OFF, SHADOW, DIRECT)

#: One canonical setting rather than a boolean that has to mean three things.
ENV_MODE = "FRIDAY_REFLEX_MODE"

#: Versions stamped on every row. A prediction made before a routing change
#: and one made after are not comparable, and without this nobody can tell
#: which is which six weeks later.
ROUTER_VERSION = "selective-1"
TAXONOMY_VERSION = "reasons-1"
THRESHOLD_VERSION = "margin-6"


def mode() -> str:
    """Which of the three states this process is in."""
    raw = (os.getenv(ENV_MODE) or "").strip().lower()
    if raw in MODES:
        return raw
    # Compatibility with the older pair of flags, and the older pair only
    # ever expressed two of the three states.
    if (os.getenv("FRIDAY_SHADOW") or "").strip().lower() in ("1", "true", "yes", "on"):
        return SHADOW
    if (os.getenv("FRIDAY_REFLEX") or "").strip().lower() in ("1", "true", "yes", "on"):
        return DIRECT
    return OFF


def enabled() -> bool:
    """Whether predictions are being collected."""
    return mode() in (SHADOW, DIRECT)


def may_act() -> bool:
    """Whether anything is allowed to execute off the reflex path."""
    return mode() == DIRECT


# ---------------------------------------------------------------------------
# What the two paths did. Not who was right.
# ---------------------------------------------------------------------------

AGREED = "AGREED"
DISAGREED = "DISAGREED"
SHADOW_ABSTAINED = "SHADOW_ABSTAINED"
PRODUCTION_ABSTAINED = "PRODUCTION_ABSTAINED"
BOTH_ABSTAINED = "BOTH_ABSTAINED"
NOT_COMPARABLE = "NOT_COMPARABLE"

COMPARISONS = (AGREED, DISAGREED, SHADOW_ABSTAINED, PRODUCTION_ABSTAINED,
               BOTH_ABSTAINED, NOT_COMPARABLE)

# ---------------------------------------------------------------------------
# Where a claim of correctness came from
# ---------------------------------------------------------------------------

VERIFIED_ACTION_RESULT = "VERIFIED_ACTION_RESULT"    # the outcome was observed
EXPLICIT_USER_CORRECTION = "EXPLICIT_USER_CORRECTION"
DURABLE_SEMANTIC_GOAL = "DURABLE_SEMANTIC_GOAL"
HUMAN_REVIEW = "HUMAN_REVIEW"
PRODUCTION_ROUTE_ONLY = "PRODUCTION_ROUTE_ONLY"      # a signal, not the truth

LABEL_SOURCES = (VERIFIED_ACTION_RESULT, EXPLICIT_USER_CORRECTION,
                 DURABLE_SEMANTIC_GOAL, HUMAN_REVIEW, PRODUCTION_ROUTE_ONLY)

GROUNDED_LABEL = 'GROUNDED'
WEAK_LABEL = 'WEAK'
UNGROUNDED_LABEL = 'UNGROUNDED'
STRONG = 'HIGH'
MEDIUM = 'MEDIUM'
WEAK = 'LOW'

LABEL_STRENGTH = {
    VERIFIED_ACTION_RESULT: (GROUNDED_LABEL, STRONG),
    EXPLICIT_USER_CORRECTION: (GROUNDED_LABEL, STRONG),
    HUMAN_REVIEW: (GROUNDED_LABEL, MEDIUM),
    DURABLE_SEMANTIC_GOAL: (GROUNDED_LABEL, MEDIUM),
    PRODUCTION_ROUTE_ONLY: (WEAK_LABEL, WEAK),
}

#: Sources strong enough to count towards a promotion decision. The production
#: route is deliberately absent.
GROUNDED = tuple(source for source, (grounding, _strength) in LABEL_STRENGTH.items()
                 if grounding == GROUNDED_LABEL)

EXECUTION_TRUTH = 'EXECUTION'
INTENT_TRUTH = 'INTENT'

TRUTHS = (EXECUTION_TRUTH, INTENT_TRUTH)

SETTLES = {
    VERIFIED_ACTION_RESULT: (EXECUTION_TRUTH,),
    DURABLE_SEMANTIC_GOAL: (EXECUTION_TRUTH,),
    EXPLICIT_USER_CORRECTION: (INTENT_TRUTH,),
    HUMAN_REVIEW: (EXECUTION_TRUTH, INTENT_TRUTH),
    PRODUCTION_ROUTE_ONLY: (),
}


def settles(source: str, truth: str) -> bool:
    """Whether a label of this provenance can settle that kind of truth."""
    return truth in SETTLES.get(source, ())


def observed_reliability(*, store=None) -> dict:
    """
    How often each label source has actually turned out right.

    The thing the guessed numbers were pretending to be. Reported as counts
    beside the rate so a rate computed from four rows is visibly a rate
    computed from four rows.
    """
    from friday.toolsets import memory as M

    found = {}
    for row in (store or M.store()).shadow_rows():
        source = row.get("label_source")
        if not source or row.get("intent_correct") is None:
            continue
        seen = found.setdefault(source, {"n": 0, "correct": 0})
        seen["n"] += 1
        seen["correct"] += int(bool(row["intent_correct"]))
    for source, seen in found.items():
        seen["rate"] = (round(seen["correct"] / seen["n"], 4)
                        if seen["n"] >= ENOUGH_TO_JUDGE else "TOO_FEW")
    return found


# ---------------------------------------------------------------------------
# A prediction, which is a record and nothing else
# ---------------------------------------------------------------------------

_ATTEMPTS = 0


def attempted_to_act() -> int:
    """
    How many times anything tried to execute from the shadow path.

    Asserted at zero, permanently. A number above zero is not a metric, it is
    a breach - something reached for execution authority the shadow path is
    built not to have.
    """
    return _ATTEMPTS


@dataclass(frozen=True)
class Prediction:
    """
    What the router would have done. Inert by construction.

    Frozen, and holding no runtime, no principal, no store handle and no route
    to `CapabilityRuntime`. There is deliberately no `execute` - not a method
    that refuses, an absence - because a method that refuses is a method
    somebody can be tempted to change.
    """

    fingerprint: str = ""
    words: int = 0
    input_source: str = "TEXT"
    request_shape: str = ""
    predicted_operation: str = ""
    predicted_target: str = ""
    predicted_capability: str = ""
    predicted_argument_shape: str = ""
    referent_available: bool = False
    referent_type: str = ""
    referent_source: str = ""
    decision: str = "ABSTAIN"           # LOCAL | ABSTAIN
    abstention_reason: str = ""
    blame: str = ""
    winner_score: float = 0.0
    runner_up_score: float = 0.0
    margin: float = 0.0
    latency_ms: float = 0.0
    turn_id: str = ""
    run_id: str = ""

    def as_row(self) -> dict:
        row = {key: getattr(self, key) for key in self.__dataclass_fields__}
        row.update(router_version=ROUTER_VERSION,
                   taxonomy_version=TAXONOMY_VERSION,
                   threshold_version=THRESHOLD_VERSION,
                   at=now_iso())
        return row


def _no_execution(*_args, **_kwargs):
    """
    The thing that happens if anything ever reaches for execution here.

    Counted and refused. `Prediction` has no method that leads here; this
    exists so the test that proves it can prove something rather than assert
    the absence of a name.
    """
    global _ATTEMPTS

    _ATTEMPTS += 1
    logger.error("shadow.execution_attempt - the shadow path has no authority")
    raise PermissionError(
        "the shadow path may observe, predict and record. It may not act.")


# ---------------------------------------------------------------------------
# Fingerprints, not transcripts
# ---------------------------------------------------------------------------


def fingerprint(text: str) -> str:
    """
    A stable handle for an utterance that is not the utterance.

    Enough to notice a sentence recurring and to join a prediction to its
    outcome. One-way, and short enough that it carries no content back.
    """
    return hashlib.sha256((text or "").lower().strip().encode()).hexdigest()[:16]


def argument_shape(arguments: dict) -> str:
    """
    The *names* of the arguments, never their values.

    "which parameters were filled" is the routing question. "what path was
    written" is somebody's private business, and the difference is the whole
    privacy argument for this table.
    """
    return ",".join(sorted(str(key) for key in (arguments or {})))


# ---------------------------------------------------------------------------
# Predicting
# ---------------------------------------------------------------------------


def predict(text: str, *, context=None, source: str = "TEXT",
            turn_id: str = "", run_id: str = "") -> Prediction | None:
    """
    What the router would have done with this. Never acts, never raises.

    Returns None when shadow mode is off or anything at all goes wrong: a
    telemetry path may not cost the boss a reply under any circumstances.
    """
    if not enabled():
        return None
    try:
        from friday import selective as SEL

        decision = SEL.decide(text, context=context)
        evidence = decision.evidence
        return Prediction(
            fingerprint=fingerprint(text),
            words=len((text or "").split()),
            input_source=source,
            request_shape=evidence.shape,
            predicted_operation=evidence.operation,
            predicted_target=evidence.target,
            predicted_capability=decision.capability,
            predicted_argument_shape=argument_shape(decision.arguments),
            referent_available=evidence.referent_grounded,
            referent_type=evidence.target if evidence.referent_grounded else "",
            referent_source="conversation" if evidence.referent_grounded else "",
            decision="LOCAL" if decision.routes else "ABSTAIN",
            abstention_reason=decision.abstained,
            blame=decision.blame,
            winner_score=decision.winner_score,
            runner_up_score=decision.runner_up_score,
            margin=decision.margin,
            latency_ms=decision.milliseconds,
            turn_id=turn_id,
            run_id=run_id,
        )
    except Exception:                                       # noqa: BLE001
        logger.exception("shadow.predict failed; the turn is unaffected")
        return None


# ---------------------------------------------------------------------------
# Writing, off the hot path
# ---------------------------------------------------------------------------

#: Small on purpose. A shadow row is worth less than a responsive Friday, so
#: the queue is allowed to overflow and the overflow is counted.
QUEUE_DEPTH = 256

_QUEUE: "queue.Queue[dict] | None" = None
_WRITER: threading.Thread | None = None
_DROPPED = 0
_WRITTEN = 0


def dropped() -> int:
    return _DROPPED


def written() -> int:
    return _WRITTEN


def queue_depth() -> int:
    return _QUEUE.qsize() if _QUEUE is not None else 0


# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
BATCH = 32


def _writer_loop(store) -> None:
    global _WRITTEN
    while True:
        first = _QUEUE.get()
        if first is None:
            _QUEUE.task_done()
            return
        batch = [first]
        try:
            while len(batch) < BATCH:
                batch.append(_QUEUE.get_nowait())
        except queue.Empty:
            pass
        stop = batch and batch[-1] is None
        if stop:
            batch.pop()
        try:
            for row in batch:
                store.record_shadow(**row)
                _WRITTEN += 1
        except Exception:                                    # noqa: BLE001
            logger.exception("shadow.write failed; dropping %d rows", len(batch))
        finally:
            for _ in batch:
                _QUEUE.task_done()
            if stop:
                _QUEUE.task_done()
                return


def _ensure_writer(store) -> bool:
    """
    Start the background writer, with its own database handle.

    `Store` sets `check_same_thread=False`, which lets a connection cross
    threads and does not make it safe to *use* from two at once. Sharing the
    main handle produced, live:

        SystemError: <method 'rollback' of 'sqlite3.Connection' objects>
        returned NULL without setting an exception

    - the writer and the turn racing inside one transaction. So the thread
    opens its own connection to the same file, which is what SQLite is
    actually good at.

    Returns False when that is impossible - an in-memory store exists only
    inside its own connection - and the caller writes synchronously instead.
    """
    global _QUEUE, _WRITER
    if _WRITER is not None and _WRITER.is_alive():
        return True
    path = str(getattr(store, "path", "") or "")
    if not path or path == ":memory:":
        return False
    from friday.store import Store

    _QUEUE = queue.Queue(maxsize=QUEUE_DEPTH)
    _WRITER = threading.Thread(target=_writer_loop, args=(Store(path),),
                               name="friday-shadow", daemon=True)
    _WRITER.start()
    return True


def record(prediction: Prediction, *, store=None, blocking: bool = False) -> bool:
    """
    Queue a prediction for writing. Returns whether it was accepted.

    Never blocks the caller. Under load the row is dropped and counted, which
    is the right trade every time: a missing observation costs a data point
    and a stalled turn costs the boss his assistant.
    """
    global _DROPPED
    if prediction is None:
        return False
    try:
        from friday.toolsets import memory as M

        store = store or M.store()
        if blocking or not _ensure_writer(store):
            store.record_shadow(**prediction.as_row())
            return True
        _QUEUE.put_nowait(prediction.as_row())
        return True
    except queue.Full:
        _DROPPED += 1
        logger.debug("shadow.dropped depth=%d total=%d", queue_depth(), _DROPPED)
        return False
    except Exception:                                        # noqa: BLE001
        _DROPPED += 1
        logger.exception("shadow.record failed; dropping the row")
        return False


def observe(text: str, *, store=None, context=None, source: str = "TEXT",
            turn_id: str = "", run_id: str = "", blocking: bool = False):
    """Predict and record in one call. The live path uses this."""
    prediction = predict(text, context=context, source=source,
                         turn_id=turn_id, run_id=run_id)
    if prediction is None:
        return None
    record(prediction, store=store, blocking=blocking)
    return prediction


def drain(timeout: float = 2.0) -> None:
    """Wait for queued rows to land. For tests and shutdown, not the hot path."""
    if _QUEUE is None:
        return
    # `unfinished_tasks` rather than `empty()`: a row the writer has taken off
    # the queue but not yet written is still in flight, and a test that
    # checks the store the moment the queue looks empty reads before it.
    deadline = timeout
    step = 0.005
    while _QUEUE.unfinished_tasks and deadline > 0:
        threading.Event().wait(step)
        deadline -= step


def reset() -> None:
    """Forget the writer and the counters, so a test starts clean."""
    global _QUEUE, _WRITER, _DROPPED, _WRITTEN, _ATTEMPTS

    if _QUEUE is not None:
        try:
            _QUEUE.put_nowait(None)
        except queue.Full:
            pass
    _QUEUE = None
    _WRITER = None
    _DROPPED = 0
    _WRITTEN = 0
    _ATTEMPTS = 0


# ---------------------------------------------------------------------------
# Comparing, and separately, judging
# ---------------------------------------------------------------------------


def compare(text: str, *, production_capability: str = "", store=None,
            action_status: str = "", verified: bool | None = None) -> str | None:
    """
    Record what Friday actually did beside what the router would have done.

    `comparison_status` says what the two paths did and nothing about who was
    right. Correctness is settled separately, by `judge`, and only where there
    is evidence for it.
    """
    if not enabled():
        return None
    try:
        from friday.toolsets import memory as M

        store = store or M.store()
        row = store.shadow_prediction(fingerprint(text))
        if row is None:
            return None
        status = _comparison(row.get("predicted_capability") or "",
                             production_capability or "")
        source, execution, intent = _label(row, production_capability,
                                           action_status, verified, status)
        grounding, strength = LABEL_STRENGTH.get(
            source, (UNGROUNDED_LABEL, WEAK))
        store.settle_shadow(row["id"],
                            production_capability=production_capability or "",
                            action_result_status=action_status or "",
                            comparison_status=status,
                            label_source=source,
                            label_grounding=grounding if source else "",
                            label_strength=strength if source else "",
                            execution_correct=execution,
                            intent_correct=intent)
        logger.info("shadow.%s predicted=%s production=%s label=%s",
                    status.lower(),
                    row.get("predicted_capability") or row.get("abstention_reason"),
                    production_capability or "-", source or "-")
        return status
    except Exception:                                        # noqa: BLE001
        logger.exception("shadow.compare failed")
        return None


def _comparison(predicted: str, production: str) -> str:
    if predicted and production:
        return AGREED if predicted == production else DISAGREED
    if predicted and not production:
        return PRODUCTION_ABSTAINED
    if production and not predicted:
        return SHADOW_ABSTAINED
    return BOTH_ABSTAINED


def _label(row: dict, production: str, action_status: str,
           verified: bool | None, status: str):
    """
    Whether anything here is strong enough to call the shadow right or wrong.

    The hierarchy in one place. A verified outcome is the strongest thing
    available without a person; the production route on its own is a signal
    worth storing and never a verdict, because the incumbent mis-routes too
    and a router taught to match it would learn its mistakes.
    """
    predicted = row.get("predicted_capability") or ""

    if verified is not None and predicted and production:
        if verified:
            # The action verifiably happened, so execution is settled. Intent
            # is not: the same evidence says nothing about what was meant.
            return VERIFIED_ACTION_RESULT, predicted == production, None
        # A verified failure grounds the label without settling either truth.
        return VERIFIED_ACTION_RESULT, None, None

    if status in (AGREED, DISAGREED):
        return PRODUCTION_ROUTE_ONLY, None, None
    return "", None, None


def judge(text: str, *, correct: bool, source: str, truth: str = INTENT_TRUTH,
          store=None) -> bool:
    """
    Attach a grounded correctness label after the fact.

    Used by the correction path and by human review. Refuses a source that is
    not in the hierarchy, because an unlabelled label is worse than none - it
    will be believed later by somebody who cannot see where it came from.
    """
    if source not in LABEL_SOURCES:
        raise ValueError(f"unknown label source {source!r}")
    if not settles(source, truth):
        raise ValueError(
            f"{source} cannot settle {truth} truth - see SETTLES. A machine observing its own side effect does not know what was meant.")
    try:
        from friday.toolsets import memory as M

        store = store or M.store()
        row = store.shadow_prediction(fingerprint(text), settled=True)
        if row is None:
            return False
        grounding, strength = LABEL_STRENGTH[source]
        column = ("intent_correct" if truth == INTENT_TRUTH
                  else "execution_correct")
        store.settle_shadow(row["id"], label_source=source,
                            label_grounding=grounding,
                            label_strength=strength, **{column: correct})
        return True
    except Exception:                                        # noqa: BLE001
        logger.exception("shadow.judge failed")
        return False


# ---------------------------------------------------------------------------
# Corrections, which are the most valuable thing here
# ---------------------------------------------------------------------------

#: "No, I meant the music." The only place real language says what the answer
#: should have been, rather than only that it was wrong.
_CORRECTION = re.compile(
    r"^\s*(?:no[,.]?\s+|nope[,.]?\s+)?(?:i (?:meant|said)\b|i was talking about\b|that'?s not\b|not (?:that|the|those|it)\b|wrong (?:one|app|window|song|thing|file)\b)",
    re.IGNORECASE)


def looks_like_a_correction(text: str) -> bool:
    return bool(_CORRECTION.match((text or "").strip()))


def record_correction(text: str, *, previous: dict | None = None,
                      store=None) -> dict | None:
    """
    Turn a correction into routing evidence without keeping the sentence.

    What is stored is the shape of the mistake - which operation, which
    target, which capability was predicted, and what the corrected reading
    is - never the words. One correction does not change production routing;
    it becomes a development case, and development cases are reviewed in
    batches for a general cause.
    """
    if not enabled() or not looks_like_a_correction(text):
        return None
    try:
        from friday import selective as SEL
        from friday.toolsets import memory as M

        corrected = SEL.decide(text)
        previous = previous or {}
        entry = {
            "fingerprint": fingerprint(text),
            "previous_operation": previous.get("predicted_operation", ""),
            "previous_target": previous.get("predicted_target", ""),
            "previous_capability": previous.get("predicted_capability", ""),
            "corrected_operation": corrected.evidence.operation,
            "corrected_target": corrected.evidence.target,
            "corrected_capability": corrected.capability,
            "referent_type": corrected.evidence.target,
            "evidence": EXPLICIT_USER_CORRECTION,
            "at": now_iso(),
        }
        (store or M.store()).record_routing_correction(**entry)
        logger.info("shadow.correction previous=%s corrected=%s",
                    entry["previous_capability"] or "-",
                    entry["corrected_capability"] or "-")
        return entry
    except Exception:                                       # noqa: BLE001
        logger.exception("shadow.correction failed")
        return None


# ---------------------------------------------------------------------------
# What it has learned
# ---------------------------------------------------------------------------

#: Below this many grounded labels, a percentage is a story rather than a
#: measurement and is refused.
ENOUGH_TO_JUDGE = 30


def status(*, store=None) -> dict:
    """
    The report. Counts what happened and refuses to compute what it cannot.

    `precision` is deliberately absent until there are enough grounded labels
    to have a denominator. Three correct out of three is not 100%, it is three.
    """
    from friday.toolsets import memory as M

    rows = (store or M.store()).shadow_rows()
    observed = len(rows)
    local = sum(1 for row in rows if row.get("decision") == "LOCAL")
    abstained = observed - local

    comparisons = {}
    blame = {}
    grounded_right = grounded_wrong = dangerous_wrong = 0
    execution_grounded = intent_grounded = 0
    for row in rows:
        key = row.get("comparison_status") or "PENDING"
        comparisons[key] = comparisons.get(key, 0) + 1
        if row.get("blame"):
            blame[row["blame"]] = blame.get(row["blame"], 0) + 1
        if row.get("execution_correct") is not None:
            execution_grounded += 1
        if (row.get("label_source") in GROUNDED
                and row.get("intent_correct") is not None):
            intent_grounded += 1
            if row["intent_correct"]:
                grounded_right += 1
            else:
                grounded_wrong += 1
                from friday import reflex as X

                if X.is_dangerous(row.get("predicted_capability") or ""):
                    dangerous_wrong += 1

    grounded = grounded_right + grounded_wrong
    latencies = sorted(float(row.get("latency_ms") or 0.0) for row in rows)
    report = {
        "mode": mode(),
        "router_version": ROUTER_VERSION,
        "observed": observed,
        "local": local,
        "abstained": abstained,
        "real_coverage": round(local / observed, 4) if observed else 0.0,
        "comparisons": comparisons,
        "blame": blame,
        "grounded_labels": grounded,
        "execution_grounded": execution_grounded,
        "intent_grounded": intent_grounded,
        "both_grounded": min(execution_grounded, intent_grounded),
        "unlabeled": observed - max(execution_grounded, intent_grounded),
        "corrections": len((store or M.store()).routing_corrections()),
        "verified_correct": grounded_right,
        "verified_wrong": grounded_wrong,
        "dangerous_wrong": dangerous_wrong,
        **{
            "queue_depth": queue_depth(),
            "dropped": dropped(),
            "written": written(),
            "execution_attempts": attempted_to_act(),
        },
    }
    if latencies:
        report["median_latency_ms"] = round(latencies[len(latencies) // 2], 3)
        report["p95_latency_ms"] = round(
            latencies[max(0, int(len(latencies) * 0.95) - 1)], 3)
    # Precision is only a number once there is a denominator worth the name.
    # Below the bar it says so, rather than reporting three-for-three as 100%.
    if grounded >= ENOUGH_TO_JUDGE:
        report["real_precision"] = round(grounded_right / grounded, 4)
    else:
        report["real_precision"] = "UNMEASURED"
    report["precision"] = report["real_precision"]
    return report


def spoken(*, store=None) -> str:
    """One sentence for Friday to say. The table belongs on a screen."""
    found = status(store=store)
    if not found["observed"]:
        return "I haven't watched any real turns yet."
    line = (f"I've watched {found['observed']} real turns. "
            f"I'd have handled {found['local']} of them locally and stepped "
            f"back from {found['abstained']}.")
    if isinstance(found["precision"], str):
        line += " I don't have enough verified outcomes to say whether I'd " \
                "have been right, so nothing changes yet."
    else:
        line += (f" Of the ones I can check, I'd have been right "
                 f"{found['precision']:.0%} of the time.")
    return line


def purge(*, before: str = "", store=None) -> int:
    """Delete shadow rows. Behavioural metadata does not accumulate for ever."""
    from friday.toolsets import memory as M

    return (store or M.store()).purge_shadow(before=before)
