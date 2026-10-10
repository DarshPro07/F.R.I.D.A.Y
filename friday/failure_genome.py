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

Capture is wired as the MCP tools `failure_capture` / `failure_lookup`
(`friday/tools/vnext_control.py`): Friday records a failure once it has the
root cause and the repair that worked. Without a producer the tier above
would retrieve from an empty table.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
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
    source           TEXT NOT NULL DEFAULT '',   -- '' = a caller vouched; else provenance
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
#: Provenance of a record written by the handoff hook, not by a person or the
#: model that root-caused it: shown wherever the record is shown.
SOURCE_WORKER = "worker-reported, unverified"
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
    #: '' when a caller vouches for the record; `SOURCE_WORKER` when a worker
    #: report produced it with nobody checking.
    source: str = ""


def _lexical(text: str) -> set[str]:
    """The memory stack's tokeniser, so a symptom scores here the way a spec
    scores there. Imported lazily: `memory_stack` reaches back into this
    module for its failures tier."""
    from friday.memory_stack import _tokens
    return set(_tokens(text))


#: Two stems this alike (character-bigram Dice) count as the same word, so a
#: typo or a derived form ("handle"/"handler") still matches.
FUZZY_AT = 0.8
_SUFFIXES = ("ing", "ed", "es", "s")


def _stem(token: str) -> str:
    """Strip one inflectional suffix: timed/times/timing -> tim,
    restarted/restarts -> restart. Deliberately crude and symmetric -- both
    sides of a comparison go through it, so consistency matters, not
    linguistic correctness."""
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[:-len(suffix)]
    return token


def _bigrams(word: str) -> set[str]:
    return {word[i:i + 2] for i in range(len(word) - 1)}


_NEGATING = ("un", "in", "dis", "non", "im", "ir", "il")


def _opposed(a: str, b: str) -> bool:
    """One word is the other with a negating prefix: unauthorized/authorized,
    unavailable/available, invalid/valid. Close in spelling, opposite in
    meaning -- the one pair a fuzzy match must never join."""
    short, long_ = sorted((a, b), key=len)
    return any(long_ == p + short for p in _NEGATING)


def _word_match(a: str, b: str) -> float:
    if a == b:
        return 1.0
    # Digits are identifiers (port 8000 vs 8001, error 404 vs 405): exact only.
    if any(ch.isdigit() for ch in a + b) or _opposed(a, b):
        return 0.0
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    dice = 2 * len(ba & bb) / (len(ba) + len(bb))
    return dice if dice >= FUZZY_AT else 0.0


def _tokens_of(text: str) -> list[str]:
    return sorted({_stem(t) for t in _lexical(text)})


def _similarity(a: str, b: str, *, fuzzy: bool = True) -> float:
    """Overlap of the two symptoms, beyond the exact token.

    Words are stemmed and a near-identical pair counts as a (partial) match,
    then the soft Dice is expressed on the Jaccard scale -- which it equals
    when every match is exact, so MATCH_AT and DUPLICATE_AT keep their
    meaning. Jaccard-shaped rather than "share of the query's tokens" on
    purpose: the latter lets one verbose stored record match every query,
    which is exactly the failure mode that makes retrieved context
    untrustworthy.

    This is morphological, not semantic: two symptoms that share no words
    ("hangs" vs "never returns") still do not match. A fuzzy pair never joins
    a word to its negation or one number to another. `fuzzy=False` is exact
    stemmed-token Jaccard, which de-duplication uses so two distinct failures
    are never merged on spelling alone.
    """
    ta, tb = set(_tokens_of(a)), set(_tokens_of(b))
    if not ta or not tb:
        return 0.0
    match = _word_match if fuzzy else (lambda x, y: 1.0 if x == y else 0.0)
    got = (sum(max(match(x, y) for y in tb) for x in ta)
           + sum(max(match(y, x) for x in ta) for y in tb))
    dice = got / (len(ta) + len(tb))
    return dice / (2 - dice)


# -- applicability ----------------------------------------------------------

_CONDITION = re.compile(r"^\s*([A-Za-z][\w.\-]*)\s*(==|!=|>=|<=|>|<)\s*(\S.*?)\s*$")
_VERSION = re.compile(r"\d+(?:\.\d+)*")


def runtime_facts() -> dict[str, str]:
    """What this process can state about its own world: `os` (windows, linux
    or darwin) and `python` (x.y.z). Anything else a condition names is not
    known here, and is reported unverified rather than guessed."""
    plat = sys.platform
    name = ("windows" if plat.startswith("win") else
            "darwin" if plat == "darwin" else
            "linux" if plat.startswith("linux") else plat)
    return {"os": name, "python": platform.python_version()}


def _version(text: str) -> tuple[int, ...] | None:
    return tuple(int(p) for p in text.split(".")) if _VERSION.fullmatch(text) else None


def _holds(condition: str, facts: dict[str, str]) -> bool | None:
    """True/False when `key <op> value` can be checked against `facts`, None
    when it is prose, names a fact nobody supplied, or orders non-versions."""
    m = _CONDITION.match(condition or "")
    if m is None:
        return None
    key, op, want = m.group(1).lower(), m.group(2), m.group(3).strip()
    have = {str(k).lower(): str(v).strip() for k, v in facts.items()}.get(key)
    if have is None:
        return None
    hv, wv = _version(have), _version(want)
    if hv is not None and wv is not None:
        if op in ("==", "!="):
            # Equal at the precision written: "== 3.11" is any 3.11.x.
            left, right = hv[:len(wv)], wv
        else:
            width = max(len(hv), len(wv))
            left = hv + (0,) * (width - len(hv))
            right = wv + (0,) * (width - len(wv))
    elif op in ("==", "!="):
        left, right = have.lower(), want.lower()
    else:
        return None
    return {"==": left == right, "!=": left != right, ">=": left >= right,
            "<=": left <= right, ">": left > right, "<": left < right}[op]


def evaluate_conditions(conditions: list[str],
                        facts: dict[str, str] | None = None) -> dict[str, list[str]]:
    """Sort a record's applicability conditions into met / unmet /
    unverified against `facts` (default: `runtime_facts()`).

    A condition is checkable when it reads `<fact> <op> <value>` with op one
    of == != >= <= > < and the fact is known; versions compare numerically.
    Everything else is prose about the world and stays `unverified` -- shown
    to the caller, never silently treated as satisfied or as violated.
    """
    known = runtime_facts() if facts is None else facts
    out: dict[str, list[str]] = {"met": [], "unmet": [], "unverified": []}
    for condition in conditions:
        verdict = _holds(condition, known)
        out["unverified" if verdict is None else
            "met" if verdict else "unmet"].append(condition)
    return out


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
            if "source" not in {r[1] for r in
                                db.execute("PRAGMA table_info(failure_genome)")}:
                db.execute("ALTER TABLE failure_genome ADD COLUMN source"
                           " TEXT NOT NULL DEFAULT ''")
            db.execute(_INDEX)

    def _connect(self):
        from friday.dbconn import ledger_connection
        return ledger_connection(self._path)

    # -- writing -----------------------------------------------------------

    def capture(self, record: FailureRecord, *, replace: bool = True) -> dict:
        """Store a root-caused failure, or refuse and say what is missing.

        `replace=False` makes the call insert-only: a restatement of a record
        already on file is skipped and never overwrites it (the handoff hook
        uses this -- unverified worker text must not displace what is stored).

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
        blocked = _inadmissible(record)
        if blocked:
            return {"status": "refused", "missing": [],
                    "note": f"not stored: {blocked}. Retrieved records are "
                            "placed in the model's context, so a failure "
                            "note may not carry secrets or sensitive data"}
        now = time.time()
        existing = self._same_failure(record)
        values = (record.symptom.strip(), record.environment,
                  record.task_class, record.failed_approach,
                  record.wrong_assumption, record.root_cause.strip(),
                  record.repair.strip(), record.prevention,
                  record.regression_test, record.skill_candidate,
                  json.dumps(list(record.applicability)), record.source)
        if existing is not None and not replace:
            return {"status": "skipped", "id": existing,
                    "note": "duplicate of a failure already on record; an "
                            "insert-only capture does not replace it"}
        if existing is not None:
            with self._connect() as db:
                db.execute(
                    "UPDATE failure_genome SET symptom = ?, environment = ?,"
                    " task_class = ?, failed_approach = ?,"
                    " wrong_assumption = ?, root_cause = ?, repair = ?,"
                    " prevention = ?, regression_test = ?,"
                    " skill_candidate = ?, applicability = ?, source = ?,"
                    " updated_at = ? WHERE id = ?", values + (now, existing))
            return {"status": "updated", "id": existing,
                    "note": "same failure already on record; the newer "
                            "root cause and repair replace it"}
        with self._connect() as db:
            cur = db.execute(
                "INSERT INTO failure_genome (symptom, environment,"
                " task_class, failed_approach, wrong_assumption, root_cause,"
                " repair, prevention, regression_test, skill_candidate,"
                " applicability, source, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values + (now, now))
            new_id = cur.lastrowid
        return {"status": "captured", "id": new_id}

    def _same_failure(self, record: FailureRecord) -> int | None:
        """The id of the row this record is a re-statement of, if any."""
        for row in self._rows():
            if row["task_class"] != record.task_class:
                continue
            if _similarity(record.symptom, row["symptom"],
                           fuzzy=False) >= DUPLICATE_AT:
                return row["id"]
        return None

    # -- reading -----------------------------------------------------------

    def _rows(self) -> list:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM failure_genome ORDER BY id").fetchall()

    def matches(self, symptom: str, *, task_class: str = "",
                environment: str = "", limit: int = 3,
                threshold: float | None = None,
                facts: dict[str, str] | None = None) -> list[dict]:
        """Prior failures that look like this one, best first.

        A failure whose applicability conditions are evaluated against
        `facts` (default: `runtime_facts()`) and any of which is unmet is not
        offered: its repair is for a world this is not. Each result carries
        `conditions` (met / unmet / unverified) so the caller still checks
        the ones that are prose.

        `hits` is bumped on everything returned. Retrieved-and-ignored and
        never-retrieved are different problems, and only the count tells them
        apart -- a genome nobody's query ever matches is dead weight and
        should be visible as such.
        """
        bar = MATCH_AT if threshold is None else threshold
        scored = []
        for row in self._rows():
            score = _score(row, symptom, task_class, environment)
            if score < bar:
                continue
            conditions = evaluate_conditions(_loads(row["applicability"]), facts)
            if not conditions["unmet"]:
                scored.append((score, row, conditions))
        scored.sort(key=lambda item: (-item[0], -item[1]["id"]))
        out = [dict(row, score=score, applicability=_loads(row["applicability"]),
                    conditions=conditions)
               for score, row, conditions in scored[:max(0, limit)]]
        if out:
            with self._connect() as db:
                db.executemany(
                    "UPDATE failure_genome SET hits = hits + 1 WHERE id = ?",
                    [(item["id"],) for item in out])
        return out

    def retrieve(self, symptom: str, *, task_class: str = "",
                 environment: str = "",
                 threshold: float | None = None,
                 facts: dict[str, str] | None = None) -> dict | None:
        """The one prior repair to read before trying this again, or None.

        A repair whose checkable conditions fail is never returned (see
        `matches`); `conditions["unverified"]` lists the prose ones ("only
        with the broker running") the caller must still check. Returning a
        repair whose conditions do not hold is the retrieval equivalent of a
        quick fix.
        """
        found = self.matches(symptom, task_class=task_class,
                             environment=environment, limit=1,
                             threshold=threshold, facts=facts)
        return found[0] if found else None

    def listing(self, task_class: str = "") -> list[dict]:
        rows = self._rows()
        return [dict(r, applicability=_loads(r["applicability"])) for r in rows
                if not task_class or r["task_class"] == task_class]


def _inadmissible(record: FailureRecord) -> str | None:
    """Why this record may not be remembered, by the same guard the brain
    ledger applies (NON_NEGOTIABLE 4: secrets never enter memory)."""
    from friday.brain import _sensitive
    text = "\n".join([record.symptom, record.environment, record.task_class,
                      record.failed_approach, record.wrong_assumption,
                      record.root_cause, record.repair, record.prevention,
                      record.regression_test, record.skill_candidate,
                      *record.applicability])
    return _sensitive(text)


def _loads(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []
