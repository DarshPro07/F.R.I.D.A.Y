"""Context health: score what is in a context, name its state, act on it (CTX-004).

PRD v7 section 19.5 asks for a tracked set of metrics, four states (HEALTHY,
NOISY, DEGRADED, ROTTING) and four responses (prune, summarize, fold, hand off
to a fresh session). This module is the pure part of that: it reads the
aggregate-shaped dict `memory_stack.aggregate()` returns - its `layout`, one
record per prompt line - and returns a verdict. It touches no store, no file
and no model.

Measured from the layout: utilization (tokens over budget, so a context at or
under its budget scores 1.0 or less), relevance density, duplicate ratio, stale
ratio (only lines that carry a timestamp), contradiction score and raw-output
ratio. Three more are measured only from what a line records about itself, and
are None - named in the verdict's `unmeasured`, never given a number - when no
line does: failed-attempt density (lines of `kind` FAILED_ATTEMPT over all
lines, once any line is an ATTEMPT or a FAILED_ATTEMPT), struggle score (the
share of the last STRUGGLE_WINDOW attempts that failed: how the work is going
now, where density is the whole context; a genome lesson, KNOWN_FAILURE, counts
toward density only, so struggle stays None until something records real attempts) and retrieval confidence (the mean
`score`, 0..1, of the lines that carry one). Like relevance density they are
reported and drive nothing.

The state is the worst level any driver reaches. Relevance density is reported
but drives nothing: rules and preferences are always-on and match no task, so a
low density is the normal case. An open contradiction raises the state but not
the action - it is a critical fact, nothing may trim it, only the person can
resolve it - so the verdict lists it under `awaiting_person` instead of acting
on every turn to no effect.

Actions. NOISY prunes (duplicates, stale and raw-dump lines). DEGRADED
summarizes (the same, plus clipping and a per-section cap), or folds when one
non-critical section holds most of the tokens: the section's lines become one
line that points at the original in the artifact vault, and the rest is
summarized. ROTTING resets: the whole non-critical context is ingested by a
temporary `Branch` (19.6) and folded out as one recoverable line, and the
context `remediate` returns is the fresh session - the rules, preferences and
open contradictions plus that line. This is opt-in (`remediate(..., reset=True)`)
for a caller that owns a session and swaps its state for the returned aggregate
when `applied` is HANDOFF; `restart` in the verdict names where the old context
is kept. A stateless per-request caller gets the summarize action instead. When the vault cannot keep the old
context the reset is not made: the context is shrunk in place with the
summarize action, as before, and `applied` says so.

Every change to a prompt is checked against `compaction.CriticalFactSet`
(rules, preferences, open contradictions); a change that would drop one is
refused and the original is kept (CTX-003).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from friday import compaction, compression

log = logging.getLogger("friday.context_health")

HEALTHY, NOISY, DEGRADED, ROTTING = "HEALTHY", "NOISY", "DEGRADED", "ROTTING"
STATES = (HEALTHY, NOISY, DEGRADED, ROTTING)

NONE, PRUNE, SUMMARIZE, FOLD, HANDOFF = "none", "prune", "summarize", "fold", "handoff"
ACTION_FOR = {HEALTHY: NONE, NOISY: PRUNE, DEGRADED: SUMMARIZE, ROTTING: HANDOFF}

#: `kind` on a layout record for an attempt at something, and for one that failed.
ATTEMPT, FAILED_ATTEMPT = "attempt", "failed_attempt"
#: `kind` of a historical, root-caused lesson (the failure genome). It is a failure
#: the context carries, not an attempt made in this session, so it counts toward
#: failed-attempt density and never toward the struggle window.
KNOWN_FAILURE = "known_failure"
#: Struggle score looks at this many of the newest attempts.
STRUGGLE_WINDOW = 5
#: The metrics that exist only when a line records them (see the module doc).
NEEDS_SOURCE = ("failed_attempt_density", "struggle_score", "retrieval_confidence")

#: Tiers whose lines are the person's standing instructions; never pruned or clipped.
CRITICAL_TIERS = ("preferences", "rules")
#: `kind` on a layout record for an unresolved contradiction; also never dropped.
CONTRADICTION = "contradiction"
#: Time-ordered tier: when a section is capped the newest lines are kept.
RECENT_TIER = "episodes"
#: Drivers no action can clear, because only the person can (see `assess`).
NEEDS_PERSON = ("contradiction_score",)

#: metric -> (NOISY, DEGRADED, ROTTING) lower bounds. Override with
#: FRIDAY_CTX_<METRIC>="a,b,c" (ascending).
_THRESHOLDS = {
    "utilization": (1.05, 1.5, 2.5),
    "duplicate_ratio": (0.15, 0.30, 0.50),
    "stale_ratio": (0.30, 0.50, 0.75),
    "contradiction_score": (0.05, 0.15, 0.30),
    "raw_output_ratio": (0.20, 0.40, 0.60),
}
#: A line longer than this is a raw dump, not a memory.
RAW_LINE_CHARS = 600
#: An episode older than this is stale.
STALE_AFTER_DAYS = 14
#: Under this many lines the ratio drivers cap at NOISY: a tiny context is cheap
#: to carry and one repeated line is not rot. Utilization is never capped.
MIN_ENTRIES = 6
#: Summarize clips a non-critical line to this and keeps this many per section.
SUMMARY_LINE_CHARS = 96
SUMMARY_SECTION_LINES = 4
#: The name `summarize`'s line clipper is registered under with the coordinator.
CLIP = "clip"

#: `kind` of the one line a fold leaves behind; summarize keeps it as it is.
FOLDED = "folded"
#: One non-critical section holding this share of the tokens is folded, not summarized.
FOLD_SHARE = 0.5
#: Pruning a context that is over its budget trims it to this share of it.
FIT_TARGET = 0.8


def _env_number(name: str, default, cast):
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return cast(raw)
    except ValueError:
        log.warning("%s=%r is not a %s; using %r", name, raw, cast.__name__, default)
        return default


def thresholds() -> dict:
    """The driver thresholds in force, environment overrides applied."""
    out = {}
    for metric, default in _THRESHOLDS.items():
        name = "FRIDAY_CTX_" + metric.upper()
        raw = os.getenv(name)
        triple = default
        if raw and raw.strip():
            try:
                parsed = tuple(float(p) for p in raw.split(","))
            except ValueError:
                parsed = ()
            if len(parsed) == 3 and parsed == tuple(sorted(parsed)):
                triple = parsed
            else:
                log.warning("%s=%r is not three ascending numbers; using %r", name, raw, default)
        out[metric] = triple
    return out


def _tokens(text: str) -> int:
    """The estimate `memory_stack` budgets with (four characters a token)."""
    return max(1, len(text) // 4)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _entries(layout) -> list:
    """The records that are content; a header record has no tier."""
    return [e for e in layout or () if e.get("tier")]


def _critical(entry: dict) -> bool:
    return entry.get("tier") in CRITICAL_TIERS or entry.get("kind") == CONTRADICTION


def _parse_ts(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _is_stale(entry: dict, now: datetime) -> bool:
    when = _parse_ts(entry.get("ts"))
    limit = _env_number("FRIDAY_CTX_STALE_AFTER_DAYS", STALE_AFTER_DAYS, float)
    return when is not None and now - when > timedelta(days=limit)


def _is_raw(entry: dict) -> bool:
    return len(entry.get("text") or "") > _env_number("FRIDAY_CTX_RAW_LINE_CHARS",
                                                      RAW_LINE_CHARS, int)


def _now(now) -> datetime:
    return now or datetime.now(timezone.utc)


def measure(agg: dict, now: datetime | None = None) -> dict:
    """The section 19.5 metrics for `agg`. A metric with no honest source on
    this context is None, never zero."""
    now = _now(now)
    entries = _entries(agg.get("layout"))
    n = len(entries)
    total = sum(_tokens(e["text"]) for e in entries)
    used = agg.get("tokens_used", total)
    budget = agg.get("budget_tokens") or 0
    seen, dup = set(), 0
    for e in entries:
        key = _norm(e["text"])
        dup += key in seen
        seen.add(key)
    stamped = [e for e in entries if _parse_ts(e.get("ts")) is not None]
    task = {w for w in _norm(agg.get("task", "")).split() if len(w) >= 3}
    tried = [e for e in entries if e.get("kind") in (ATTEMPT, FAILED_ATTEMPT)]
    recent = [e["kind"] == FAILED_ATTEMPT for e in tried[-STRUGGLE_WINDOW:]]
    scores = [min(1.0, max(0.0, float(e["score"]))) for e in entries
              if isinstance(e.get("score"), (int, float)) and not isinstance(e["score"], bool)]
    return {
        "entries": n,
        "tokens": total,
        "utilization": round(used / budget, 4) if budget else None,
        "relevance_density": (round(sum(1 for e in entries
                                        if task & set(_norm(e["text"]).split())) / n, 4)
                              if task and n else None),
        "duplicate_ratio": round(dup / n, 4) if n else 0.0,
        "stale_ratio": (round(sum(_is_stale(e, now) for e in stamped) / n, 4)
                        if stamped else None),
        "contradiction_score": (round(sum(e.get("kind") == CONTRADICTION for e in entries) / n, 4)
                                if n else 0.0),
        "raw_output_ratio": (round(sum(_tokens(e["text"]) for e in entries if _is_raw(e))
                                   / total, 4) if total else 0.0),
        "failed_attempt_density": (round(sum(e.get("kind") in (FAILED_ATTEMPT, KNOWN_FAILURE)
                                             for e in entries) / n, 4)
                                   if tried or any(e.get("kind") == KNOWN_FAILURE
                                                   for e in entries) else None),
        "struggle_score": round(sum(recent) / len(recent), 4) if recent else None,
        "retrieval_confidence": round(sum(scores) / len(scores), 4) if scores else None,
    }


def _bulk(entries: list) -> tuple[str | None, float]:
    """The ingested-material tier (not the conversation itself, which is
    summarized, not folded) holding the largest share of the tokens."""
    total = sum(_tokens(e["text"]) for e in entries) or 1
    by_tier: dict = {}
    for e in entries:
        if not _critical(e) and e["tier"] != RECENT_TIER:
            by_tier[e["tier"]] = by_tier.get(e["tier"], 0) + _tokens(e["text"])
    if not by_tier:
        return None, 0.0
    tier = max(by_tier, key=by_tier.get)
    return tier, by_tier[tier] / total


def assess(agg: dict, now: datetime | None = None) -> dict:
    """The verdict: metrics, state, the drivers that set it, and the action it calls for."""
    metrics = measure(agg, now)
    small = metrics["entries"] < _env_number("FRIDAY_CTX_MIN_ENTRIES", MIN_ENTRIES, int)
    level, fixable, drivers = 0, 0, {}
    for name, bounds in thresholds().items():
        value = metrics.get(name)
        if value is None:
            continue
        reached = sum(1 for b in bounds if value >= b)
        if small and name != "utilization":
            reached = min(reached, 1)
        if reached:
            drivers[name] = STATES[reached]
            level = max(level, reached)
            if name not in NEEDS_PERSON:
                fixable = max(fixable, reached)
    # The state is the worst driver. The action answers only the drivers an
    # action can clear: an open contradiction is a critical fact, so no prune
    # or summary may drop it and only the person can close it.
    action = ACTION_FOR[STATES[fixable]]
    bulk_tier, bulk_share = _bulk(_entries(agg.get("layout")))
    if STATES[fixable] == DEGRADED and bulk_share >= _env_number("FRIDAY_CTX_FOLD_SHARE",
                                                                 FOLD_SHARE, float):
        action = FOLD
    return {"state": STATES[level], "action": action,
            "awaiting_person": [d for d in drivers if d in NEEDS_PERSON],
            "drivers": drivers, "metrics": metrics,
            "unmeasured": [m for m in NEEDS_SOURCE if metrics[m] is None],
            "bulk_tier": bulk_tier}


def critical_facts(layout) -> compaction.CriticalFactSet:
    """What no action may lose: the standing rules and preferences, and every
    open contradiction, as the lines the prompt carries them in."""
    entries = _entries(layout)
    return compaction.CriticalFactSet(
        constraints=tuple(e["text"] for e in entries if e["tier"] in CRITICAL_TIERS),
        questions=tuple(e["text"] for e in entries if e.get("kind") == CONTRADICTION))


def _tidy(layout: list) -> list:
    """Drop a header whose section kept no line."""
    out, header = [], None
    for e in layout:
        if not e.get("tier"):
            header = e
            continue
        if header is not None:
            out.append(header)
            header = None
        out.append(e)
    return out


def _shorten(text: str, action: str, facts, ledger: list | None, backend: str = "") -> str | None:
    """Run `action` on one line through the compression coordinator. Returns
    the text that replaces it, or None when the coordinator refused and the
    line must stay as it is: its original is in the vault, or it does not go."""
    try:
        out, record = compression.default().transform(
            text, action, backend=backend, facts=facts,
            hint=compression.DROP_AFTER_EXTRACTION if action == compression.DROP
            else compression.SUMMARIZABLE)
    except compaction.LossyCompaction:
        log.info("context health: a line that carries a critical fact stays as it is")
        return None
    if record.refusal:
        log.warning("context health: %s refused (%s)", action, record.refusal)
    if ledger is not None and (record.refusal or record.action != compression.PASS_RAW):
        ledger.append(record)
    return None if record.refusal else out


def _fit(layout: list, target: int, facts=None, ledger: list | None = None) -> list:
    """Drop non-critical lines until the context is within `target` tokens: the
    oldest turns first, then the tail of the other sections - the lines
    `memory_stack.aggregate` would have stopped at the budget before taking."""
    out = list(layout)
    total = sum(_tokens(e["text"]) for e in _entries(out))
    order = ([i for i, e in enumerate(out) if e.get("tier") == RECENT_TIER and not _critical(e)]
             + [i for i in reversed(range(len(out)))
                if out[i].get("tier") and out[i]["tier"] != RECENT_TIER and not _critical(out[i])])
    gone = set()
    for i in order:
        if total <= target:
            break
        if _shorten(out[i]["text"], compression.DROP, facts, ledger) is None:
            continue
        total -= _tokens(out[i]["text"])
        gone.add(i)
    return [e for i, e in enumerate(out) if i not in gone]


def prune(layout, now: datetime | None = None, budget: int = 0,
          ledger: list | None = None) -> list:
    """Duplicates, stale lines and raw dumps out; critical lines stay. A context
    over `budget` is then trimmed to FIT_TARGET of it, so the next few turns
    have room instead of tripping the verdict again at once. Each line let go
    of goes through the compression coordinator, which keeps its original in
    the vault; the records are appended to `ledger`. An exact repeat of a line
    that stays loses nothing and is not a transformation."""
    now, seen, out = _now(now), set(), []
    facts = critical_facts(layout)
    for e in layout:
        if not e.get("tier"):
            out.append(e)
            continue
        key = _norm(e["text"])
        if key in seen:
            continue
        if (not _critical(e) and (_is_stale(e, now) or _is_raw(e))
                and _shorten(e["text"], compression.DROP, facts, ledger) is not None):
            continue
        seen.add(key)
        out.append(e)
    if budget and sum(_tokens(e["text"]) for e in _entries(out)) > budget:
        out = _fit(out, int(budget * _env_number("FRIDAY_CTX_FIT_TARGET", FIT_TARGET, float)),
                   facts, ledger)
    return _tidy(out)


def fold(layout, bulk_tier, ledger: list | None = None) -> list:
    """Collapse the non-critical lines of `bulk_tier` into one line that names
    where the original block is kept, at the place the first of them stood. The
    block goes through the compression coordinator, which keeps it in the vault
    and records it in `ledger`. When the coordinator refuses (a critical fact in
    the block, a vault that cannot keep it) the layout is returned as it was."""
    idx = [i for i, e in enumerate(layout)
           if e.get("tier") == bulk_tier and not _critical(e)]
    if not bulk_tier or len(idx) < 2:
        return list(layout)
    block = "\n".join(dict.fromkeys(layout[i]["text"] for i in idx))
    stub = _shorten(block, compression.FOLD_TO_ARTIFACT, critical_facts(layout), ledger)
    if stub is None:
        return list(layout)
    first, gone = idx[0], set(idx[1:])
    return [dict(e, text=stub, ts=None, kind=FOLDED) if i == first else e
            for i, e in enumerate(layout) if i not in gone]


def _clip(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


def _clip_line(text: str) -> str:
    return _clip(text, _env_number("FRIDAY_CTX_SUMMARY_LINE_CHARS", SUMMARY_LINE_CHARS, int))


compression.register_backend(CLIP, _clip_line)


def summarize(layout, now: datetime | None = None, budget: int = 0,
              ledger: list | None = None) -> list:
    """Prune, then clip each non-critical line and cap each section. The
    newest lines survive in the time-ordered tier, the best-ranked elsewhere.
    Clipping and capping go through the compression coordinator like pruning."""
    pruned = prune(layout, now, budget, ledger)
    facts = critical_facts(layout)
    cap = _env_number("FRIDAY_CTX_SUMMARY_SECTION_LINES", SUMMARY_SECTION_LINES, int)
    by_tier: dict = {}
    for i, e in enumerate(pruned):
        if e.get("tier") and not _critical(e) and e.get("kind") != FOLDED:
            by_tier.setdefault(e["tier"], []).append(i)
    keep = set()
    for tier, idxs in by_tier.items():
        keep.update(idxs[-cap:] if tier == RECENT_TIER else idxs[:cap])
    out = []
    for i, e in enumerate(pruned):
        if not e.get("tier") or _critical(e) or e.get("kind") == FOLDED:
            out.append(e)
        elif i in keep:
            clipped = _shorten(e["text"], compression.SUMMARIZE, facts, ledger, CLIP)
            out.append(e if clipped is None else dict(e, text=clipped))
        elif _shorten(e["text"], compression.DROP, facts, ledger) is None:
            out.append(e)
    return _tidy(out)


def _packet(layout) -> str:
    return "\n".join(e["text"] for e in layout)


#: Heading of the line a branch leaves in its parent, and of the one a reset starts from.
BRANCH_HEADING = "FOLDED FROM A TEMPORARY BRANCH (original in the artifact vault):"
RESET_HEADING = "CARRIED FROM THE PREVIOUS SESSION (original in the artifact vault):"


class Branch:
    """A temporary context branch (PRD v7 19.6). Large material is ingested into
    the branch instead of the parent; `fold_out` writes all of it to the artifact
    vault through the compression coordinator and gives the parent one line: a
    concise result and the pointer to the original. The parent never holds the
    raw material, and no critical line goes into a branch - those stay in the
    parent, whole."""

    def __init__(self, parent, heading: str = BRANCH_HEADING):
        self.parent = list(parent)
        self.heading = heading
        self.entries: list = []
        self.ledger: list = []

    def ingest(self, entries) -> None:
        for e in entries:
            if not e.get("tier"):
                continue
            if _critical(e):
                raise ValueError("a critical line stays in the parent context")
            self.entries.append(e)

    @property
    def recovery(self) -> str:
        """The handle that gives back the whole block ("" before a fold-out)."""
        folds = [r for r in self.ledger if r.action == compression.FOLD_TO_ARTIFACT]
        return folds[-1].recovery if folds else ""

    def _result(self) -> str:
        distinct = list(dict.fromkeys(e["text"] for e in self.entries))
        newest = distinct[-_env_number("FRIDAY_CTX_SUMMARY_SECTION_LINES",
                                       SUMMARY_SECTION_LINES, int):]
        return _clip(" | ".join(_clip_line(t) for t in newest),
                     3 * _env_number("FRIDAY_CTX_SUMMARY_LINE_CHARS", SUMMARY_LINE_CHARS, int))

    def fold_out(self, result: str | None = None) -> list | None:
        """The parent layout with the branch folded into it: a heading and one
        line, `result` (default: the newest lines, clipped) then the pointer.
        None when the coordinator refuses - the vault cannot keep the block -
        and the material stays where it was. Raises `compaction.LossyCompaction`
        if the new parent would lose a critical fact."""
        if not self.entries:
            return list(self.parent)
        facts = critical_facts(self.parent)
        block = "\n".join(dict.fromkeys(e["text"] for e in self.entries))
        stub = _shorten(block, compression.FOLD_TO_ARTIFACT, facts, self.ledger)
        if stub is None:
            return None
        text = "%s %s" % (self._result() if result is None else result, stub)
        out = self.parent + [{"tier": None, "text": self.heading},
                             {"tier": RECENT_TIER, "text": text, "ts": None, "kind": FOLDED}]
        compaction.validate(_packet(out), facts)
        return out


def restart(layout, ledger: list | None = None) -> tuple[list, str] | None:
    """The fresh session for `layout`: its critical lines, with their headings,
    and everything else folded out through a `Branch` into one line. Returns
    (fresh layout, recovery handle of the old context), or None when the old
    context cannot be kept and so cannot be let go of."""
    old = [e for e in _entries(layout) if not _critical(e)]
    kept = _tidy([e for e in layout if not e.get("tier") or _critical(e)])
    if not old:
        return kept, ""
    branch = Branch(kept, RESET_HEADING)
    branch.ingest(old)
    fresh = branch.fold_out()
    if ledger is not None:
        ledger.extend(branch.ledger)
    return None if fresh is None else (fresh, branch.recovery)


def remediate(agg: dict, now: datetime | None = None,
              reset: bool = False) -> tuple[dict, dict]:
    """Act on the verdict. `reset` is for a caller that holds a session and will
    swap its state for the returned aggregate: only then does HANDOFF restart
    from a fresh context. A caller that rebuilds its context on every request
    (the voice brain) leaves it False, and a ROTTING context is summarized in
    place, task-relevant lines kept, with nothing written to the vault. Returns the aggregate with its layout, prompt and
    token count replaced, and the verdict with `applied` (what was done to the
    prompt), `after` (the state it left) and `after_action` (what that state
    would still call for; none means nothing an action can clear is left).
    Refuses, and returns `agg` unchanged, any change that would lose a
    critical fact."""
    verdict = assess(agg, now)
    action = verdict["action"]
    layout = list(agg.get("layout") or ())
    if action == NONE:
        return agg, dict(verdict, applied=NONE)
    budget = agg.get("budget_tokens") or 0
    ledger: list = []
    restarted = restart(layout, ledger) if action == HANDOFF and reset else None
    if action == PRUNE:
        shrunk = prune(layout, now, budget, ledger)
    elif action == FOLD:
        shrunk = summarize(fold(layout, verdict["bulk_tier"], ledger), now, budget, ledger)
    elif restarted is not None:
        shrunk = restarted[0]
    else:
        shrunk = summarize(layout, now, budget, ledger)
    folded = any(r.action == compression.FOLD_TO_ARTIFACT for r in ledger)
    facts = critical_facts(layout)
    try:
        compaction.validate(_packet(shrunk), facts)
    except compaction.LossyCompaction as exc:
        log.error("context health %s: %s refused, it drops %d critical fact(s)",
                  verdict["state"], action, len(exc.missing))
        return agg, dict(verdict, applied=NONE, refused=list(exc.missing))
    after = dict(agg, layout=shrunk, prompt=_packet(shrunk),
                 tokens_used=sum(_tokens(e["text"]) for e in _entries(shrunk)))
    left = assess(after, now)
    out = dict(verdict, applied=PRUNE if action == PRUNE else HANDOFF if restarted is not None
               else FOLD if folded else SUMMARIZE,
               after=left["state"], after_action=left["action"],
               transformations=[t.to_dict() for t in ledger])
    if action == HANDOFF:
        packet = _packet([e for e in _entries(layout) if _critical(e)])
        compaction.validate(packet, facts)
        out["handoff_packet"] = packet
        if restarted is not None:
            out["restart"] = {"recovery": restarted[1]}
    log.info("context health %s -> %s (applied %s, now %s)", verdict["state"], action,
             out["applied"], out["after"])
    return after, out
