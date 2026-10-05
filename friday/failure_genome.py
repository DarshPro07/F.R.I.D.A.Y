"""friday/failure_genome.py -- a root-caused failure as a reusable pattern.

PRD v7 §36: "Meaningful failures become structured reusable knowledge:
symptom; environment/version; task class; failed approach; wrong assumption;
root cause; successful repair; prevention; regression test; skill candidate;
applicability conditions. Before repeating a known failure pattern, retrieve
this structure rather than searching old chat."

What already existed is outcome STATISTICS. `friday/evaluation.py` keeps every
Attempt and computes pass rates (`Record.scored`, `Record.best_for`), which
says an agent fails some fraction of a task class and nothing about why, or
what fixed it last time. This is the other half: one row per failure that was
actually root caused, retrieved by symptom before the same wall is walked into
again.

Shaped on `friday/skill_ladder.py` rather than on evaluation.py's flat JSON
list, for two reasons. Retrieval here is a lookup, not a whole-file read. And
the same anti-pollution rule applies: `capture` refuses a record with no root
cause or no repair, because a symptom with neither is a bug report -- storing
it would make retrieval answer "this broke before" and nothing else, which is
worse than answering nothing. The skill ladder is the sibling for successful
procedures; this is the sibling for failures, and `skill_candidate` is the
field that hands one to the other.

Retrieval is wired as a memory tier (`friday.memory_stack.failures`), so the
matching prior repair is in the bundle BEFORE a model plans the task. Nothing
has to remember to ask -- which is the whole of "before repeating a known
failure pattern".
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

_TABLE = """
CREATE TABLE IF NOT EXISTS failure_genome (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symptom          TEXT NOT NULL,          -- what was observed
    environment      TEXT NOT NULL DEFAULT '',   -- env / version it happened on
    task_class       TEXT NOT NULL DEFAULT '',   -- the kind of work being done
    failed_approach  TEXT NOT NULL DEFAULT '',
    wrong_assumption TEXT NOT NULL DEFAULT '',
    root_cause       TEXT NOT NULL,          -- required: no cause, no record
    repair           TEXT NOT NULL,          -- required: the fix that worked
    prevention       TEXT NOT NULL DEFAULT '',
    regression_test  TEXT NOT NULL DEFAULT '',
    skill_candidate  TEXT NOT NULL DEFAULT '',
    applicability    TEXT NOT NULL DEFAULT '[]', -- conditions, as JSON strings
    hits             INTEGER NOT NULL DEFAULT 0, -- times retrieval surfaced it
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
)
"""

_INDEX = ("CREATE INDEX IF NOT EXISTS failure_genome_class"
          " ON failure_genome (task_class)")

#: A candidate below this score is not the same failure, so it is not offered.
#: Tuned against the scoring below: a differently worded recurrence of one
#: failure clears it; a different failure in the same task class does not.
MATCH_AT = float(os.getenv("FRIDAY_FAILURE_MATCH", "0.35"))
#: At or above this, a new capture is the SAME failure being recorded again,
#: so it updates the row instead of adding a near-duplicate.
DUPLICATE_AT = 0.85


@dataclass
class FailureRecord:
    """One root-caused failure, in the shape PRD §36 asks for.

    `symptom`, `root_cause` and `repair` are positional because a record
    without all three cannot answer the only question asked of it later:
    "this is happening again -- what fixed it?"
    """

    symptom: str
    root_cause: str
    repair: str
    task_class: str = ""
    environment: str = ""
    failed_approach: str = ""
    wrong_assumption: str = ""
    prevention: str = ""
    regression_test: str = ""
    skill_candidate: str = ""
    applicability: list[str] = field(default_factory=list)


def _lexical(text: str) -> set[str]:
    """The memory stack's tokeniser, so a symptom scores here the way a spec
    scores there. Imported lazily: `memory_stack` reaches back into this
    module for its failures tier."""
    from friday.memory_stack import _tokens
    return set(_tokens(text))


def _similarity(a: str, b: str) -> float:
    """Jaccard overlap of the two token sets.

    Jaccard rather than "share of the query's tokens" on purpose: the latter
    lets one verbose stored record match every query, which is exactly the
    failure mode that makes retrieved context untrustworthy.
    """
    ta, tb = _lexical(a), _lexical(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _score(row, symptom: str, task_class: str, environment: str) -> float:
    """How much this stored failure looks like the one being faced.

    A task class on both sides that disagrees scores zero, not a penalty: a
    migration failure worded like a voice-pipeline failure is a different
    failure, and offering its repair would send the caller down a wrong path
    with Friday's authority behind it. A record captured without a class
    stays eligible for everything, which is what an unclassified record
    means.
    """
    if task_class and row["task_class"] and row["task_class"] != task_class:
        return 0.0
    score = 0.75 * _similarity(symptom, row["symptom"])
    if task_class and row["task_class"] == task_class:
        score += 0.15
    if environment and (_lexical(environment) & _lexical(row["environment"])):
        score += 0.10
    return round(score, 3)


class FailureGenome:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from friday.store import DEFAULT_DB
            db_path = os.getenv("ADA_DB") or DEFAULT_DB
        self._path = str(db_path)
        with self._connect() as db:
            db.execute(_TABLE)
            db.execute(_INDEX)

    def _connect(self):
        from friday.dbconn import ledger_connection
        return ledger_connection(self._path)

    # -- writing -----------------------------------------------------------

    def capture(self, record: FailureRecord) -> dict:
        """Store a root-caused failure, or refuse and say what is missing.

        Refusal is the rule, not an edge case. "It broke and I do not know
        why" is a bug report; promoting it to retrievable knowledge means a
        later task gets told a wall exists with no way through it.
        """
        missing = [name for name in ("symptom", "root_cause", "repair")
                   if not (getattr(record, name) or "").strip()]
        if missing:
            return {"status": "refused",
                    "note": "a failure genome record needs a symptom, a root "
                            "cause and the repair that worked; without them "
                            "it is a bug report, not reusable knowledge",
                    "missing": missing}
        now = time.time()
        existing = self._same_failure(record)
        values = (record.symptom.strip(), record.environment,
                  record.task_class, record.failed_approach,
                  record.wrong_assumption, record.root_cause.strip(),
                  record.repair.strip(), record.prevention,
                  record.regression_test, record.skill_candidate,
                  json.dumps(list(record.applicability)))
        if existing is not None:
            with self._connect() as db:
                db.execute(
                    "UPDATE failure_genome SET symptom = ?, environment = ?,"
                    " task_class = ?, failed_approach = ?,"
                    " wrong_assumption = ?, root_cause = ?, repair = ?,"
                    " prevention = ?, regression_test = ?,"
                    " skill_candidate = ?, applicability = ?, updated_at = ?"
                    " WHERE id = ?", values + (now, existing))
            return {"status": "updated", "id": existing,
                    "note": "same failure already on record; the newer "
                            "root cause and repair replace it"}
        with self._connect() as db:
            cur = db.execute(
                "INSERT INTO failure_genome (symptom, environment,"
                " task_class, failed_approach, wrong_assumption, root_cause,"
                " repair, prevention, regression_test, skill_candidate,"
                " applicability, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", values + (now, now))
            new_id = cur.lastrowid
        return {"status": "captured", "id": new_id}

    def _same_failure(self, record: FailureRecord) -> int | None:
        """The id of the row this record is a re-statement of, if any."""
        for row in self._rows():
            if row["task_class"] != record.task_class:
                continue
            if _similarity(record.symptom, row["symptom"]) >= DUPLICATE_AT:
                return row["id"]
        return None

    # -- reading -----------------------------------------------------------

    def _rows(self) -> list:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM failure_genome ORDER BY id").fetchall()

    def matches(self, symptom: str, *, task_class: str = "",
                environment: str = "", limit: int = 3,
                threshold: float | None = None) -> list[dict]:
        """Prior failures that look like this one, best first.

        `hits` is bumped on everything returned. Retrieved-and-ignored and
        never-retrieved are different problems, and only the count tells them
        apart -- a genome nobody's query ever matches is dead weight and
        should be visible as such.
        """
        bar = MATCH_AT if threshold is None else threshold
        scored = []
        for row in self._rows():
            score = _score(row, symptom, task_class, environment)
            if score >= bar:
                scored.append((score, row))
        scored.sort(key=lambda pair: (-pair[0], -pair[1]["id"]))
        out = [dict(row, score=score, applicability=_loads(row["applicability"]))
               for score, row in scored[:max(0, limit)]]
        if out:
            with self._connect() as db:
                db.executemany(
                    "UPDATE failure_genome SET hits = hits + 1 WHERE id = ?",
                    [(item["id"],) for item in out])
        return out

    def retrieve(self, symptom: str, *, task_class: str = "",
                 environment: str = "",
                 threshold: float | None = None) -> dict | None:
        """The one prior repair to read before trying this again, or None.

        `applicability` comes back as captured, unevaluated: the conditions
        are prose about the world ("only on Windows", "only with the broker
        running"), so the caller checks them. Returning a repair whose
        conditions do not hold is the retrieval equivalent of a quick fix.
        """
        found = self.matches(symptom, task_class=task_class,
                             environment=environment, limit=1,
                             threshold=threshold)
        return found[0] if found else None

    def listing(self, task_class: str = "") -> list[dict]:
        rows = self._rows()
        return [dict(r, applicability=_loads(r["applicability"])) for r in rows
                if not task_class or r["task_class"] == task_class]


def _loads(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []
