"""
Which run is "that one"?

`run_id` is system identity. It is not user ergonomics, and it is not
something the boss can be expected to have. Asked "how did that product
catalogue job finish?" in a fresh conversation, Friday called a status-by-id
tool, had no id, and asked *him* for one - which he could not know, because
Friday invented it.

So resolution is a first-class step. It returns a named basis rather than a
sentence, because two different callers need two different things from it:

    a reader   may act on a likely answer, as long as it says which it picked
    a mutator  may not. "Retry those" against the wrong run reprocesses
               somebody else's catalogue, and picking the newest merely
               because it is newest is exactly the silent guess that must not
               happen where there are side effects.

`safe_to_mutate` is the whole point of the type. Everything else here exists
to make that one boolean defensible.

Deliberately a pure function over a list of run dicts rather than a class
holding a store: every domain already knows how to list its own runs, and a
resolver that cannot be tested without a database gets tested less.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- how the run was arrived at --------------------------------------------

EXPLICIT_RUN_ID = "EXPLICIT_RUN_ID"          # the caller said which
CURRENT_ACTIVE_RUN = "CURRENT_ACTIVE_RUN"    # this process started it
UNIQUE_RECENT_MATCH = "UNIQUE_RECENT_MATCH"  # one run fits what he said
LAST_DOMAIN_RUN = "LAST_DOMAIN_RUN"          # the newest, and there are others
ONLY_RUN = "ONLY_RUN"                        # the newest, and it is the only one
AMBIGUOUS = "AMBIGUOUS"                      # several fit; do not guess
NOTHING_RECORDED = "NOTHING_RECORDED"

BASES = (EXPLICIT_RUN_ID, CURRENT_ACTIVE_RUN, UNIQUE_RECENT_MATCH,
         LAST_DOMAIN_RUN, ONLY_RUN, AMBIGUOUS, NOTHING_RECORDED)

# --- and how sure that makes us --------------------------------------------

CERTAIN = "CERTAIN"      # exactly one run can be meant
LIKELY = "LIKELY"        # probably this one, and others exist
NONE = "NONE"            # no answer

#: The bases that permit a side effect without asking first.
SAFE_TO_MUTATE = frozenset({EXPLICIT_RUN_ID, CURRENT_ACTIVE_RUN,
                            UNIQUE_RECENT_MATCH, ONLY_RUN})


@dataclass(frozen=True)
class Resolution:
    run_id: str
    basis: str
    reason: str
    candidate_count: int = 0
    confidence: str = NONE
    candidates: tuple[dict, ...] = field(default=())

    def __bool__(self) -> bool:
        return bool(self.run_id)

    @property
    def safe_to_mutate(self) -> bool:
        """
        May a side-effecting call proceed on this, or must it ask?

        LAST_DOMAIN_RUN is deliberately excluded. Reading the newest of several
        runs and naming it is helpful; *retrying* the newest of several because
        it happens to be newest is a coin toss with side effects.
        """
        return bool(self.run_id) and self.basis in SAFE_TO_MUTATE


def _label(run: dict) -> str:
    return f"{run.get('run_id', '')} ({run.get('source') or 'no source'})"


def _matches(run: dict, hint: str) -> bool:
    needle = hint.strip().lower()
    return any(needle in str(run.get(field_, "")).lower()
               for field_ in ("run_id", "source", "label", "name"))


def resolve(runs, *, hint: str = "", noun: str = "run",
            active_run_id: str = "") -> Resolution:
    """
    Pick the run a request means, from the runs a domain has recorded.

    `runs` is newest-first, as every `*_runs` listing already returns them.
    `hint` is whatever the request narrowed by - a file name, part of an id.
    `active_run_id` is the run this process started, if any: in a conversation
    that just processed a catalogue, "those" means that one and nothing else,
    however many are in the database from before.
    """
    runs = list(runs or [])
    if not runs:
        return Resolution("", NOTHING_RECORDED,
                          f"no {noun} has been recorded yet", 0, NONE)

    hint = (hint or "").strip()
    if hint:
        hits = [r for r in runs if _matches(r, hint)]
        if len(hits) == 1:
            return Resolution(
                hits[0]["run_id"], UNIQUE_RECENT_MATCH,
                f"the one {noun} matching {hint!r}: {_label(hits[0])}",
                1, CERTAIN)
        if len(hits) > 1:
            return Resolution(
                "", AMBIGUOUS,
                f"{len(hits)} {noun}s match {hint!r} - say which one: "
                + ", ".join(_label(h) for h in hits[:5]),
                len(hits), NONE, tuple(hits[:5]))
        # No match. Fall through rather than failing: the hint may be his
        # words for the job rather than anything we recorded.

    if active_run_id and any(r["run_id"] == active_run_id for r in runs):
        active = next(r for r in runs if r["run_id"] == active_run_id)
        return Resolution(
            active_run_id, CURRENT_ACTIVE_RUN,
            f"the {noun} this session started: {_label(active)}",
            1, CERTAIN)

    newest = runs[0]
    if len(runs) == 1:
        return Resolution(newest["run_id"], ONLY_RUN,
                          f"the only {noun}: {_label(newest)}", 1, CERTAIN)
    return Resolution(
        newest["run_id"], LAST_DOMAIN_RUN,
        f"the most recent of {len(runs)} {noun}s: {_label(newest)}",
        len(runs), LIKELY, tuple(runs[:5]))
