"""
SQLite persistence for runs, results, artifacts and memory.

Two design rules taken from auditing the donors:

1. **Memory is not a prompt cache.** Mark-L's memory_manager keeps a 2200-char
   JSON blob and silently deletes the oldest entries to stay under the cap, so
   remembering something new can erase something old with no record. Here rows
   are never trimmed, and recall is a query rather than a paste of everything.

2. **Provenance is required, not optional.** Every memory row carries source,
   scope, confidence and a kind - FACT / PREFERENCE / PATTERN / INFERENCE. An
   inference cannot become a fact by being read back, because the kind is
   stored with the row.

Raw and normalized utterances are kept in separate columns (§14). The raw
utterance is never overwritten; a correction adds the normalized form plus the
reason, evidence and confidence for the change.

Uses stdlib sqlite3 directly; a schema this small does not justify an ORM.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from friday.config import DATA_DIR
from friday.contracts import (PERSON, READ_MATERIAL, ActionResult, Artifact, Run,
                              Verification, now_iso)


class CompletionRefused(RuntimeError):
    """A run was asked to become COMPLETED without the evidence that word
    requires. Raised by `Store.finish_objective_run`, the last writer."""


class _Rows:
    """A statement's result, fully materialised under the store lock.

    Looks like the cursor callers already use: `fetchone`, `fetchall`,
    iteration, `lastrowid`, `rowcount`, `description`. Deliberately no
    `fetchmany`: nothing in the tree calls it, and an unused method is
    dead code the reachability gate is right to refuse."""

    __slots__ = ("_rows", "_i", "lastrowid", "rowcount", "description")

    def __init__(self, cur) -> None:
        self._rows = cur.fetchall() if cur.description is not None else []
        self._i = 0
        self.lastrowid = cur.lastrowid
        self.rowcount = cur.rowcount
        self.description = cur.description

    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        row = self._rows[self._i]
        self._i += 1
        return row

    def fetchall(self):
        out = self._rows[self._i:]
        self._i = len(self._rows)
        return out

    def __iter__(self):
        while self._i < len(self._rows):
            yield self.fetchone()

    def close(self) -> None:
        self._i = len(self._rows)


class _SerializedConnection:
    """The one shared connection, every statement under the store lock.

    A-051 soak finding. `Store` shares ONE sqlite3 connection across
    threads (check_same_thread=False) and serialised only its writes
    (`_tx`); 43 read sites in `store.py` and the continuity engine's
    thread pool ran `self._conn.execute(...)` bare. Two threads on one
    connection share one SQL transaction: a read in thread A opens the
    snapshot, another process commits, and thread B's write inside `_tx`
    then fails at once with "database is locked" (SQLITE_BUSY_SNAPSHOT,
    which the busy timeout never waits out). Every statement is now
    executed AND materialised while the lock is held, so no read
    transaction outlives the lock and no two threads interleave inside
    one transaction. The lock is re-entrant, so `_tx` nests cleanly."""

    __slots__ = ("_conn", "_lock")

    def __init__(self, conn: sqlite3.Connection, lock) -> None:
        self._conn = conn
        self._lock = lock

    def execute(self, sql: str, params=()) -> _Rows:
        with self._lock:
            return _Rows(self._conn.execute(sql, params))

    def executemany(self, sql: str, seq) -> _Rows:
        with self._lock:
            return _Rows(self._conn.executemany(sql, seq))

    def executescript(self, script: str):
        with self._lock:
            return self._conn.executescript(script)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
OBJECTIVE_DELIVERY_TTL_S = 21600


#: Anchored to the repository, not to the working directory.
#:
#: This was `Path("data") / "ada.sqlite3"`, and the bug that found it is worth
#: recording: a scheduled automation fired correctly, ran its steps correctly,
#: and wrote its result to `C:\Windows\System32\data\ada.sqlite3`, because
#: Task Scheduler starts a process in System32. Nothing raised. The run simply
#: was not in the database anyone reads, so a working automation looked like
#: one that never fired.
#:
#: Every detached process has this shape - scheduled tasks, the reminder fire
#: script, anything launched by the OS rather than from a shell - so the fix
#: belongs here rather than in each caller. ADA_DB still overrides it.
DEFAULT_DB = DATA_DIR / "ada.sqlite3"

#: How long a connection waits for another process's write lock before
#: giving up (audit A-038). Ten seconds covers a checkpoint or a slow
#: objective-ledger transaction on a loaded host; anything longer is a
#: hung writer and should surface as an error rather than a silent wait.
BUSY_TIMEOUT_S = 10.0

# Memory kinds. An INFERENCE must never be reported as a FACT.
FACT = "FACT"
PREFERENCE = "PREFERENCE"
PATTERN = "PATTERN"
INFERENCE = "INFERENCE"
MEMORY_KINDS = (FACT, PREFERENCE, PATTERN, INFERENCE)

#: Where a memory came from (PRD v7 MEM-002): `contracts.PERSON` and
#: `contracts.READ_MATERIAL`, the run lineage the rest of FRIDAY carries.
TRUST_CLASSES = (PERSON, READ_MATERIAL)

# PRD v3.1 FR-016 memory classes. `kind` above says how much to trust a
# record (fact vs inference); `memory_type` says what KIND OF THING it is
# and therefore its lifecycle: working memory dies with the objective,
# session memory with the session, the rest are durable until superseded.
MEMORY_TYPES = ("working", "session", "project", "user", "semantic",
                "episodic", "procedural", "codebase", "tool_state")
#: Lifecycle per type: the retention policy a record gets by default.
MEMORY_RETENTION = {
    "working": "objective", "session": "session", "project": "durable",
    "user": "durable", "semantic": "durable", "episodic": "rolling",
    "procedural": "durable", "codebase": "durable", "tool_state": "session",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    request     TEXT NOT NULL,
    state       TEXT NOT NULL,
    capability  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    error       TEXT
);

-- The continuity / durable-objective engine's tables. continuity.py INSERTs
-- into all of these, but the reconstructed schema created only `runs` - the
-- other eight were lost, so a fresh database could not admit an objective at
-- all (the live database had them only from an older schema it was migrated
-- across). DDL below is the authoritative live schema, recovered 2026-08-29
-- and found by scripts/golden_continuous_run.py. IF NOT EXISTS keeps the live
-- database untouched.
CREATE TABLE IF NOT EXISTS run_controls (
    run_id              TEXT PRIMARY KEY REFERENCES runs(run_id),
    outcome             TEXT,
    portion_budget      TEXT NOT NULL,
    total_budget        TEXT NOT NULL,
    counters            TEXT NOT NULL,
    lease_owner         TEXT,
    lease_token         TEXT,
    lease_until         TEXT,
    wake_generation     INTEGER NOT NULL DEFAULT 1,
    checkpoint_version  INTEGER NOT NULL DEFAULT 0,
    last_progress_at    TEXT
);

CREATE TABLE IF NOT EXISTS run_tasks (
    task_id               TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES runs(run_id),
    description           TEXT NOT NULL,
    status                TEXT NOT NULL,
    dependencies          TEXT NOT NULL DEFAULT '[]',
    idempotency_key       TEXT NOT NULL,
    verification_required INTEGER NOT NULL DEFAULT 0,
    result                TEXT NOT NULL DEFAULT '{}',
    evidence_refs         TEXT NOT NULL DEFAULT '[]',
    attempt_count         INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    UNIQUE(run_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS run_task_attempts (
    attempt_id       TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL REFERENCES run_tasks(task_id),
    run_id           TEXT NOT NULL REFERENCES runs(run_id),
    portion_id       TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    status           TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    result_ref       TEXT,
    error            TEXT,
    UNIQUE(run_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS run_portions (
    portion_id       TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(run_id),
    wake_generation  INTEGER NOT NULL,
    lease_token      TEXT NOT NULL,
    status           TEXT NOT NULL,
    action_count     INTEGER NOT NULL DEFAULT 0,
    model_tokens     INTEGER NOT NULL DEFAULT 0,
    started_at       TEXT NOT NULL,
    finished_at      TEXT
);

CREATE TABLE IF NOT EXISTS run_wakes (
    run_id       TEXT PRIMARY KEY REFERENCES runs(run_id),
    generation   INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    task_id      TEXT REFERENCES run_tasks(task_id),
    due_at       TEXT,
    detail       TEXT NOT NULL DEFAULT '',
    signal_key   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_checkpoints (
    run_id           TEXT NOT NULL REFERENCES runs(run_id),
    version          INTEGER NOT NULL,
    portion_id       TEXT NOT NULL REFERENCES run_portions(portion_id),
    current_task_id  TEXT REFERENCES run_tasks(task_id),
    completed_tasks  TEXT NOT NULL DEFAULT '[]',
    evidence_refs    TEXT NOT NULL DEFAULT '[]',
    wake_generation  INTEGER NOT NULL,
    summary           TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    PRIMARY KEY(run_id, version)
);

CREATE TABLE IF NOT EXISTS run_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL REFERENCES runs(run_id),
    task_id        TEXT,
    portion_id     TEXT,
    kind           TEXT NOT NULL,
    message        TEXT NOT NULL,
    evidence_refs  TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL
);

-- AGENT-001: one row per agent FRIDAY spawns (run_id is the executor run id),
-- and an append-only stream of action evidence behind it.
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id         TEXT PRIMARY KEY,
    parent_run_id  TEXT REFERENCES agent_runs(run_id),
    role_id        TEXT NOT NULL DEFAULT '',
    executor_id    TEXT NOT NULL,
    model_route    TEXT NOT NULL DEFAULT '',
    task           TEXT NOT NULL,
    workspace      TEXT NOT NULL,
    status         TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    ended_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_parent ON agent_runs(parent_run_id);

CREATE TABLE IF NOT EXISTS agent_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL REFERENCES agent_runs(run_id),
    kind           TEXT NOT NULL,
    message        TEXT NOT NULL,
    evidence_refs  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS agent_events_no_update BEFORE UPDATE ON agent_events
BEGIN SELECT RAISE(ABORT, 'agent_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS agent_events_no_delete BEFORE DELETE ON agent_events
BEGIN SELECT RAISE(ABORT, 'agent_events is append-only'); END;

CREATE TABLE IF NOT EXISTS run_narrations (
    run_id         TEXT NOT NULL REFERENCES runs(run_id),
    milestone_key  TEXT NOT NULL,
    speech_id      TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY(run_id, milestone_key)
);

CREATE TABLE IF NOT EXISTS tool_results (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT NOT NULL REFERENCES runs(run_id),
    tool_id            TEXT NOT NULL,
    status             TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    completed_at       TEXT,
    output             TEXT,
    error              TEXT,
    side_effects       TEXT,
    verify_method      TEXT,
    verify_evidence    TEXT,
    verify_checked_at  TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id        TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES runs(run_id),
    type               TEXT NOT NULL,
    title              TEXT NOT NULL,
    path_or_uri        TEXT NOT NULL,
    producer           TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    metadata           TEXT,
    verify_method      TEXT NOT NULL,
    verify_evidence    TEXT NOT NULL,
    verify_checked_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject     TEXT NOT NULL,
    value       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'user',
    source      TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0,
    run_id      TEXT,
    created_at  TEXT NOT NULL,
    superseded  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_subject ON memories(subject, superseded);

CREATE TABLE IF NOT EXISTS utterances (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT,
    raw                   TEXT NOT NULL,
    normalized            TEXT,
    correction_reason     TEXT,
    correction_evidence   TEXT,
    correction_confidence REAL,
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    name        TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    summary     TEXT
);

CREATE TABLE IF NOT EXISTS project_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL REFERENCES projects(name),
    decision    TEXT NOT NULL,
    rationale   TEXT,
    source      TEXT NOT NULL,
    run_id      TEXT,
    created_at  TEXT NOT NULL,
    superseded  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_decisions_project
    ON project_decisions(project, superseded);

CREATE TABLE IF NOT EXISTS open_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL REFERENCES projects(name),
    question    TEXT NOT NULL,
    why         TEXT,
    options     TEXT,
    asked_at    TEXT NOT NULL,
    answer      TEXT,
    answered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_open_questions_project
    ON open_questions(project, answered_at);

-- The Project Brain (BRAIN-001): structured, human-inspectable project records
-- of 13 kinds. friday/project_brain.py owns the kinds and the body shapes;
-- this table only keeps them. A record that restates a row in another table
-- (a decision, a requirement, an artifact) points at it through ref instead
-- of copying it. A change supersedes: the old row stays as history, and only
-- one row per (project, kind, key) can be active at a time.
CREATE TABLE IF NOT EXISTS project_brain_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL REFERENCES projects(name),
    kind        TEXT NOT NULL
                CHECK (kind IN ('architecture', 'requirements', 'decisions',
                                'assumptions', 'research', 'failures',
                                'experiments', 'objectives', 'incidents',
                                'artifacts', 'handoffs', 'procedures',
                                'entities')),
    key         TEXT NOT NULL,
    body        TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'active'
                CHECK (state IN ('active', 'superseded')),
    origin      TEXT NOT NULL,
    ref         TEXT,
    supersedes  INTEGER REFERENCES project_brain_records(id),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_brain_active_key
    ON project_brain_records(project, kind, key) WHERE state = 'active';
CREATE INDEX IF NOT EXISTS idx_brain_project_kind
    ON project_brain_records(project, kind, state);

-- What the selective router would have done, beside what Friday did.
--
-- Never acted on. This exists so a promotion decision can be made against
-- real speech rather than against a corpus this project wrote - there are
-- four real utterances in the whole store, and every routing number so far
-- comes from sentences the same hands invented.
--
-- Holds a one-way fingerprint and structured routing metadata. Never the
-- sentence, never its contents: a shadow log that quietly becomes a recording
-- of everything the boss says is a worse problem than the one it solves.
--
-- `comparison_status` says what the two paths did. `label_source` says where
-- any claim of correctness came from, and they are separate columns because
-- the production route is a fallible signal and not ground truth - a router
-- taught to match it would learn its mistakes.
CREATE TABLE IF NOT EXISTS shadow_predictions (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    at                     TEXT NOT NULL,
    fingerprint            TEXT NOT NULL,
    words                  INTEGER NOT NULL DEFAULT 0,
    input_source           TEXT,
    turn_id                TEXT,
    run_id                 TEXT,

    router_version         TEXT,
    taxonomy_version       TEXT,
    threshold_version      TEXT,

    request_shape          TEXT,
    predicted_operation    TEXT,
    predicted_target       TEXT,
    predicted_capability   TEXT,
    predicted_argument_shape TEXT,

    referent_available     INTEGER NOT NULL DEFAULT 0,
    referent_type          TEXT,
    referent_source        TEXT,

    decision               TEXT NOT NULL,
    abstention_reason      TEXT,
    blame                  TEXT,
    winner_score           REAL,
    runner_up_score        REAL,
    margin                 REAL,
    latency_ms             REAL,

    production_capability  TEXT,
    action_result_status   TEXT,
    comparison_status      TEXT,
    label_source           TEXT,
    label_grounding        TEXT,
    label_strength         TEXT,
    -- Two truths, not one. An ActionResult can be entirely truthful about
    -- execution and say nothing about intent: "stop it" resolved to Chrome
    -- and browser_close verified means Chrome really closed, and the route
    -- was still wrong. One column would score that a success.
    execution_correct      INTEGER,
    intent_correct         INTEGER,
    settled_at             TEXT
);
CREATE INDEX IF NOT EXISTS idx_shadow_fingerprint
    ON shadow_predictions(fingerprint, settled_at);

-- What the boss said the answer should have been. The only place real
-- language carries a correction rather than only a complaint - and stored as
-- the shape of the mistake, never the sentence.
CREATE TABLE IF NOT EXISTS routing_corrections (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    at                   TEXT NOT NULL,
    fingerprint          TEXT NOT NULL,
    previous_operation   TEXT,
    previous_target      TEXT,
    previous_capability  TEXT,
    corrected_operation  TEXT,
    corrected_target     TEXT,
    corrected_capability TEXT,
    referent_type        TEXT,
    evidence             TEXT NOT NULL
);

-- What the product must do, and how anyone would know it does.
--
-- Versioned rather than overwritten: "why did we remove multiplayer?" is a
-- question the boss will ask weeks later, and an answer requires the old row
-- to still be there with what replaced it. A requirement is superseded, never
-- deleted.
CREATE TABLE IF NOT EXISTS requirements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project        TEXT NOT NULL REFERENCES projects(name),
    statement      TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT 'FUNCTIONAL',
    rationale      TEXT,
    source         TEXT,
    acceptance     TEXT NOT NULL DEFAULT '[]',
    assumptions    TEXT NOT NULL DEFAULT '[]',
    -- PROPOSED | ACCEPTED | SUPERSEDED | REJECTED
    status         TEXT NOT NULL DEFAULT 'PROPOSED',
    needs_target   INTEGER NOT NULL DEFAULT 0,
    superseded_by  INTEGER,
    why_changed    TEXT,
    created_at     TEXT NOT NULL,
    changed_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_requirements_project
    ON requirements(project, status);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    title           TEXT,
    project         TEXT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    summary         TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    run_id          TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, id);

-- People Friday is expected to know about. Not a CRM: the fields are the ones
-- that come up in a spoken turn ("call my sister", "what's Ravi's email"), and
-- everything else goes in `notes`. `name` is the natural key because that is
-- what a request says; aliases are matched too so "mum" reaches "Sunita Rao".
CREATE TABLE IF NOT EXISTS contacts (
    name            TEXT PRIMARY KEY,
    relation        TEXT,
    phone           TEXT,
    email           TEXT,
    aliases         TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Which fabric providers actually work, per operation.
--
-- On disk rather than in memory, and that is the whole point: an in-process
-- tally is relearned from zero after every restart, and Friday restarts most
-- days. "Self-improving" that forgets overnight improves nothing. Rows are one
-- outcome each rather than a running count, so a rolling window can be applied
-- at read time and an upstream fixed last week is not judged on last month.
CREATE TABLE IF NOT EXISTS fabric_outcomes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id TEXT NOT NULL,
    operation   TEXT NOT NULL DEFAULT '',
    ok          INTEGER NOT NULL,
    at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fabric_outcomes_provider
    ON fabric_outcomes(provider_id, operation, at);

-- Phase 1H: the user model.
-- An observation is a candidate the extractor found in a turn. It carries the
-- verbatim quote it came from, so "why does Friday think this?" is always
-- answerable, and it stays on record whether accepted or rejected.
CREATE TABLE IF NOT EXISTS observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    run_id          TEXT,
    dimension       TEXT NOT NULL,
    subject         TEXT NOT NULL,
    value           TEXT NOT NULL,
    kind            TEXT NOT NULL,
    confidence      REAL NOT NULL,
    evidence        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    memory_id       INTEGER,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_subject ON observations(subject, status);
CREATE INDEX IF NOT EXISTS idx_obs_dimension ON observations(dimension, status);

-- A contradiction is never resolved silently. Both sides are recorded, and
-- resolution says which won and why - including "ask the user", which is the
-- correct answer when two stated facts disagree.
CREATE TABLE IF NOT EXISTS contradictions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    subject        TEXT NOT NULL,
    existing_value TEXT NOT NULL,
    existing_kind  TEXT NOT NULL,
    new_value      TEXT NOT NULL,
    new_kind       TEXT NOT NULL,
    observation_id INTEGER,
    resolution     TEXT NOT NULL DEFAULT 'pending',
    rationale      TEXT,
    created_at     TEXT NOT NULL,
    resolved_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_contra_resolution
    ON contradictions(resolution, created_at);

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message     TEXT NOT NULL,
    due_at      TEXT NOT NULL,
    scheduler   TEXT,
    job_id      TEXT,
    created_at  TEXT NOT NULL,
    fired       INTEGER NOT NULL DEFAULT 0
);

-- Automations: a trigger, a step graph, and every run it ever produced.
--
-- The definition is stored as data rather than as code so a new automation is
-- a row, not a deploy. `steps` is a JSON list where each step names a
-- capability from the engine's allow-list and may declare `needs`, which is
-- what makes this a graph rather than a list - a step whose dependency failed
-- is skipped and recorded as skipped, not run into the same failure.
--
-- `task_name` is the Windows scheduled task that fires it, or NULL for a
-- manual automation. Unlike the donor design this field is load-bearing: a
-- trigger that nothing dispatches on is decoration, so "manual" here means
-- no task was registered, and that is verifiable against schtasks.
CREATE TABLE IF NOT EXISTS automations (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    trigger     TEXT NOT NULL,
    steps       TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    task_name   TEXT,
    created_at  TEXT NOT NULL
);

-- One row per execution, whether it was fired by the scheduler or by hand.
--
-- `steps` holds the per-step outcome as JSON: status, attempts, evidence and
-- error for each. Without it "the automation failed" is a claim nobody can
-- check the morning after, which is the failure mode that makes background
-- work untrustworthy.
-- `runtime` holds where this process resolved everything: cwd, project root,
-- data dir, database, logs, and the scheduled task that fired it. It is here
-- because the defect it makes visible does not raise. A run whose cwd is
-- System32 and whose database sits under the project root is the fixed
-- version; the same cwd with a database beside it is the broken one, and
-- without this the difference has to be re-derived from scratch every time.
CREATE TABLE IF NOT EXISTS automation_runs (
    run_id      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    fired_by    TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    steps       TEXT NOT NULL DEFAULT '[]',
    error       TEXT,
    runtime     TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_automation_runs_name
    ON automation_runs(name, started_at DESC);

-- One row per scheduled execution key (invariant A-048): the primary key
-- is the whole at-most-once guarantee across crash, restart and re-fire.
CREATE TABLE IF NOT EXISTS automation_executions (
    execution_key TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    claimed_at    TEXT NOT NULL
);

-- PRD v3.1 FR-041 / FR-042: scheduled OBJECTIVES (one-time or recurring)
-- with budgets, permissions, delivery channel and an optional condition,
-- and one row per firing so "did it run last night" is a lookup.
CREATE TABLE IF NOT EXISTS schedules (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    objective   TEXT NOT NULL,
    tasks       TEXT NOT NULL DEFAULT '[]',
    trigger     TEXT NOT NULL,
    budgets     TEXT NOT NULL DEFAULT '{}',
    permissions TEXT NOT NULL DEFAULT '[]',
    delivery    TEXT NOT NULL DEFAULT 'session',
    condition   TEXT NOT NULL DEFAULT '{"kind": "always"}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    task_name   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule_runs (
    firing_id        TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    fired_by         TEXT NOT NULL,
    run_id           TEXT,
    status           TEXT NOT NULL,
    condition_met    INTEGER,
    condition_detail TEXT,
    delivered_via    TEXT NOT NULL DEFAULT '',
    started_at       TEXT NOT NULL,
    finished_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_name
    ON schedule_runs(name, started_at DESC);

-- Product processing runs, and one row per record.
--
-- Records are written as they finish rather than at the end, which is what
-- makes a batch resumable: a run that dies at record 47 of 400 keeps its 47
-- and its run_id, instead of starting again and producing them twice.
--
-- Everything here is scoped by run_id, and that is a correctness requirement
-- rather than tidiness. A verifier asking "does an export exist" will find
-- the *previous* run's and call this one successful; it has to ask whether
-- this run produced one.
CREATE TABLE IF NOT EXISTS product_runs (
    run_id          TEXT PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    total_records   INTEGER NOT NULL DEFAULT 0,
    stages          TEXT NOT NULL DEFAULT '[]',
    summary         TEXT NOT NULL DEFAULT '{}',
    started_at      TEXT NOT NULL,
    finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS product_records (
    run_id          TEXT NOT NULL,
    product_key     TEXT NOT NULL,
    status          TEXT NOT NULL,
    input_hash      TEXT NOT NULL DEFAULT '',
    output_hash     TEXT NOT NULL DEFAULT '',
    quarantine_reason TEXT NOT NULL DEFAULT '',
    collapsed       INTEGER NOT NULL DEFAULT 0,
    source_row      TEXT NOT NULL DEFAULT '{}',
    fields          TEXT NOT NULL DEFAULT '{}',
    stages          TEXT NOT NULL DEFAULT '{}',
    at              TEXT NOT NULL,
    PRIMARY KEY (run_id, product_key)
);

CREATE INDEX IF NOT EXISTS idx_product_records_run
    ON product_records(run_id, status);

-- Skills Friday wrote for itself.
--
-- `state` is the lifecycle, and the reason it has four steps rather than a
-- boolean: passing its tests makes a skill VERIFIED, which is a statement
-- about its behaviour. It is not a decision to run it, and it is certainly
-- not a decision to run it everywhere. REGISTERED means it is installed and
-- addressable; ENABLED, with `scopes`, means something may actually call it.
-- The thing that wrote the code never takes the last step.
--
-- `source_sha256` is what was verified. If the file on disk stops matching
-- it, the verification refers to code that is no longer there, and the skill
-- is not the skill that was checked.
CREATE TABLE IF NOT EXISTS forged_skills (
    name            TEXT PRIMARY KEY,
    state           TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    spec            TEXT NOT NULL,
    source_sha256   TEXT NOT NULL,
    source_path     TEXT NOT NULL,
    risk            TEXT NOT NULL,
    provenance      TEXT NOT NULL,
    verification    TEXT NOT NULL DEFAULT '{}',
    verified_at     TEXT,
    scopes          TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Every state change, with who asked and why. A skill that quietly became
-- ENABLED is the failure this table exists to make impossible.
CREATE TABLE IF NOT EXISTS forged_skill_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    actor       TEXT NOT NULL,
    at          TEXT NOT NULL
);

-- Development work handed to an executor.
--
-- This table is the reason a coding session can be thrown away. The Claude
-- transcript is an accelerator; the task bundle, the decisions and the
-- progress recorded here are the source of truth, and they are enough to
-- start a fresh executor if the session is gone.
--
-- `task_bundle` is stored whole, as JSON, on purpose: a resume that needs to
-- reconstruct the goal from a summary has already lost the thing it needed.
CREATE TABLE IF NOT EXISTS executor_runs (
    run_id              TEXT PRIMARY KEY,
    executor_type       TEXT NOT NULL,
    session_id          TEXT,
    project             TEXT,
    working_directory   TEXT NOT NULL,
    worktree_path       TEXT,
    pid                 INTEGER,
    status              TEXT NOT NULL,
    task_bundle         TEXT NOT NULL,
    last_event          TEXT,
    summary             TEXT,
    completion_evidence TEXT,
    exit_code           INTEGER,
    resume_count        INTEGER NOT NULL DEFAULT 0,
    started_at          TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    ended_at            TEXT
);
CREATE INDEX IF NOT EXISTS idx_executor_status
    ON executor_runs(status, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_executor_project
    ON executor_runs(project, last_seen_at);

-- What was promoted out of a worktree, and how to undo it.
--
-- Written before the merge and updated after, so a promotion interrupted
-- halfway is visible as PROMOTING rather than as nothing having happened.
-- rollback_target is recorded rather than worked out later: reconstructing
-- "where were we before" from history after a bad deploy is exactly the
-- moment you cannot afford to get it wrong.
CREATE TABLE IF NOT EXISTS promotions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    state           TEXT NOT NULL,
    worktree        TEXT NOT NULL,
    branch          TEXT NOT NULL,
    target_branch   TEXT,
    base_commit     TEXT,
    result_commit   TEXT,
    merge_commit    TEXT,
    rollback_target TEXT,
    reason          TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_promotions_run ON promotions(run_id, id);

-- Continuous-execution runs: one multi-step objective, driven to terminal
-- without the user saying "Continue".
--
-- The row is written BEFORE any task executes, because a run whose writer
-- died mid-startup must still leave a recoverable row - a row nobody wrote
-- is a run nobody can resume. `lease_*` is the single-writer guard: exactly
-- one executor owns the run at a time, and a stale lease (expired, or held
-- by an executor that no longer answers) is how an orphan is detected and
-- reacquired. `next_wake` is when a continuation is scheduled; every
-- non-terminal run must have an active lease, a scheduled wake, or a
-- legitimate WAITING_* state - the invariant
-- NON_TERMINAL_RUN_HAS_FUTURE, checked by the engine after every mutation.
-- `manual_continue_count` is the P0 gate metric: it must stay 0.
CREATE TABLE IF NOT EXISTS objective_runs (
    run_id              TEXT PRIMARY KEY,
    request             TEXT NOT NULL,
    objective_summary   TEXT NOT NULL,
    status              TEXT NOT NULL,
    lease_executor_id   TEXT,
    lease_generation    INTEGER NOT NULL DEFAULT 0,
    lease_expiry        TEXT,
    next_wake           TEXT,
    manual_continue_count INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    finished_at         TEXT,
    summary             TEXT
);

-- One row per compiled task, written at compile time (status QUEUED), so a
-- crash mid-graph never loses the plan. `dependencies` is what makes the
-- tasks a graph: a task whose dependency did not succeed is skipped with
-- `blocked_by` naming the task that stopped it, never run into the same
-- failure. `arguments` may embed {{tasks.<id>.<key>}} references to an
-- earlier task's result. `failure_kind` classifies the terminal failure so a
-- capability-missing task is never re-called and a transient one gets its
-- bounded retries.
CREATE TABLE IF NOT EXISTS objective_tasks (
    task_id         TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    status          TEXT NOT NULL,
    capability      TEXT NOT NULL,
    arguments       TEXT NOT NULL,
    dependencies    TEXT NOT NULL DEFAULT '[]',
    parent_id       TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    failure_kind    TEXT,
    result          TEXT,
    evidence        TEXT,
    blocked_by      TEXT,
    next_wake       TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_objective_tasks_run
    ON objective_tasks(run_id, status);

-- ASSUME-001: what Friday decided for the person while compiling this
-- objective, with how sure it was, what it costs if wrong, how it would be
-- confirmed and whether it has been. `key` is the statement's stable identity,
-- so recording the same assumption again updates the row rather than
-- duplicating it.
CREATE TABLE IF NOT EXISTS assumption_register (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    key         TEXT NOT NULL,
    statement   TEXT NOT NULL,
    origin      TEXT NOT NULL,
    confidence  TEXT NOT NULL,
    consequence TEXT NOT NULL,
    validation  TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'UNVALIDATED',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (run_id, key)
);

-- The continuation trace: every state transition, in order, with the
-- payload that explains it. Without it "the run completed" is a claim
-- nobody can check the morning after.
CREATE TABLE IF NOT EXISTS objective_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    task_id     TEXT,
    event       TEXT NOT NULL,
    detail      TEXT,
    at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_objective_events_run
    ON objective_events(run_id, id);

-- Exactly-once user delivery for a terminal ObjectiveRun. The engine writes
-- this at the terminal transition; the live agent claims atomically and
-- marks delivered. UNIQUE(run_id) makes duplicate finish observations safe.
CREATE TABLE IF NOT EXISTS objective_deliveries (
    delivery_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL UNIQUE,
    message       TEXT NOT NULL,
    delivery_state TEXT NOT NULL DEFAULT 'PENDING',
    created_at    TEXT NOT NULL,
    delivered_at TEXT,
    delivered_via TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_objective_deliveries_state
    ON objective_deliveries(delivery_state, delivery_id);

-- A power action Friday asked for and could not stay to watch.
--
-- Written BEFORE the request goes out, because after may never arrive: the
-- machine suspends, the process ends mid-sentence, and a run that was going
-- to record its own outcome never gets to. This row is the only thing that
-- survives that, and reconcile() settles it when Friday comes back.
--
-- boot_id is the evidence. It is the machine's boot time as it stood when the
-- request was made, so a later run can compare it with the current one and
-- know whether a restart actually happened - without taking the earlier run's
-- word for anything, which it is in no position to give.
CREATE TABLE IF NOT EXISTS pending_power (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    action          TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    boot_id         TEXT NOT NULL,
    requested_at    TEXT NOT NULL,
    deadline_at     TEXT NOT NULL,
    settled_at      TEXT,
    settled_by      TEXT,
    detail          TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_power_outcome
    ON pending_power(outcome, requested_at);

-- The tenant registry (FR-PRV-002, invariant 6). One install can hold a
-- user, a company, several brands, or an agency's clients, and private state
-- must not cross between them.
--
-- `active` marks the workspace a process adopts when nothing else says
-- otherwise; exactly one row carries 1, enforced by set_active_workspace
-- rather than by a constraint, because switching is two writes and a partial
-- unique index would reject the intermediate state.
--
-- Every install has a `default` row: it is where every memory written before
-- this table existed belongs. Backfilling those rows to an invented owner
-- would be a lie, and leaving them unowned would make them readable from
-- every workspace, which is the leak this table exists to prevent.
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'user',
    created_at  TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 0,
    detail      TEXT NOT NULL DEFAULT '{}'
);

-- PRD v7 (V7-D08): the confirmations every FRIDAY process shares, so the
-- person's yes - given where the person is - reaches the process that asked.
-- `asker` is that process instance: only it may spend the yes, so a restart
-- voids everything asked before it. Only the fingerprint of the action is
-- kept; the arguments, which can carry what was typed into a form, are not.
-- PRD v7 STOP-001: the emergency stop, one row, in the store every FRIDAY
-- process shares. While it is latched nothing new starts anywhere; it stays
-- latched across restarts until the person resumes.
CREATE TABLE IF NOT EXISTS stop_latch (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    reason      TEXT NOT NULL,
    actor       TEXT NOT NULL,
    latched_at  TEXT NOT NULL
);

-- PRD v7 NET-001: the network grant behind each objective's scope. Issued at
-- compile, revoked by the person or by STOP, read by netguard in whichever
-- process acts. A grant this table has never seen is treated as revoked.
CREATE TABLE IF NOT EXISTS network_grants (
    grant_id       TEXT PRIMARY KEY,
    objective_id   TEXT NOT NULL,
    mode           TEXT NOT NULL,
    hosts          TEXT NOT NULL DEFAULT '[]',
    expires_at     TEXT NOT NULL DEFAULT '',
    issued_at      TEXT NOT NULL,
    revoked_at     TEXT,
    revoked_reason TEXT
);

CREATE TABLE IF NOT EXISTS confirmations (
    nonce        TEXT PRIMARY KEY,
    asker        TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    action       TEXT NOT NULL,
    target       TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    question     TEXT NOT NULL,
    state        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    answered_by  TEXT,
    answered_at  TEXT,
    spent_at     TEXT
);
-- PRD v7 DEVICE-003: a device's persistent identity and where it is in the
-- pairing and trust lifecycle. Only the hash of the pairing code is kept.
CREATE TABLE IF NOT EXISTS devices (
    device_id        TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    state            TEXT NOT NULL,
    code_hash        TEXT NOT NULL,
    code_expires_at  TEXT NOT NULL,
    inventory        TEXT,
    policy           TEXT,
    created_at       TEXT NOT NULL,
    approved_by      TEXT,
    approved_at      TEXT,
    activated_at     TEXT
);
-- PRD v7 ULTRA-002: a stage result keyed by what produced it. Private to the
-- workspace that ran the stage, so the column is part of the key.
CREATE TABLE IF NOT EXISTS ultracode_results (
    workspace_id  TEXT NOT NULL DEFAULT 'default',
    result_key    TEXT NOT NULL,
    result        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (workspace_id, result_key)
);

-- PRD v7 PRO-001: the Opportunities Inbox. A deterministic detector over rows
-- the engine already writes raises one row per distinct fact; the unique key
-- is what makes re-scanning the same facts free. `evidence_key` names the
-- fact (e.g. the first run of a failure streak), so a dismissed row stays
-- dismissed until something new happens.
CREATE TABLE IF NOT EXISTS opportunities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id  TEXT NOT NULL DEFAULT 'default',
    kind          TEXT NOT NULL,
    subject       TEXT NOT NULL,
    evidence_key  TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'open',
    created_at    TEXT NOT NULL,
    resolved_at   TEXT,
    UNIQUE (workspace_id, kind, subject, evidence_key)
);

-- PRD v7 EVO-001: the Evolution Controller's proposals. `dedupe_key` is what
-- makes a repeated trigger the same proposal; `body` carries benefit, risk,
-- cost, components, acceptance tests, budget and termination condition.
CREATE TABLE IF NOT EXISTS evolution_proposals (
    proposal_id   TEXT PRIMARY KEY,
    dedupe_key    TEXT NOT NULL UNIQUE,
    trigger_kind  TEXT NOT NULL,
    subject       TEXT NOT NULL,
    klass         TEXT NOT NULL,
    zone          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    occurrences   INTEGER NOT NULL DEFAULT 1,
    body          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    run_id        TEXT NOT NULL DEFAULT '',
    lease         TEXT NOT NULL DEFAULT ''
);
"""


class Store:
    def __init__(self, path: Path | str | None = None) -> None:
        # No path means the configured database (ADA_DB), else the default.
        # A bare `Store()` used to mean the live data/ada.sqlite3 whatever
        # ADA_DB said, so every caller that made one reached the owner's data
        # past the tests' own database (V7-D17).
        self.path = Path(path if path is not None
                         else (os.getenv("ADA_DB") or DEFAULT_DB))
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(str(self.path), check_same_thread=False,
                              timeout=BUSY_TIMEOUT_S)
        raw.row_factory = sqlite3.Row
        # Every statement on the shared connection runs under this lock
        # (see _SerializedConnection); RLock so _tx nests without
        # self-deadlock. Created before the wrapper that needs it.
        self._lock = threading.RLock()
        self._conn = _SerializedConnection(raw, self._lock)
        # Concurrency contract (audit A-038). Several processes open this
        # file at once - the voice agent, the MCP server, the UI server,
        # schedule firings, the objective CLI - so the durability settings
        # are set here, once, where the connection is made:
        #   WAL        readers never block the writer and vice versa; a
        #              crash mid-write leaves the main file consistent
        #              and the WAL is replayed or discarded on next open.
        #   busy_timeout (the `timeout=` above, plus the pragma for
        #              statements SQLite issues itself) waits out another
        #              process's write instead of failing "database is
        #              locked" on the first contention.
        #   synchronous=NORMAL is WAL's durable-enough default: the WAL is
        #              fsynced at checkpoint, and a power cut can lose only
        #              the last transactions, never corrupt the file.
        # In-memory databases have no WAL; the pragmas are no-ops there.
        if str(self.path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_S * 1000)}")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # One connection is shared across threads (check_same_thread=False),
        # and the continuity engine reserves narrations and attempts from a
        # thread pool. Without serialization, two threads' transactions
        # interleave on the one connection and the second commit fails with
        # "cannot commit - no transaction is active". _tx holds this lock for
        # the whole transaction so a commit only ever ends the transaction it
        # opened. RLock so a future nested _tx does not self-deadlock.
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    #: Columns added after the first databases were already in use. CREATE
    #: TABLE IF NOT EXISTS does nothing to a table that exists, so these are
    #: added separately - the alternative is a fresh database, and losing what
    #: Friday has learned to gain a column is not a trade worth making.
    _ADDED_COLUMNS = (
        ("evolution_proposals", "run_id", "TEXT NOT NULL DEFAULT ''"),
        ("evolution_proposals", "lease", "TEXT NOT NULL DEFAULT ''"),
        # PRD v7 SEC-005 / TRUST-002: where an objective came from, whether a
        # person is there to answer, and the envelope it runs inside. NULL on
        # rows written before envelopes existed; such a run waits for the
        # person instead of running under an assumed scope.
        ("objective_runs", "provenance", "TEXT"),
        ("objective_runs", "attended", "INTEGER"),
        ("objective_runs", "envelope", "TEXT"),
        # PRD v7 INTENT-001: the objective contract the intent compiler read
        # from the request. NULL on runs compiled before it, or from explicit
        # task lists with no request to read.
        ("objective_runs", "contract", "TEXT"),
        # Who wrote a schedule: its firings carry this. NULL on schedules
        # written before it existed, which then fire as untrusted.
        ("schedules", "provenance", "TEXT"),
        # How many separate times this has been observed. Confidence alone
        # cannot tell "said once, emphatically" from "said five times".
        ("memories", "evidence_count", "INTEGER NOT NULL DEFAULT 1"),
        # When it was last seen to still be true, as opposed to first learned.
        ("memories", "last_confirmed", "TEXT"),
        # The observation this came from: every belief traces to a raw episode.
        ("memories", "observation_id", "INTEGER"),
        # When the executor process started. A pid on its own is not an
        # identity - it is reused, and this machine runs enough node processes
        # that a recycled number reads as "still working" if you only check
        # the name. Creation time is what makes the pid mean something.
        ("executor_runs", "pid_started_at", "REAL"),
        # Where the process that ran this automation resolved its paths. A
        # background run that writes to the wrong database does not raise, so
        # the only way to see it is to record what it decided.
        ("automation_runs", "runtime", "TEXT NOT NULL DEFAULT '{}'"),
        # How many identical rows a surviving record stood in for. Counted
        # apart from processing, so "processed N products" stays true.
        ("product_records", "collapsed", "INTEGER NOT NULL DEFAULT 0"),
        # The row as it arrived. Without it a retry has nothing to re-run
        # and would reprocess an empty record, quarantining a healthy one.
        ("product_records", "source_row", "TEXT NOT NULL DEFAULT '{}'"),
        ("objective_tasks", "parent_id", "TEXT"),
        # Fingerprint/strategy state for the failure-loop guard: last
        # fingerprint, its history, the stated hypothesis and how many
        # times the strategy has changed for this task. JSON, additive.
        ("objective_tasks", "detail", "TEXT NOT NULL DEFAULT '{}'"),
        ("open_questions", "blocking", "INTEGER NOT NULL DEFAULT 1"),
        ("open_questions", "impact", "TEXT"),
        ("open_questions", "assumption", "TEXT"),
        ("open_questions", "assumption_reason", "TEXT"),
        ("open_questions", "status", "TEXT NOT NULL DEFAULT 'OPEN'"),
        # continuity.start_run INSERTs attended and provenance into `runs`, but
        # the base CREATE TABLE and this list both omitted them. The live
        # database has them only because it was migrated across an older schema
        # that did; on a *fresh* database objective admission failed with
        #   sqlite3.OperationalError: table runs has no column named attended
        # i.e. the whole autonomous engine was broken on a clean install. Found
        # by scripts/golden_continuous_run.py on a temp DB, 2026-08-29. Types
        # match the live columns (INTEGER/TEXT, NOT NULL, defaults 1/'PERSON').
        # The `if column not in existing` guard makes this a no-op where they
        # already exist, so the live DB is untouched.
        ("runs", "attended", "INTEGER NOT NULL DEFAULT 1"),
        ("runs", "provenance", "TEXT NOT NULL DEFAULT 'PERSON'"),
        # PRD v3.1 FR-001 objective schema (9.2): the durable objective
        # carries its class, risk tier, budgets, constraints, approvals and
        # evidence as first-class columns, not as prose in the summary.
        ("objective_runs", "task_class", "TEXT NOT NULL DEFAULT ''"),
        ("objective_runs", "risk_tier", "TEXT NOT NULL DEFAULT ''"),
        ("objective_runs", "owner_id", "TEXT NOT NULL DEFAULT 'owner'"),
        ("objective_runs", "project_scope", "TEXT NOT NULL DEFAULT ''"),
        ("objective_runs", "memory_scope", "TEXT NOT NULL DEFAULT 'user'"),
        ("objective_runs", "constraints", "TEXT NOT NULL DEFAULT '[]'"),
        ("objective_runs", "required_capabilities", "TEXT NOT NULL DEFAULT '[]'"),
        ("objective_runs", "retry_budget", "INTEGER NOT NULL DEFAULT 3"),
        ("objective_runs", "cost_budget_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("objective_runs", "time_budget_s", "INTEGER NOT NULL DEFAULT 0"),
        ("objective_runs", "approvals", "TEXT NOT NULL DEFAULT '[]'"),
        ("objective_runs", "evidence", "TEXT NOT NULL DEFAULT '[]'"),
        ("objective_runs", "blocker", "TEXT NOT NULL DEFAULT ''"),
        ("objective_runs", "source_channel", "TEXT NOT NULL DEFAULT 'local'"),
        # PRD v3.1 FR-016/017/018 memory record contract (9.5): type,
        # project scope, source reference, supersession/contradiction links,
        # retention and last retrieval. Additive; existing rows read as
        # SEMANTIC/'' which is what they were.
        ("memories", "memory_type", "TEXT NOT NULL DEFAULT 'semantic'"),
        ("memories", "project_scope", "TEXT NOT NULL DEFAULT ''"),
        ("memories", "source_ref", "TEXT NOT NULL DEFAULT ''"),
        ("memories", "supersedes_id", "INTEGER"),
        ("memories", "contradicts_id", "INTEGER"),
        ("memories", "retention_policy", "TEXT NOT NULL DEFAULT 'durable'"),
        ("memories", "last_retrieved_at", "TEXT"),
        ("memories", "importance", "REAL NOT NULL DEFAULT 0.5"),
        # PRD v7 MEM-002: where a memory came from - the person (PERSON) or
        # something read (READ_MATERIAL). '' is a row written before this, or
        # by a path that does not classify; read material may not overrule it.
        ("memories", "trust_class", "TEXT NOT NULL DEFAULT ''"),
        # FR-PRV-002 / invariant 6: the tenant boundary. Every private-state
        # table named in workspace.SCOPED carries the owning workspace, and
        # the default is `default` - the one workspace a single-tenant
        # install always was, so existing rows keep being readable by the
        # install that wrote them and by nobody else.
        #
        # The column is added to every scoped table, but only the tables
        # whose reads are filtered below (memories, observations) are
        # *enforced* today; the rest are stamped so their owner is
        # attributable and their reads can be filtered without a second
        # migration. tests/test_workspace_isolation.py asserts which is
        # which, so the difference cannot quietly become a claim.
        *(
            (table, "workspace_id", "TEXT NOT NULL DEFAULT 'default'")
            for table in (
                "memories", "observations", "utterances", "conversations",
                "messages", "contacts", "projects", "project_decisions",
                "project_brain_records",
                "requirements", "open_questions", "objective_runs",
                "forged_skills", "product_records", "product_runs",
                "automations", "schedules", "reminders",
            )
        ),
    )

    #: Sources the read-material producers wrote before `trust_class` existed
    #: (a worker's handoff, a skill's facts, a worker's bridge write, a channel
    #: resolved from a page). All are set by code, never by the text itself.
    _READ_SOURCES = ("handoff:%", "skill:%", "worker:%", "resolved from %")

    def _migrate(self) -> None:
        self._retire_superseded_shadow_table()
        for table, column, spec in self._ADDED_COLUMNS:
            existing = {row["name"] for row in
                        self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
                if (table, column) == ("memories", "trust_class"):
                    # Once, as the column arrives: what those producers wrote
                    # before is read material too (Slice 3 review).
                    self._conn.execute(
                        "UPDATE memories SET trust_class=? WHERE trust_class='' AND ("
                        + " OR ".join("source LIKE ?" for _ in self._READ_SOURCES) + ")",
                        (READ_MATERIAL, *self._READ_SOURCES))
        # Rows written before last_confirmed existed were confirmed when they
        # were created; leaving them NULL would read as "never confirmed".
        self._conn.execute(
            "UPDATE memories SET last_confirmed=created_at WHERE last_confirmed IS NULL")

    _SUPERSEDED_SHADOW = {"actual", "verdict", "predicted", "label_confidence",
                          "verified_outcome", "abstained"}

    def _retire_superseded_shadow_table(self) -> None:
        """
        Move an out-of-date shadow table aside and build the current one.

        `CREATE TABLE IF NOT EXISTS` does nothing to a table that exists, so a
        database that met an earlier version of this schema kept it - and the
        first live turn after a rewrite failed with

            sqlite3.OperationalError: table shadow_predictions has no column
            named request_shape

        `_ADDED_COLUMNS` cannot repair this one: the current table has NOT
        NULL columns without defaults, which SQLite refuses to add to an
        existing table at all.

        The first attempt dropped the table when it was empty and *raised*
        when it was not. That was worse than the bug it fixed - it bricked
        every `Store()` construction in the process, including the test suite,
        because three rows of telemetry existed. A migration that halts the
        application to protect three rows has its priorities backwards.

        So the old table is renamed rather than dropped or defended. Nothing
        is lost, nothing is blocked, and the rows are sitting in
        `shadow_predictions_superseded` for anyone who wants them.
        """
        try:
            columns = {row["name"] for row in
                       self._conn.execute("PRAGMA table_info(shadow_predictions)")}
        except sqlite3.Error:
            return
        if not columns or not (self._SUPERSEDED_SHADOW & columns):
            return

        rows = self._conn.execute(
            "SELECT COUNT(*) FROM shadow_predictions").fetchone()[0]
        if not rows:
            self._conn.execute("DROP TABLE shadow_predictions")
        else:
            aside = "shadow_predictions_superseded"
            index = 1
            while self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE name=?",
                    (aside,)).fetchone():
                index += 1
                aside = f"shadow_predictions_superseded_{index}"
            self._conn.execute(
                f"ALTER TABLE shadow_predictions RENAME TO {aside}")
            logging.getLogger("friday-agent.store").warning(
                "shadow_predictions had a superseded schema with %d rows; moved to %s and rebuilt",
                rows, aside)
        self._conn.execute(
            "DROP INDEX IF EXISTS idx_shadow_fingerprint")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # -- workspaces (FR-PRV-002) --------------------------------------------

    def register_workspace(self, workspace_id: str, *, label: str = "",
                           kind: str = "user", detail: dict | None = None,
                           make_active: bool = False) -> str:
        """Register a tenant. Idempotent: registering an existing id updates
        its label/kind rather than raising, so onboarding can be re-run.

        The id is validated by `workspace.valid_id` because it becomes a
        directory name for secrets, skills and browser profiles - an id that
        cannot be a safe path component is not an id.
        """
        from friday import workspace as W
        W.valid_id(workspace_id)
        if kind not in W.KINDS:
            raise ValueError(f"unknown workspace kind {kind!r}; known: {list(W.KINDS)}")
        payload = json.dumps(detail or {})
        with self._tx() as conn:
            existing = conn.execute("SELECT id FROM workspaces WHERE id=?",
                                    (workspace_id,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE workspaces SET label=?, kind=?, detail=? WHERE id=?",
                    (label, kind, payload, workspace_id))
            else:
                conn.execute(
                    "INSERT INTO workspaces (id, label, kind, created_at, active, detail) "
                    "VALUES (?,?,?,?,0,?)",
                    (workspace_id, label, kind, now_iso(), payload))
        if make_active:
            self.set_active_workspace(workspace_id)
        return workspace_id

    def workspaces(self) -> list[dict]:
        """Every registered tenant, oldest first. `default` is included even
        on an install that never registered anything - see
        `ensure_default_workspace`."""
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM workspaces ORDER BY created_at, id")]

    def ensure_default_workspace(self) -> str:
        """Make sure the workspace that owns pre-boundary rows is registered
        and active. Called by first run and by anything that needs the
        registry to be non-empty; safe to call repeatedly."""
        from friday.workspace import DEFAULT_ID
        rows = self._conn.execute("SELECT id, active FROM workspaces").fetchall()
        if not any(r["id"] == DEFAULT_ID for r in rows):
            self.register_workspace(DEFAULT_ID, label="This computer",
                                    kind="user")
        if not any(r["active"] for r in rows):
            self.set_active_workspace(DEFAULT_ID)
        return DEFAULT_ID

    def active_workspace(self) -> str | None:
        """The workspace a process adopts when nothing else says otherwise,
        or None when the registry is empty. None rather than `default` so
        `workspace.current()` can tell "not set up yet" from "set to the
        default", which are different facts."""
        row = self._conn.execute(
            "SELECT id FROM workspaces WHERE active=1 LIMIT 1").fetchone()
        return str(row["id"]) if row else None

    def set_active_workspace(self, workspace_id: str) -> str:
        """Switch the active workspace. Refuses an unregistered id: adopting
        a workspace that does not exist would read private state under a key
        nothing owns, and an empty result reads as "nothing here" when the
        truth is "wrong key"."""
        from friday.workspace import UnknownWorkspace, valid_id
        valid_id(workspace_id)
        with self._tx() as conn:
            known = conn.execute("SELECT id FROM workspaces WHERE id=?",
                                 (workspace_id,)).fetchone()
            if not known:
                raise UnknownWorkspace(
                    f"workspace {workspace_id!r} is not registered; "
                    "register_workspace() first")
            conn.execute("UPDATE workspaces SET active=0 WHERE active=1")
            conn.execute("UPDATE workspaces SET active=1 WHERE id=?",
                         (workspace_id,))
        return workspace_id

    @staticmethod
    def _workspace(workspace_id: str | None) -> str:
        """The workspace a private-state call runs under: what it was given,
        or the process's current one. One place, so no call site invents its
        own default."""
        from friday.workspace import resolve
        return resolve(workspace_id)

    # -- runs ---------------------------------------------------------------

    def save_run(self, run: Run) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, request, state, capability, created_at, updated_at, error) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET state=excluded.state, "
                "capability=excluded.capability, updated_at=excluded.updated_at, "
                "error=excluded.error",
                (run.run_id, run.request, run.state, run.capability,
                 run.created_at, run.updated_at, run.error),
            )
            for result in run.results:
                self._insert_result(conn, result)
                for artifact in result.artifacts:
                    self._insert_artifact(conn, artifact)

    @staticmethod
    def _insert_result(conn, result: ActionResult) -> None:
        v = result.verification
        conn.execute(
            "INSERT INTO tool_results (run_id, tool_id, status, started_at, completed_at, "
            "output, error, side_effects, verify_method, verify_evidence, verify_checked_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (result.run_id, result.tool_id, result.status, result.started_at,
             result.completed_at, json.dumps(result.output, default=str),
             result.error, json.dumps(list(result.side_effects)),
             v.method if v else None, v.evidence if v else None,
             v.checked_at if v else None),
        )

    @staticmethod
    def _insert_artifact(conn, artifact: Artifact) -> None:
        v = artifact.verification
        conn.execute(
            "INSERT OR REPLACE INTO artifacts (artifact_id, run_id, type, title, "
            "path_or_uri, producer, created_at, metadata, verify_method, "
            "verify_evidence, verify_checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (artifact.artifact_id, artifact.run_id, artifact.type, artifact.title,
             artifact.path_or_uri, artifact.producer, artifact.created_at,
             json.dumps(artifact.metadata), v.method, v.evidence, v.checked_at),
        )

    def load_run(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        run = dict(row)
        run["results"] = [dict(r) for r in self._conn.execute(
            "SELECT * FROM tool_results WHERE run_id=? ORDER BY id", (run_id,)
        )]
        run["artifacts"] = [dict(r) for r in self._conn.execute(
            "SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at", (run_id,)
        )]
        return run

    def recent_runs(self, limit: int = 10) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
        )]

    # -- artifacts ----------------------------------------------------------

    def artifacts_for(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM artifacts WHERE run_id=?", (run_id,)
        )]

    # -- memory -------------------------------------------------------------

    def remember(
        self, subject: str, value: str, *, kind: str, source: str,
        scope: str = "user", confidence: float = 1.0, run_id: str | None = None,
        supersede: bool = True, evidence_count: int = 1,
        observation_id: int | None = None,
        memory_type: str = "semantic", project_scope: str = "",
        source_ref: str = "", importance: float = 0.5,
        retention_policy: str = "", workspace_id: str | None = None,
        trust_class: str = "",
    ) -> int:
        """
        Store a memory. `kind` and `source` are required - an unattributed
        memory is not storable, which is what stops inference drifting into
        fact.

        `evidence_count` is how many separate times this has been observed and
        `observation_id` is the episode it came from. Together they answer the
        two questions confidence alone cannot: how often, and from where.

        PRD 9.5 fields: `memory_type` (FR-016 class, sets the lifecycle),
        `project_scope` (FR-017: '' = every project, else only that one),
        `source_ref` (FR-018: the run/message/file the fact came from). When
        this record supersedes an active one on the same subject+kind, the
        old row is marked superseded AND the new row carries `supersedes_id`
        pointing at it - the link the UI needs to explain "this replaced
        that" rather than just "that is gone".

        `workspace_id` (FR-PRV-002) defaults to the process's current
        workspace. Supersession is scoped to it as well: a write in one
        workspace must not retire another workspace's belief about the same
        subject, which is the subtle half of the boundary - the read filter
        stops A *seeing* B, and this stops A *overwriting* B.

        `trust_class` (PRD v7 MEM-002) is where it came from: the person
        (`contracts.PERSON`) or something read (`contracts.READ_MATERIAL`);
        '' for a path that does not classify. `memory_promotion` sets it.
        """
        if trust_class not in ("", *TRUST_CLASSES):
            raise ValueError(f"unknown trust class {trust_class!r}")
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind {kind!r}; known: {list(MEMORY_KINDS)}")
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"unknown memory type {memory_type!r}; known: {list(MEMORY_TYPES)}")
        if not source.strip():
            raise ValueError("memory requires a source")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence must be 0..1, got {confidence}")
        if evidence_count < 1:
            raise ValueError(f"evidence_count must be at least 1, got {evidence_count}")
        retention = retention_policy or MEMORY_RETENTION[memory_type]
        owner = self._workspace(workspace_id)

        stamp = now_iso()
        with self._tx() as conn:
            superseded_id: int | None = None
            if supersede:
                prior = conn.execute(
                    "SELECT id FROM memories WHERE subject=? AND kind=? AND superseded=0 "
                    "AND project_scope=? AND workspace_id=? ORDER BY id DESC LIMIT 1",
                    (subject, kind, project_scope, owner)).fetchone()
                superseded_id = int(prior[0]) if prior else None
                # Older rows are marked, never deleted - history stays auditable.
                conn.execute(
                    "UPDATE memories SET superseded=1 WHERE subject=? AND kind=? "
                    "AND superseded=0 AND project_scope=? AND workspace_id=?",
                    (subject, kind, project_scope, owner),
                )
            cur = conn.execute(
                "INSERT INTO memories (subject, value, kind, scope, source, confidence, "
                "run_id, created_at, evidence_count, last_confirmed, observation_id, "
                "memory_type, project_scope, source_ref, supersedes_id, "
                "retention_policy, importance, workspace_id, trust_class) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (subject, value, kind, scope, source, confidence, run_id, stamp,
                 evidence_count, stamp, observation_id, memory_type, project_scope,
                 source_ref, superseded_id, retention, importance, owner,
                 trust_class),
            )
            return int(cur.lastrowid)

    def recall_scoped(self, subject: str = "", *, project_scope: str = "",
                      needle: str = "", memory_types: tuple[str, ...] = (),
                      limit: int = 20, touch: bool = True,
                      workspace_id: str | None = None) -> list[dict]:
        """FR-017 scoped retrieval. A record is visible when it is global
        (project_scope '') or belongs to the requested project. Another
        project's records are never returned, whatever the query matches.
        `touch` stamps `last_retrieved_at` on what was returned (9.5).

        FR-PRV-002: the workspace filter is not optional and has no "global"
        escape the way project_scope does. A project is a compartment inside
        one tenant's memory; a workspace is a different tenant, and there is
        no such thing as a fact that belongs to all of them.
        """
        owner = self._workspace(workspace_id)
        sql = ("SELECT * FROM memories WHERE superseded=0 AND workspace_id=? "
               "AND (project_scope='' OR project_scope=?)")
        params: list = [owner, project_scope]
        if subject:
            sql += " AND subject=?"
            params.append(subject)
        if needle:
            sql += " AND (subject LIKE ? OR value LIKE ?)"
            params += [f"%{needle}%", f"%{needle}%"]
        if memory_types:
            sql += " AND memory_type IN (%s)" % ",".join("?" for _ in memory_types)
            params += list(memory_types)
        sql += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit)
        rows = [dict(r) for r in self._conn.execute(sql, params)]
        if touch and rows:
            with self._tx() as conn:
                conn.execute(
                    "UPDATE memories SET last_retrieved_at=? WHERE id IN (%s)"
                    % ",".join("?" for _ in rows),
                    [now_iso()] + [r["id"] for r in rows])
        return rows

    def memory_provenance(self, memory_id: int,
                          workspace_id: str | None = None) -> dict | None:
        """FR-018: where a remembered fact came from and whether it is
        current - the record, what it superseded, what superseded it, and
        any open contradiction on its subject.

        FR-PRV-002: a row belonging to another workspace RAISES
        `CrossWorkspaceRefused` rather than returning None. Returning None
        would be indistinguishable from "no such memory", which turns a
        boundary violation into a lookup miss and lets a caller enumerate
        ids until something comes back. An id is not a capability.
        """
        row = self._conn.execute("SELECT * FROM memories WHERE id=?",
                                 (memory_id,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        from friday.workspace import guard
        guard(workspace_id, str(record.get("workspace_id") or "default"))
        successor = self._conn.execute(
            "SELECT id, value, source, created_at FROM memories WHERE supersedes_id=? "
            "ORDER BY id DESC LIMIT 1", (memory_id,)).fetchone()
        predecessor = None
        if record.get("supersedes_id"):
            predecessor = self._conn.execute(
                "SELECT id, value, source, created_at FROM memories WHERE id=?",
                (record["supersedes_id"],)).fetchone()
        contradictions = [dict(r) for r in self._conn.execute(
            "SELECT * FROM contradictions WHERE subject=? AND resolution='pending'",
            (record["subject"],))]
        return {
            "id": record["id"], "subject": record["subject"], "value": record["value"],
            "kind": record["kind"], "memory_type": record.get("memory_type"),
            "scope": record["scope"], "project_scope": record.get("project_scope", ""),
            "source": record["source"], "source_ref": record.get("source_ref", ""),
            "confidence": record["confidence"], "importance": record.get("importance"),
            "created_at": record["created_at"], "last_confirmed": record.get("last_confirmed"),
            "last_retrieved_at": record.get("last_retrieved_at"),
            "retention_policy": record.get("retention_policy"),
            "current": not record["superseded"],
            "superseded_by": dict(successor) if successor else None,
            "supersedes": dict(predecessor) if predecessor else None,
            "open_contradictions": contradictions,
        }

    def export_memories(self, *, project_scope: str | None = None,
                        include_superseded: bool = False,
                        workspace_id: str | None = None,
                        every_workspace: bool = False) -> list[dict]:
        """FR-019/FR-066: the owner's durable memory as plain records, for
        export or inspection. Scoped when a project is named.

        `every_workspace=True` is the one deliberate hole in the boundary and
        it is spelled out at the call site rather than reachable by omission:
        a whole-install backup (FR-BKP-001) has to read every tenant. It is
        never the default, and no tool a model can call passes it.
        """
        clauses, params = [], []
        if not every_workspace:
            clauses.append("workspace_id=?")
            params.append(self._workspace(workspace_id))
        if not include_superseded:
            clauses.append("superseded=0")
        if project_scope is not None:
            clauses.append("(project_scope='' OR project_scope=?)")
            params.append(project_scope)
        sql = "SELECT * FROM memories"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        return [dict(r) for r in self._conn.execute(sql, params)]

    def expire_memories(self, *, retention_policy: str, run_id: str | None = None,
                        workspace_id: str | None = None) -> int:
        """Lifecycle (FR-016): retire working/session-scoped records when
        their objective or session ends. Marked superseded, never deleted -
        the audit trail stays. Scoped: one workspace's session ending must
        not retire another's working memory."""
        sql = ("UPDATE memories SET superseded=1 WHERE superseded=0 "
               "AND retention_policy=? AND workspace_id=?")
        params: list = [retention_policy, self._workspace(workspace_id)]
        if run_id is not None:
            sql += " AND run_id=?"
            params.append(run_id)
        with self._tx() as conn:
            return conn.execute(sql, params).rowcount

    def recall(self, subject: str, *, include_superseded: bool = False,
               workspace_id: str | None = None) -> list[dict]:
        owner = self._workspace(workspace_id)
        sql = "SELECT * FROM memories WHERE subject=? AND workspace_id=?"
        if not include_superseded:
            sql += " AND superseded=0"
        sql += " ORDER BY created_at DESC"
        return [dict(r) for r in self._conn.execute(sql, (subject, owner))]

    def search_memories(self, needle: str, limit: int = 20,
                        workspace_id: str | None = None) -> list[dict]:
        """Free-text search. The workspace filter comes first in the WHERE
        clause on purpose: this is the query a curious model is most likely
        to reach for, and a substring search that crossed the boundary would
        be the widest possible leak."""
        owner = self._workspace(workspace_id)
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM memories WHERE workspace_id=? AND superseded=0 AND "
            "(subject LIKE ? OR value LIKE ?) ORDER BY created_at DESC LIMIT ?",
            (owner, f"%{needle}%", f"%{needle}%", limit),
        )]

    def supersede(self, subject: str, *, kind: str | None = None,
                  workspace_id: str | None = None) -> int:
        """
        Mark a subject's active rows superseded, across kinds by default.

        `remember()` only supersedes within a kind, so a FACT and an INFERENCE
        about the same subject can coexist - which is correct for the store,
        because they are different claims. The profile layer needs one active
        belief per subject, so it calls this before replacing one.
        """
        sql = ("UPDATE memories SET superseded=1 WHERE subject=? AND superseded=0 "
               "AND workspace_id=?")
        params: list = [subject, self._workspace(workspace_id)]
        if kind is not None:
            sql += " AND kind=?"
            params.append(kind)
        with self._tx() as conn:
            return conn.execute(sql, params).rowcount

    def forget(self, subject: str, *, workspace_id: str | None = None) -> int:
        """Retire a subject's active rows in this workspace only. Forgetting
        in one tenant must not forget for another - a shared subject name
        like "pricing policy" is exactly the collision that would."""
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE memories SET superseded=1 WHERE subject=? AND superseded=0 "
                "AND workspace_id=?",
                (subject, self._workspace(workspace_id)),
            )
            return cur.rowcount

    # -- utterances (§14) ---------------------------------------------------

    def record_utterance(
        self, raw: str, *, normalized: str | None = None, reason: str | None = None,
        evidence: str | None = None, confidence: float | None = None,
        run_id: str | None = None,
    ) -> int:
        """Raw is stored verbatim and never overwritten by a later correction."""
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO utterances (run_id, raw, normalized, correction_reason, "
                "correction_evidence, correction_confidence, created_at) VALUES (?,?,?,?,?,?,?)",
                (run_id, raw, normalized, reason, evidence, confidence, now_iso()),
            )
            return int(cur.lastrowid)

    def get_utterance(self, utterance_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM utterances WHERE id=?", (utterance_id,)
        ).fetchone()
        return dict(row) if row else None

    # -- projects and decisions ---------------------------------------------

    def ensure_project(self, name: str, summary: str | None = None) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO projects (name, created_at, summary) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "summary=COALESCE(excluded.summary, projects.summary)",
                (name, now_iso(), summary),
            )

    def projects(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM projects ORDER BY created_at"
        )]

    def record_decision(
        self, project: str, decision: str, *, source: str,
        rationale: str | None = None, run_id: str | None = None,
    ) -> int:
        self.ensure_project(project)
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO project_decisions (project, decision, rationale, "
                "source, run_id, created_at) VALUES (?,?,?,?,?,?)",
                (project, decision, rationale, source, run_id, now_iso()),
            )
            return int(cur.lastrowid)

    def decisions(self, project: str) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM project_decisions WHERE project=? AND superseded=0 "
            "ORDER BY created_at DESC", (project,)
        )]

    # -- project brain (BRAIN-001) --------------------------------------------
    # Thin on purpose: the kinds, body shapes and secret filter belong to
    # friday/project_brain.py. These only keep and fetch rows.

    #: The table and key column each Project Brain `ref` may point into.
    BRAIN_REF_TABLES = {
        "project_decisions": "id", "open_questions": "id",
        "assumption_register": "id", "requirements": "id",
        "artifacts": "artifact_id",
    }

    def brain_ref_exists(self, ref: str) -> bool:
        """Whether `table:id` names a real row in a table a brain record may
        reference. The table and column come from BRAIN_REF_TABLES, never from
        the caller; only the id is a bound parameter."""
        table, _, row_id = ref.partition(":")
        column = self.BRAIN_REF_TABLES.get(table)
        if column is None or not row_id:
            return False
        return self._conn.execute(
            f"SELECT 1 FROM {table} WHERE {column}=?", (row_id,)
        ).fetchone() is not None

    def brain_insert(self, project: str, kind: str, key: str, body: str, *,
                     origin: str, ref: str | None = None,
                     supersedes: int | None = None) -> int:
        """Insert an active record. sqlite3.IntegrityError if the kind is
        unknown or (project, kind, key) already has an active record."""
        self.ensure_project(project)
        with self._tx() as conn:
            return self._brain_insert(conn, project, kind, key, body, origin,
                                      ref, supersedes)

    def brain_supersede(self, project: str, kind: str, key: str, body: str, *,
                        origin: str, ref: str | None = None) -> int:
        """Retire the active record and insert its replacement in one
        transaction. LookupError if there is no active record to retire."""
        with self._tx() as conn:
            old = conn.execute(
                "SELECT id FROM project_brain_records "
                "WHERE project=? AND kind=? AND key=? AND state='active'",
                (project, kind, key)).fetchone()
            if old is None:
                raise LookupError(f"no active {kind} record {key!r} in {project!r}")
            conn.execute(
                "UPDATE project_brain_records SET state='superseded', "
                "updated_at=? WHERE id=?", (now_iso(), old["id"]))
            return self._brain_insert(conn, project, kind, key, body, origin,
                                      ref, int(old["id"]))

    @staticmethod
    def _brain_insert(conn, project, kind, key, body, origin, ref,
                      supersedes) -> int:
        stamp = now_iso()
        cur = conn.execute(
            "INSERT INTO project_brain_records (project, kind, key, body, "
            "origin, ref, supersedes, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (project, kind, key, body, origin, ref, supersedes, stamp, stamp))
        return int(cur.lastrowid)

    def brain_rows(self, project: str, *, kind: str | None = None,
                   key: str | None = None,
                   include_superseded: bool = False) -> list[dict]:
        """Records for a project, ordered by kind, key, then age (oldest first,
        so a key's history reads top to bottom)."""
        sql = "SELECT * FROM project_brain_records WHERE project=?"
        args: list = [project]
        if kind is not None:
            sql += " AND kind=?"
            args.append(kind)
        if key is not None:
            sql += " AND key=?"
            args.append(key)
        if not include_superseded:
            sql += " AND state='active'"
        sql += " ORDER BY kind, key, id"
        return [dict(r) for r in self._conn.execute(sql, args)]

    def ask_question(self, project: str, question: str, *, why: str = "",
                     options: str = "", blocking: bool = True, impact: str = "",
                     assumption: str = "", assumption_reason: str = "") -> int:
        """
        Record a question, and whether work should wait for it.

        `blocking` defaults to True because a question Friday could not
        classify is one it should not guess about. The cheap ones are marked
        explicitly.
        """
        self.ensure_project(project)
        status = "ASSUMED" if assumption and not blocking else "OPEN"
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO open_questions (project, question, why, options, "
                "blocking, impact, assumption, assumption_reason, status, asked_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (project, question, why, options, int(bool(blocking)), impact,
                 assumption, assumption_reason, status, now_iso()),
            )
            return int(cur.lastrowid)

    def blocking_questions(self, project: str = "") -> list[dict]:
        """Unanswered questions that work should actually wait for."""
        return [row for row in self.open_questions(project)
                if row.get("blocking")]

    def assumptions(self, project: str = "") -> list[dict]:
        """
        What Friday decided in the absence of an answer.

        Kept apart from decisions on purpose. A decision is something the boss
        said; an assumption is something Friday chose and can be overruled
        without anybody having been wrong.
        """
        return [row for row in self.open_questions(project)
                if row.get("assumption")]

    def open_questions(self, project: str = "") -> list[dict]:
        """Everything asked and not yet answered, oldest first."""
        if project:
            rows = self._conn.execute(
                "SELECT * FROM open_questions WHERE project=? AND answer IS NULL ORDER BY asked_at",
                (project,))
        else:
            rows = self._conn.execute(
                "SELECT * FROM open_questions WHERE answer IS NULL ORDER BY asked_at")
        return [dict(r) for r in rows]

    def answer_question(self, question_id: int, answer: str) -> bool:
        """True if this closed a question that was actually open."""
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE open_questions SET answer=?, answered_at=? WHERE id=? AND answer IS NULL",
                (answer, now_iso(), question_id))
            return cur.rowcount > 0

    def save_assumption(self, run_id: str, *, key: str, statement: str,
                        origin: str, confidence: str, consequence: str,
                        validation: str = "", state: str = "UNVALIDATED") -> None:
        """Record an assumption against an objective run; the same key updates."""
        stamp = now_iso()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO assumption_register (run_id, key, statement, origin, "
                "confidence, consequence, validation, state, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id, key) DO UPDATE SET statement=excluded.statement, "
                "origin=excluded.origin, confidence=excluded.confidence, "
                "consequence=excluded.consequence, validation=excluded.validation, "
                "state=excluded.state, updated_at=excluded.updated_at",
                (run_id, key, statement, origin, confidence, consequence,
                 validation, state, stamp, stamp))

    def list_assumptions(self, run_id: str) -> list[dict]:
        """The run's register, in the order it was recorded."""
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM assumption_register WHERE run_id=? ORDER BY id",
            (run_id,))]

    def set_assumption_state(self, run_id: str, key: str, state: str) -> bool:
        """True if the run had that assumption."""
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE assumption_register SET state=?, updated_at=? "
                "WHERE run_id=? AND key=?", (state, now_iso(), run_id, key))
            return cur.rowcount > 0

    _SHADOW_COLUMNS = (
        "at", "fingerprint", "words", "input_source", "turn_id", "run_id",
        "router_version", "taxonomy_version", "threshold_version",
        "request_shape", "predicted_operation", "predicted_target",
        "predicted_capability", "predicted_argument_shape",
        "referent_available", "referent_type", "referent_source",
        "decision", "abstention_reason", "blame",
        "winner_score", "runner_up_score", "margin", "latency_ms",
    )

    def record_shadow(self, **fields) -> int:
        """One prediction, unacted on."""
        unknown = set(fields) - set(self._SHADOW_COLUMNS)
        if unknown:
            raise ValueError(f"shadow_predictions has no column {sorted(unknown)}")
        fields.setdefault("at", now_iso())
        fields["referent_available"] = int(bool(fields.get("referent_available")))
        names = list(fields)
        with self._tx() as conn:
            cursor = conn.execute(
                f"INSERT INTO shadow_predictions ({', '.join(names)}) VALUES ("
                f"{', '.join('?' for _ in names)})",
                [fields[name] for name in names])
            return int(cursor.lastrowid)

    def shadow_prediction(self, fingerprint: str, *,
                          settled: bool = False) -> dict | None:
        """The most recent prediction for this utterance."""
        clause = "settled_at IS NOT NULL" if settled else "settled_at IS NULL"
        row = self._conn.execute(
            f"SELECT * FROM shadow_predictions WHERE fingerprint=? AND {clause} ORDER BY id DESC LIMIT 1",
            (fingerprint,)).fetchone()
        return dict(row) if row else None

    _SHADOW_SETTLE = (
        "production_capability", "action_result_status", "comparison_status",
        "label_source", "label_grounding", "label_strength",
        "execution_correct", "intent_correct",
    )

    def settle_shadow(self, prediction_id: int, **fields) -> None:
        unknown = set(fields) - set(self._SHADOW_SETTLE)
        if unknown:
            raise ValueError(f"cannot settle {sorted(unknown)}")
        for name in ("execution_correct", "intent_correct"):
            if fields.get(name) is not None:
                fields[name] = int(bool(fields[name]))
        sets = ", ".join(f"{name}=?" for name in fields)
        with self._tx() as conn:
            conn.execute(
                f"UPDATE shadow_predictions SET {sets}, settled_at=? WHERE id=?",
                [*fields.values(), now_iso(), prediction_id])

    def shadow_rows(self, limit: int = 5000) -> list[dict]:
        return [dict(row) for row in self._conn.execute(
            "SELECT * FROM shadow_predictions ORDER BY id DESC LIMIT ?",
            (limit,))]

    def purge_shadow(self, *, before: str = "") -> int:
        """Delete shadow rows. Behavioural metadata does not accumulate."""
        with self._tx() as conn:
            if before:
                cursor = conn.execute(
                    "DELETE FROM shadow_predictions WHERE at < ?", (before,))
            else:
                cursor = conn.execute("DELETE FROM shadow_predictions")
            return cursor.rowcount

    def record_routing_correction(self, **fields) -> int:
        allowed = ("at", "fingerprint", "previous_operation", "previous_target",
                   "previous_capability", "corrected_operation",
                   "corrected_target", "corrected_capability", "referent_type",
                   "evidence")
        unknown = set(fields) - set(allowed)
        if unknown:
            raise ValueError(f"routing_corrections has no column {sorted(unknown)}")
        fields.setdefault("at", now_iso())
        names = list(fields)
        with self._tx() as conn:
            cursor = conn.execute(
                f"INSERT INTO routing_corrections ({', '.join(names)}) VALUES ("
                f"{', '.join('?' for _ in names)})",
                [fields[name] for name in names])
            return int(cursor.lastrowid)

    def routing_corrections(self, limit: int = 500) -> list[dict]:
        return [dict(row) for row in self._conn.execute(
            "SELECT * FROM routing_corrections ORDER BY id DESC LIMIT ?",
            (limit,))]

    def add_requirement(self, project: str, statement: str, *,
                        category: str = "FUNCTIONAL", rationale: str = "",
                        source: str = "", acceptance=(), assumptions=(),
                        status: str = "PROPOSED",
                        needs_target: bool = False) -> int:
        import json

        self.ensure_project(project)
        with self._tx() as conn:
            cursor = conn.execute(
                "INSERT INTO requirements (project, statement, category, rationale, "
                "source, acceptance, assumptions, status, needs_target, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (project, statement, category, rationale, source,
                 json.dumps(list(acceptance)), json.dumps(list(assumptions)),
                 status, int(bool(needs_target)), now_iso()),
            )
            return int(cursor.lastrowid)

    def requirements(self, project: str, *,
                     include_superseded: bool = False) -> list[dict]:
        import json

        clause = "" if include_superseded else " AND status != 'SUPERSEDED'"
        rows = []
        for row in self._conn.execute(
                f"SELECT * FROM requirements WHERE project=?{clause} ORDER BY id",
                (project,)):
            item = dict(row)
            for key in ("acceptance", "assumptions"):
                try:
                    item[key] = json.loads(item[key] or "[]")
                except (TypeError, ValueError):
                    item[key] = []
            item["needs_target"] = bool(item["needs_target"])
            rows.append(item)
        return rows

    def supersede_requirement(self, requirement_id: int, *, why: str,
                              replaced_by: int | None = None) -> None:
        """
        Retire a requirement without losing it.

        "Why did we remove multiplayer?" needs the old row and the reason,
        and a DELETE answers it with silence.
        """
        with self._tx() as conn:
            conn.execute(
                "UPDATE requirements SET status='SUPERSEDED', superseded_by=?, "
                "why_changed=?, changed_at=? WHERE id=?",
                (replaced_by, why, now_iso(), requirement_id))

    def accept_requirement(self, requirement_id: int) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE requirements SET status='ACCEPTED', changed_at=? WHERE id=?",
                (now_iso(), requirement_id))

    # -- conversations ------------------------------------------------------

    def start_conversation(
        self, conversation_id: str, *, title: str | None = None,
        project: str | None = None,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO conversations "
                "(conversation_id, title, project, started_at) VALUES (?,?,?,?)",
                (conversation_id, title, project, now_iso()),
            )

    def add_message(
        self, conversation_id: str, role: str, content: str, *,
        run_id: str | None = None,
    ) -> int:
        self.start_conversation(conversation_id)
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, content, run_id, created_at) "
                "VALUES (?,?,?,?,?)",
                (conversation_id, role, content, run_id, now_iso()),
            )
            return int(cur.lastrowid)

    def messages(self, conversation_id: str, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        )
        return [dict(r) for r in rows][::-1]

    def truncate_message(self, message_id: int, heard: str) -> bool:
        """
        FR-039 (PRD v3.1): when the boss interrupts, history keeps only what
        he actually heard. `heard` is the delivered prefix; the row is
        rewritten to it with an interruption marker so the model sees a
        turn that stopped, not a turn that finished. Returns False when the
        id is unknown or `heard` is not a prefix of what was stored (a
        client cannot rewrite a reply into something else).
        """
        heard = (heard or "").strip()
        with self._tx() as conn:
            row = conn.execute("SELECT content FROM messages WHERE id=?",
                               (int(message_id),)).fetchone()
            if row is None:
                return False
            full = (row["content"] if hasattr(row, "keys") else row[0]) or ""
            if heard and not full.startswith(heard):
                return False
            marker = " [interrupted]"
            content = (heard + marker) if heard else "[interrupted before anything was heard]"
            if full.endswith(marker) and full == content:
                return True
            conn.execute("UPDATE messages SET content=? WHERE id=?",
                         (content, int(message_id)))
            return True

    def recent_messages(self, limit: int = 30) -> list[dict]:
        """
        The last N turns Friday had with anyone, oldest first.

        Deliberately NOT scoped to one conversation. The failure this exists
        for is "say hey, and the next session has forgotten yesterday": recall
        that stops at a conversation boundary is the same amnesia with extra
        steps. Conversation id is still on every row for anyone who wants one
        thread.
        """
        rows = self._conn.execute(
            "SELECT conversation_id, role, content, created_at FROM messages "
            "ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows][::-1]

    # -- fabric outcomes ----------------------------------------------------

    def record_fabric_outcome(self, provider_id: str, operation: str,
                              ok: bool) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO fabric_outcomes (provider_id, operation, ok, at) "
                "VALUES (?,?,?,?)",
                (provider_id, operation or "", 1 if ok else 0, now_iso()))

    def fabric_outcomes(self, since_iso: str = "") -> list[dict]:
        """Every outcome since `since_iso`, newest last.

        Returned whole rather than per provider: ranking asks about every
        candidate on a request, so one query the caller caches beats one query
        per candidate on a voice turn.
        """
        if since_iso:
            rows = self._conn.execute(
                "SELECT provider_id, operation, ok FROM fabric_outcomes "
                "WHERE at >= ? ORDER BY id", (since_iso,))
        else:
            rows = self._conn.execute(
                "SELECT provider_id, operation, ok FROM fabric_outcomes "
                "ORDER BY id")
        return [dict(r) for r in rows]

    def prune_fabric_outcomes(self, before_iso: str) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM fabric_outcomes WHERE at < ?", (before_iso,))
            return int(cur.rowcount or 0)

    # -- ultracode stage results (ULTRA-002) --------------------------------

    def ultracode_result_get(self, workspace_id: str, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT result FROM ultracode_results WHERE workspace_id=? AND result_key=?",
            (workspace_id, key)).fetchone()
        return json.loads(row["result"]) if row else None

    def ultracode_result_put(self, workspace_id: str, key: str, result: dict) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ultracode_results "
                "(workspace_id, result_key, result, created_at) VALUES (?,?,?,?)",
                (workspace_id, key, json.dumps(result, sort_keys=True), now_iso()))

    # -- opportunities inbox (PRO-001) --------------------------------------

    OPPORTUNITY_RESOLUTIONS = ("resolved", "dismissed")

    def opportunity_raise(self, workspace_id: str, kind: str, subject: str,
                          evidence_key: str, detail: dict) -> dict | None:
        """Record one opportunity. Returns the new row, or None when this exact
        fact is already in the inbox (open or not): re-scanning is free."""
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO opportunities "
                "(workspace_id, kind, subject, evidence_key, detail, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (workspace_id, kind, subject, evidence_key,
                 json.dumps(detail, sort_keys=True), now_iso()))
            if not cur.rowcount:
                return None
            row = conn.execute("SELECT * FROM opportunities WHERE id=?",
                               (cur.lastrowid,)).fetchone()
        return self._opportunity(row)

    @staticmethod
    def _opportunity(row) -> dict:
        got = dict(row)
        got["detail"] = json.loads(got["detail"])
        return got

    def opportunities(self, workspace_id: str, status: str = "open") -> list[dict]:
        return [self._opportunity(r) for r in self._conn.execute(
            "SELECT * FROM opportunities WHERE workspace_id=? AND status=? "
            "ORDER BY id", (workspace_id, status))]

    def opportunity_resolve(self, workspace_id: str, opportunity_id: int,
                            status: str) -> bool:
        """Close an open opportunity of this workspace. False when there is
        none such - another workspace's row is not visible here."""
        if status not in self.OPPORTUNITY_RESOLUTIONS:
            raise ValueError(f"unknown resolution {status!r}; "
                             f"known: {list(self.OPPORTUNITY_RESOLUTIONS)}")
        with self._tx() as conn:
            return conn.execute(
                "UPDATE opportunities SET status=?, resolved_at=? "
                "WHERE id=? AND workspace_id=? AND status='open'",
                (status, now_iso(), opportunity_id, workspace_id)).rowcount > 0

    # -- evolution proposals (EVO-001) --------------------------------------

    def evolution_proposal_raise(self, proposal_id: str, dedupe_key: str, *,
                                 trigger_kind: str, subject: str, klass: str,
                                 zone: str, body: dict) -> tuple[dict, bool]:
        """Record one proposal. A second raise with the same `dedupe_key` adds
        no row: it counts one more occurrence, and it can only RAISE the zone
        (Z1 < ... < Z4, so a trust root seen later is never absorbed into a
        lower-zone row), adopting the new risk and uniting the components.
        Returns (row, created)."""
        now = now_iso()
        with self._tx() as conn:
            created = conn.execute(
                "INSERT INTO evolution_proposals "
                "(proposal_id, dedupe_key, trigger_kind, subject, klass, zone, body, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(dedupe_key) DO NOTHING",
                (proposal_id, dedupe_key, trigger_kind, subject, klass, zone,
                 json.dumps(body, sort_keys=True), now, now)).rowcount > 0
            if not created:
                old = conn.execute("SELECT zone, body FROM evolution_proposals "
                                   "WHERE dedupe_key=?", (dedupe_key,)).fetchone()
                kept = json.loads(old["body"])
                kept["components"] = list(dict.fromkeys(
                    [*kept.get("components", []), *body.get("components", [])]))
                kept["cost"] = {**kept.get("cost", {}), "components": len(kept["components"])}
                new_zone = max(old["zone"], zone)
                if new_zone != old["zone"]:
                    kept["risk"] = body["risk"]
                conn.execute(
                    "UPDATE evolution_proposals SET occurrences=occurrences+1, zone=?, "
                    "body=?, updated_at=? WHERE dedupe_key=?",
                    (new_zone, json.dumps(kept, sort_keys=True), now, dedupe_key))
            row = conn.execute("SELECT * FROM evolution_proposals WHERE dedupe_key=?",
                               (dedupe_key,)).fetchone()
        return self._evolution_proposal(row), created

    @staticmethod
    def _evolution_proposal(row) -> dict:
        got = dict(row)
        got["body"] = json.loads(got["body"])
        return got

    def evolution_proposal(self, proposal_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM evolution_proposals WHERE proposal_id=?",
                                 (proposal_id,)).fetchone()
        return self._evolution_proposal(row) if row else None

    def evolution_proposals(self, status: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM evolution_proposals", ()
        if status is not None:
            sql, args = sql + " WHERE status=?", (status,)
        return [self._evolution_proposal(r)
                for r in self._conn.execute(sql + " ORDER BY created_at, proposal_id", args)]

    def evolution_proposal_move(self, proposal_id: str, from_status: str, to_status: str) -> bool:
        """Change status only from the one the caller saw; False when it moved."""
        with self._tx() as conn:
            return conn.execute(
                "UPDATE evolution_proposals SET status=?, updated_at=? "
                "WHERE proposal_id=? AND status=?",
                (to_status, now_iso(), proposal_id, from_status)).rowcount > 0

    def evolution_proposal_link(self, proposal_id: str, run_id: str, lease: str) -> bool:
        """Remember which run and governor lease an admitted proposal holds, so
        a restart can still find and release them."""
        with self._tx() as conn:
            return conn.execute(
                "UPDATE evolution_proposals SET run_id=?, lease=?, updated_at=? "
                "WHERE proposal_id=? AND status='admitted'",
                (run_id, lease, now_iso(), proposal_id)).rowcount > 0

    # -- contacts -----------------------------------------------------------

    def save_contact(self, name: str, **fields) -> None:
        """Upsert by name. Only the fields given are written; the rest survive."""
        name = (name or "").strip()
        if not name:
            raise ValueError("a contact needs a name")
        cols = ("relation", "phone", "email", "aliases", "notes")
        given = {k: str(v).strip() for k, v in fields.items()
                 if k in cols and v is not None}
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO contacts (name, created_at, updated_at) "
                "VALUES (?,?,?)", (name, now_iso(), now_iso()))
            for key, value in given.items():
                conn.execute(
                    f"UPDATE contacts SET {key}=?, updated_at=? WHERE name=?",
                    (value, now_iso(), name))

    def contacts(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM contacts ORDER BY name LIMIT ?", (limit,))]

    def find_contacts(self, query: str, limit: int = 5) -> list[dict]:
        """
        Contacts a request is plausibly about, by name, alias or relation.

        Substring on purpose: "call mum" has to reach the row whose alias list
        is "mum, mummy", and a request never spells a name the way the row
        does.
        """
        words = {w for w in re.split(r"[^a-z0-9]+", (query or "").lower()) if len(w) > 1}
        if not words:
            return []
        hits = []
        for row in self.contacts(limit=500):
            hay = " ".join(str(row.get(k) or "") for k in
                           ("name", "aliases", "relation")).lower()
            terms = {w for w in re.split(r"[^a-z0-9]+", hay) if w}
            if words & terms:
                hits.append(row)
            if len(hits) >= limit:
                break
        return hits

    def close_conversation(self, conversation_id: str, summary: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE conversations SET ended_at=?, summary=? WHERE conversation_id=?",
                (now_iso(), summary, conversation_id),
            )

    def recent_conversations(self, limit: int = 5) -> list[dict]:
        """
        Most recent first. Unlike Mark-L's pop_last_session, reading does not
        consume - the same recap can be asked for twice and answer the same.
        """
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM conversations ORDER BY started_at DESC LIMIT ?", (limit,)
        )]

    # -- user model: observations and contradictions -------------------------

    def add_observation(
        self, *, dimension: str, subject: str, value: str, kind: str,
        confidence: float, evidence: str, conversation_id: str | None = None,
        run_id: str | None = None, status: str = "pending",
        workspace_id: str | None = None,
    ) -> int:
        """An observed episode, with the quote it came from.

        Scoped to a workspace (FR-PRV-002) because promotion reads these to
        decide what becomes a durable memory: an unscoped observation would
        let one tenant's overheard detail be promoted into another tenant's
        facts, which is the boundary breached one step earlier than the
        memory table.
        """
        if not evidence.strip():
            raise ValueError("an observation must carry the quote it came from")
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO observations (conversation_id, run_id, dimension, "
                "subject, value, kind, confidence, evidence, status, created_at, "
                "workspace_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (conversation_id, run_id, dimension, subject, value, kind,
                 confidence, evidence, status, now_iso(),
                 self._workspace(workspace_id)),
            )
            return int(cur.lastrowid)

    def set_observation_status(
        self, observation_id: int, status: str, memory_id: int | None = None
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE observations SET status=?, memory_id=COALESCE(?, memory_id) "
                "WHERE id=?",
                (status, memory_id, observation_id),
            )

    def observations(
        self, *, dimension: str | None = None, subject: str | None = None,
        status: str | None = None, limit: int = 100,
        workspace_id: str | None = None,
    ) -> list[dict]:
        clauses, params = ["workspace_id=?"], [self._workspace(workspace_id)]
        if dimension:
            clauses.append("dimension=?")
            params.append(dimension)
        if subject:
            clauses.append("subject=?")
            params.append(subject)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}"
        params.append(limit)
        return [dict(r) for r in self._conn.execute(
            f"SELECT * FROM observations {where} ORDER BY created_at DESC LIMIT ?",
            params,
        )]

    def add_contradiction(
        self, *, subject: str, existing_value: str, existing_kind: str,
        new_value: str, new_kind: str, observation_id: int | None = None,
        resolution: str = "pending", rationale: str | None = None,
    ) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO contradictions (subject, existing_value, existing_kind, "
                "new_value, new_kind, observation_id, resolution, rationale, "
                "created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (subject, existing_value, existing_kind, new_value, new_kind,
                 observation_id, resolution, rationale, now_iso(),
                 now_iso() if resolution != "pending" else None),
            )
            return int(cur.lastrowid)

    def contradictions(self, *, resolution: str | None = None,
                       limit: int = 50) -> list[dict]:
        where = "WHERE resolution=?" if resolution else ""
        params = ([resolution] if resolution else []) + [limit]
        return [dict(r) for r in self._conn.execute(
            f"SELECT * FROM contradictions {where} ORDER BY created_at DESC LIMIT ?",
            params,
        )]

    def resolve_contradiction(
        self, contradiction_id: int, resolution: str, rationale: str
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE contradictions SET resolution=?, rationale=?, resolved_at=? "
                "WHERE id=?",
                (resolution, rationale, now_iso(), contradiction_id),
            )

    def memories_by_scope(self, scope: str, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM memories WHERE scope=? AND superseded=0 "
            "ORDER BY confidence DESC, created_at DESC LIMIT ?",
            (scope, limit),
        )]

    # -- reminders ----------------------------------------------------------

    def save_reminder(
        self, message: str, due_at: str, *, scheduler: str | None = None,
        job_id: str | None = None,
    ) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO reminders (message, due_at, scheduler, job_id, created_at) "
                "VALUES (?,?,?,?,?)",
                (message, due_at, scheduler, job_id, now_iso()),
            )
            return int(cur.lastrowid)

    def pending_reminders(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM reminders WHERE fired=0 ORDER BY due_at"
        )]

    def set_reminder_job(self, reminder_id: int, job_id: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE reminders SET job_id=? WHERE id=?",
                         (job_id, reminder_id))

    def close_reminder(self, reminder_id: int) -> None:
        """Mark a reminder as no longer pending (fired or cancelled)."""
        with self._tx() as conn:
            conn.execute("UPDATE reminders SET fired=1 WHERE id=?", (reminder_id,))

    def get_reminder(self, reminder_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM reminders WHERE id=?", (reminder_id,)
        ).fetchone()
        return dict(row) if row else None

    # -- automations --------------------------------------------------------

    def save_automation(self, name: str, *, trigger: dict, steps: list,
                        description: str = "", task_name: str | None = None,
                        enabled: bool = True) -> None:
        """Create or replace an automation. The engine validates before this."""
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO automations "
                "  (name, description, trigger, steps, enabled, task_name, created_at) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "  description=excluded.description, trigger=excluded.trigger, "
                "  steps=excluded.steps, enabled=excluded.enabled, "
                "  task_name=excluded.task_name",
                (name, description, json.dumps(trigger), json.dumps(steps),
                 1 if enabled else 0, task_name, now_iso()),
            )

    @staticmethod
    def _automation(row) -> dict:
        got = dict(row)
        got["trigger"] = json.loads(got["trigger"])
        got["steps"] = json.loads(got["steps"])
        got["enabled"] = bool(got["enabled"])
        return got

    def get_automation(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM automations WHERE name=?", (name,)).fetchone()
        return self._automation(row) if row else None

    def automations(self) -> list[dict]:
        return [self._automation(r) for r in self._conn.execute(
            "SELECT * FROM automations ORDER BY name")]

    def delete_automation(self, name: str) -> bool:
        with self._tx() as conn:
            return conn.execute(
                "DELETE FROM automations WHERE name=?", (name,)).rowcount > 0

    def start_automation_run(self, run_id: str, name: str, fired_by: str,
                             runtime: dict | None = None) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO automation_runs "
                "  (run_id, name, fired_by, status, started_at, runtime) "
                "VALUES (?,?,?,?,?,?)",
                (run_id, name, fired_by, "running", now_iso(),
                 json.dumps(runtime or {})),
            )

    def finish_automation_run(self, run_id: str, *, status: str, steps: list,
                              error: str | None = None) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE automation_runs SET status=?, steps=?, error=?, "
                "  finished_at=? WHERE run_id=?",
                (status, json.dumps(steps), error, now_iso(), run_id),
            )

    def claim_automation_execution(self, execution_key: str, run_id: str) -> dict | None:
        """Claim one scheduled execution for `run_id`. Returns None when the
        claim is new (the caller may run), or the PRIOR run's record when
        this key was already claimed - by a run that finished, or by one
        still running in another process. One INSERT with a primary key is
        the whole mechanism: two processes racing the same key cannot both
        win, and the claim is on disk before any step executes, so a crash
        after the claim leaves a `running` row a re-fire will find rather
        than a second execution (invariant A-048 "scheduler/idempotency")."""
        with self._tx() as conn:
            try:
                conn.execute(
                    "INSERT INTO automation_executions (execution_key, run_id, claimed_at) "
                    "VALUES (?,?,?)", (execution_key, run_id, now_iso()))
                return None
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT r.run_id, r.status, r.steps FROM automation_executions e "
                    "JOIN automation_runs r ON r.run_id = e.run_id "
                    "WHERE e.execution_key=?", (execution_key,)).fetchone()
                if row is None:
                    # Claimed, but the run row was never written (died between
                    # the two INSERTs). Still a claim: report it as running.
                    prior = conn.execute(
                        "SELECT run_id FROM automation_executions WHERE execution_key=?",
                        (execution_key,)).fetchone()
                    return {"run_id": prior["run_id"], "status": "running", "steps": []}
                try:
                    steps = json.loads(row["steps"] or "[]")
                except ValueError:
                    steps = []
                return {"run_id": row["run_id"], "status": row["status"], "steps": steps}

    # -- schedules (PRD v3.1 FR-041/042) ------------------------------------

    def save_schedule(self, name: str, *, objective: str, tasks: list, trigger: dict,
                      budgets: dict, permissions: list, delivery: str, condition: dict,
                      description: str = "", task_name: str | None = None,
                      enabled: bool = True, provenance: str | None = None) -> None:
        """Write a schedule. `provenance` is who wrote it (TRUST-002): a new
        author's replaces the old one's, because the content is now theirs;
        a re-save that names none keeps what is recorded."""
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO schedules (name, description, objective, tasks, trigger, budgets, "
                "  permissions, delivery, condition, enabled, task_name, created_at, updated_at, "
                "  provenance) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET description=excluded.description, "
                "  objective=excluded.objective, tasks=excluded.tasks, trigger=excluded.trigger, "
                "  budgets=excluded.budgets, permissions=excluded.permissions, "
                "  delivery=excluded.delivery, condition=excluded.condition, "
                "  enabled=excluded.enabled, task_name=excluded.task_name, "
                "  updated_at=excluded.updated_at, "
                "  provenance=COALESCE(excluded.provenance, schedules.provenance)",
                (name, description, objective, json.dumps(tasks), json.dumps(trigger),
                 json.dumps(budgets), json.dumps(permissions), delivery, json.dumps(condition),
                 1 if enabled else 0, task_name, now_iso(), now_iso(), provenance))

    @staticmethod
    def _schedule(row) -> dict:
        got = dict(row)
        for key in ("tasks", "trigger", "budgets", "permissions", "condition"):
            try:
                got[key] = json.loads(got[key]) if isinstance(got[key], str) else got[key]
            except ValueError:
                pass
        got["enabled"] = bool(got["enabled"])
        return got

    def get_schedule(self, name: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM schedules WHERE name=?", (name,)).fetchone()
        return self._schedule(row) if row else None

    def schedules(self) -> list[dict]:
        return [self._schedule(r) for r in
                self._conn.execute("SELECT * FROM schedules ORDER BY name")]

    def delete_schedule(self, name: str) -> bool:
        with self._tx() as conn:
            return conn.execute("DELETE FROM schedules WHERE name=?", (name,)).rowcount > 0

    # -- the emergency stop (PRD v7 STOP-001) ---------------------------------

    def stop_latch(self, *, reason: str, actor: str, now: str) -> bool:
        """Latch the stop. False if it was already latched (the first reason stays)."""
        with self._tx() as conn:
            return conn.execute(
                "INSERT OR IGNORE INTO stop_latch (id, reason, actor, latched_at) "
                "VALUES (1, ?, ?, ?)", (reason, actor, now)).rowcount == 1

    def stop_latched(self) -> dict | None:
        row = self._conn.execute("SELECT * FROM stop_latch WHERE id=1").fetchone()
        return dict(row) if row else None

    def stop_clear(self) -> bool:
        with self._tx() as conn:
            return conn.execute("DELETE FROM stop_latch WHERE id=1").rowcount == 1

    # -- network grants (PRD v7 NET-001) --------------------------------------

    def grant_issue(self, *, grant_id: str, objective_id: str, mode: str,
                    hosts: tuple[str, ...], expires_at: str, now: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO network_grants (grant_id, objective_id, mode, hosts, "
                "expires_at, issued_at) VALUES (?,?,?,?,?,?)",
                (grant_id, objective_id, mode, json.dumps(list(hosts)),
                 expires_at, now))

    def grant(self, grant_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM network_grants WHERE grant_id=?", (grant_id,)).fetchone()
        return dict(row) if row else None

    def grant_revoked(self, grant_id: str) -> bool:
        """Revoked, or never issued: either way it licenses nothing."""
        row = self.grant(grant_id)
        return row is None or row["revoked_at"] is not None

    def grant_revoke(self, grant_id: str, *, reason: str, now: str) -> bool:
        with self._tx() as conn:
            return conn.execute(
                "UPDATE network_grants SET revoked_at=?, revoked_reason=? "
                "WHERE grant_id=? AND revoked_at IS NULL",
                (now, reason, grant_id)).rowcount == 1

    def grants_revoke_all(self, *, reason: str, now: str) -> list[str]:
        """Revoke every live grant; returns the ids that were live."""
        with self._tx() as conn:
            live = [row["grant_id"] for row in conn.execute(
                "SELECT grant_id FROM network_grants WHERE revoked_at IS NULL")]
            conn.execute(
                "UPDATE network_grants SET revoked_at=?, revoked_reason=? "
                "WHERE revoked_at IS NULL", (now, reason))
        return live

    # -- devices (PRD v7 DEVICE-003) -------------------------------------------
    # Each lifecycle step is one conditional UPDATE on the state the step
    # expects, so two racing callers cannot both advance the same device.

    def device_create(self, *, device_id: str, name: str, endpoint: str, state: str,
                      code_hash: str, code_expires_at: str, created_at: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO devices (device_id, name, endpoint, state, code_hash, "
                "code_expires_at, created_at) VALUES (?,?,?,?,?,?,?)",
                (device_id, name, endpoint, state, code_hash, code_expires_at,
                 created_at))

    def device(self, device_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
        return dict(row) if row else None

    def devices(self) -> list[dict]:
        return [dict(row) for row in self._conn.execute(
            "SELECT * FROM devices ORDER BY created_at, device_id")]

    def device_advance(self, device_id: str, *, expect: str, state: str,
                       **fields: str | None) -> bool:
        """`expect` -> `state`, setting `fields` (column names the lifecycle
        owns). True only for the call that changed the row."""
        allowed = {"inventory", "policy", "approved_by", "approved_at", "activated_at"}
        if not set(fields) <= allowed:
            raise ValueError(f"not a device lifecycle field: {sorted(set(fields) - allowed)}")
        sets = ", ".join(["state=?"] + [f"{name}=?" for name in fields])
        with self._tx() as conn:
            return conn.execute(
                f"UPDATE devices SET {sets} WHERE device_id=? AND state=?",
                (state, *fields.values(), device_id, expect)).rowcount == 1

    # -- confirmations (PRD v7, V7-D08) ---------------------------------------
    # Each state change is one conditional UPDATE, and the row count decides:
    # two processes answering or spending the same yes cannot both succeed.

    def confirmation_record(self, *, nonce: str, asker: str, run_id: str,
                            action: str, target: str, fingerprint: str,
                            question: str, state: str, created_at: str,
                            expires_at: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO confirmations (nonce, asker, run_id, action, target, "
                "fingerprint, question, state, created_at, expires_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (nonce, asker, run_id, action, target, fingerprint, question,
                 state, created_at, expires_at))

    def confirmation(self, nonce: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM confirmations WHERE nonce=?", (nonce,)).fetchone()
        return dict(row) if row else None

    def confirmations(self, *, state: str | None = None) -> list[dict]:
        if state is None:
            rows = self._conn.execute(
                "SELECT * FROM confirmations ORDER BY expires_at")
        else:
            rows = self._conn.execute(
                "SELECT * FROM confirmations WHERE state=? ORDER BY expires_at",
                (state,))
        return [dict(row) for row in rows]

    def confirmation_approve(self, nonce: str, *, answered_by: str, now: str) -> bool:
        """PENDING and unexpired -> APPROVED. True only for the answer that
        changed the row."""
        with self._tx() as conn:
            return conn.execute(
                "UPDATE confirmations SET state='APPROVED', answered_by=?, "
                "answered_at=? WHERE nonce=? AND state='PENDING' AND expires_at > ?",
                (answered_by, now, nonce, now)).rowcount == 1

    def confirmation_refuse(self, nonce: str, *, answered_by: str, now: str) -> bool:
        """Anything not yet spent -> REFUSED. A no is final."""
        with self._tx() as conn:
            return conn.execute(
                "UPDATE confirmations SET state='REFUSED', answered_by=?, "
                "answered_at=? WHERE nonce=? AND state IN ('PENDING', 'APPROVED')",
                (answered_by, now, nonce)).rowcount == 1

    def confirmation_spend(self, nonce: str, *, asker: str, fingerprint: str,
                           now: str) -> bool:
        """APPROVED, asked by this process instance, for exactly this action,
        and unexpired -> CONSUMED. True for exactly one spender."""
        with self._tx() as conn:
            return conn.execute(
                "UPDATE confirmations SET state='CONSUMED', spent_at=? "
                "WHERE nonce=? AND state='APPROVED' AND asker=? "
                "AND fingerprint=? AND expires_at > ?",
                (now, nonce, asker, fingerprint, now)).rowcount == 1

    def confirmations_forget(self, *, now: str) -> int:
        """Drop what can no longer be used. Returns how many went."""
        with self._tx() as conn:
            return conn.execute(
                "DELETE FROM confirmations WHERE expires_at <= ? "
                "OR state IN ('CONSUMED', 'REFUSED', 'EXPIRED')", (now,)).rowcount

    def start_schedule_run(self, firing_id: str, name: str, fired_by: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO schedule_runs (firing_id, name, fired_by, status, started_at) "
                "VALUES (?,?,?,?,?)", (firing_id, name, fired_by, "running", now_iso()))

    def finish_schedule_run(self, firing_id: str, *, run_id: str | None, status: str,
                            condition_met: bool, condition_detail: str,
                            delivered_via: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE schedule_runs SET run_id=?, status=?, condition_met=?, "
                "  condition_detail=?, delivered_via=?, finished_at=? WHERE firing_id=?",
                (run_id, status, 1 if condition_met else 0, condition_detail,
                 delivered_via, now_iso(), firing_id))

    def schedule_history(self, name: str | None = None, limit: int = 20) -> list[dict]:
        sql = "SELECT * FROM schedule_runs"
        args: tuple = ()
        if name:
            sql += " WHERE name=?"
            args = (name,)
        sql += " ORDER BY started_at DESC LIMIT ?"
        rows = [dict(r) for r in self._conn.execute(sql, args + (limit,))]
        for r in rows:
            r["condition_met"] = None if r["condition_met"] is None else bool(r["condition_met"])
        return rows

    def automation_history(self, name: str | None = None,
                           limit: int = 20) -> list[dict]:
        sql = "SELECT * FROM automation_runs"
        args: tuple = ()
        if name:
            sql += " WHERE name=?"
            args = (name,)
        sql += " ORDER BY started_at DESC LIMIT ?"
        rows = self._conn.execute(sql, (*args, limit))
        out = []
        for row in rows:
            got = dict(row)
            got["steps"] = json.loads(got["steps"] or "[]")
            got["runtime"] = json.loads(got.get("runtime") or "{}")
            out.append(got)
        return out

    # -- product processing -------------------------------------------------

    def start_product_run(self, run_id: str, *, source: str, total: int,
                          schema_version: int, stages: list) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO product_runs (run_id, source, status, "
                "  schema_version, total_records, stages, started_at) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "  total_records=excluded.total_records",
                (run_id, source, "running", schema_version, total,
                 json.dumps(stages), now_iso()),
            )

    def finish_product_run(self, run_id: str, *, status: str,
                           summary: dict) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE product_runs SET status=?, summary=?, finished_at=? "
                "WHERE run_id=?",
                (status, json.dumps(summary), now_iso(), run_id),
            )

    def save_product_record(self, run_id: str, record: dict) -> None:
        """
        Write one finished record. Replaces its own row, never another run's.

        The primary key is (run_id, product_key) rather than product_key, so
        two runs over the same catalogue keep separate evidence instead of the
        later one quietly overwriting what the earlier one proved.
        """
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO product_records (run_id, product_key, status, "
                "  input_hash, output_hash, quarantine_reason, collapsed, source_row, "
                "  fields, stages, at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id, product_key) DO UPDATE SET "
                "  status=excluded.status, output_hash=excluded.output_hash, "
                "  quarantine_reason=excluded.quarantine_reason, "
                "  collapsed=excluded.collapsed, source_row=excluded.source_row, "
                "  fields=excluded.fields, stages=excluded.stages, at=excluded.at",
                (run_id, record["product_key"], record["status"],
                 record.get("input_hash", ""), record.get("output_hash", ""),
                 record.get("quarantine_reason", ""),
                 int(record.get("collapsed") or 0),
                 json.dumps(record.get("source_row") or {}),
                 json.dumps(record.get("fields") or {}),
                 json.dumps(record.get("stages") or {}), now_iso()),
            )

    def product_records(self, run_id: str, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM product_records WHERE run_id=?"
        args: tuple = (run_id,)
        if status:
            sql += " AND status=?"
            args += (status,)
        rows = []
        for row in self._conn.execute(sql + " ORDER BY product_key", args):
            got = dict(row)
            got["fields"] = json.loads(got["fields"] or "{}")
            got["stages"] = json.loads(got["stages"] or "{}")
            got["source_row"] = json.loads(got.get("source_row") or "{}")
            rows.append(got)
        return rows

    def product_run(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM product_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        got = dict(row)
        got["stages"] = json.loads(got["stages"] or "[]")
        got["summary"] = json.loads(got["summary"] or "{}")
        return got

    def product_runs(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT run_id, source, status, total_records, started_at, "
            # rowid breaks a tie: two runs can share a start time on a
            # coarse clock, and the one written last is the newer (V7-D37).
            "finished_at FROM product_runs ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (limit,))]

    # -- forged skills ------------------------------------------------------

    def save_forged_skill(self, record: dict) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO forged_skills (name, state, version, spec, "
                "  source_sha256, source_path, risk, provenance, verification, "
                "  verified_at, scopes, created_at, updated_at) "
                "VALUES (:name,:state,:version,:spec,:source_sha256,"
                "  :source_path,:risk,:provenance,:verification,:verified_at,"
                "  :scopes,:created_at,:updated_at) "
                "ON CONFLICT(name) DO UPDATE SET "
                "  state=excluded.state, version=excluded.version, "
                "  spec=excluded.spec, source_sha256=excluded.source_sha256, "
                "  source_path=excluded.source_path, risk=excluded.risk, "
                "  provenance=excluded.provenance, "
                "  verification=excluded.verification, "
                "  verified_at=excluded.verified_at, scopes=excluded.scopes, "
                "  updated_at=excluded.updated_at",
                record,
            )

    @staticmethod
    def _forged(row) -> dict:
        got = dict(row)
        for column in ("spec", "verification", "scopes"):
            got[column] = json.loads(got[column] or ("[]" if column == "scopes"
                                                     else "{}"))
        return got

    def get_forged_skill(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM forged_skills WHERE name=?", (name,)).fetchone()
        return self._forged(row) if row else None

    def forged_skills(self, state: str | None = None) -> list[dict]:
        if state:
            rows = self._conn.execute(
                "SELECT * FROM forged_skills WHERE state=? ORDER BY name",
                (state,))
        else:
            rows = self._conn.execute(
                "SELECT * FROM forged_skills ORDER BY name")
        return [self._forged(row) for row in rows]

    def record_forged_transition(self, name: str, from_state: str | None,
                                 to_state: str, reason: str, actor: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO forged_skill_history "
                "  (name, from_state, to_state, reason, actor, at) "
                "VALUES (?,?,?,?,?,?)",
                (name, from_state, to_state, reason, actor, now_iso()),
            )

    def forged_skill_history(self, name: str) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM forged_skill_history WHERE name=? ORDER BY id",
            (name,))]

    # -- executor runs ------------------------------------------------------

    def open_executor_run(
        self, run_id: str, *, executor_type: str, working_directory: str,
        task_bundle: str, project: str = "", worktree_path: str | None = None,
        status: str = "STARTING",
    ) -> None:
        """
        Record a development run before it starts.

        Written first and updated after, never the other way round: a run that
        dies during startup must still leave a row, because a row nobody wrote
        is a run nobody can recover.
        """
        stamp = now_iso()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO executor_runs (run_id, executor_type, project, "
                "working_directory, worktree_path, status, task_bundle, "
                "started_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, "
                "last_seen_at=excluded.last_seen_at, "
                "resume_count=executor_runs.resume_count + 1",
                (run_id, executor_type, project, working_directory,
                 worktree_path, status, task_bundle, stamp, stamp),
            )

    def touch_executor_run(self, run_id: str, **fields) -> None:
        """Heartbeat plus whatever changed. Silent if the run is unknown."""
        allowed = {"session_id", "pid", "pid_started_at", "status", "last_event",
                   "summary", "completion_evidence", "exit_code", "worktree_path",
                   "ended_at"}
        sets = ["last_seen_at=?"]
        values: list[object] = [now_iso()]
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"executor_runs has no updatable column {key!r}")
            sets.append(f"{key}=?")
            values.append(value)
        values.append(run_id)
        with self._tx() as conn:
            conn.execute(
                f"UPDATE executor_runs SET {', '.join(sets)} WHERE run_id=?",
                values)

    def executor_run(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM executor_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def record_promotion(self, run_id: str, *, worktree: str, promotion) -> int:
        """One row per attempt. Superseded attempts stay on record."""
        stamp = now_iso()
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO promotions (run_id, state, worktree, branch, "
                "target_branch, base_commit, result_commit, merge_commit, "
                "rollback_target, reason, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, promotion.state, worktree, promotion.branch,
                 promotion.target, promotion.base_commit, promotion.result_commit,
                 promotion.merge_commit, promotion.rollback_target,
                 promotion.reason, stamp, stamp),
            )
            return int(cur.lastrowid)

    def promotions(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM promotions WHERE run_id=? ORDER BY id", (run_id,))]

    def latest_promotion(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM promotions WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (run_id,)).fetchone()
        return dict(row) if row else None

    def executor_runs(self, *, project: str | None = None,
                      status: str | None = None, limit: int = 20) -> list[dict]:
        sql = "SELECT * FROM executor_runs"
        where, values = [], []
        if project:
            where.append("project=?")
            values.append(project)
        if status:
            where.append("status=?")
            values.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_seen_at DESC LIMIT ?"
        values.append(limit)
        return [dict(r) for r in self._conn.execute(sql, values)]

    # -- objective runs (continuous execution) -------------------------------

    def open_objective_run(
        self, run_id: str, *, request: str, objective_summary: str,
        status: str = "RUNNING",
        lease_executor_id: str | None = None, lease_generation: int = 0,
        lease_expiry: str | None = None, next_wake: str | None = None,
        manual_continue_count: int = 0,
        provenance: str | None = None, attended: bool | None = None,
        envelope: dict | None = None, contract: dict | None = None,
    ) -> None:
        """
        Record an objective run before anything executes.

        Written first and updated after, never the other way round: a run
        that dies during startup must still leave a row, because a row nobody
        wrote is a run nobody can recover. Re-opening an existing run_id
        (a restart reconciling a run it already created) refreshes the row
        while preserving `created_at`.

        Lineage and the envelope are written once: a re-open keeps what the
        run was compiled with, so a restart cannot swap in a wider scope. It
        may fill them where they are missing - a run from before envelopes
        that the person resumes.
        """
        stamp = now_iso()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO objective_runs (run_id, request, objective_summary, "
                "status, lease_executor_id, lease_generation, lease_expiry, "
                "next_wake, manual_continue_count, created_at, updated_at, "
                "provenance, attended, envelope, contract) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "request=excluded.request, "
                "objective_summary=excluded.objective_summary, "
                "status=excluded.status, "
                "lease_executor_id=excluded.lease_executor_id, "
                "lease_generation=excluded.lease_generation, "
                "lease_expiry=excluded.lease_expiry, "
                "next_wake=excluded.next_wake, "
                "manual_continue_count=excluded.manual_continue_count, "
                "updated_at=excluded.updated_at, "
                "provenance=COALESCE(objective_runs.provenance, excluded.provenance), "
                "attended=COALESCE(objective_runs.attended, excluded.attended), "
                "envelope=COALESCE(objective_runs.envelope, excluded.envelope), "
                "contract=COALESCE(objective_runs.contract, excluded.contract)",
                (run_id, request, objective_summary, status, lease_executor_id,
                 lease_generation, lease_expiry, next_wake,
                 manual_continue_count, stamp, stamp, provenance,
                 None if attended is None else int(attended),
                 None if envelope is None else json.dumps(envelope),
                 None if contract is None else json.dumps(contract)),
            )

    def objective_run(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM objective_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        got = dict(row)
        if got.get("summary"):
            got["summary"] = json.loads(got["summary"])
        return got

    def objective_runs_in(self, status: str) -> list[dict]:
        """Every objective run in one status, however many there are."""
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM objective_runs WHERE status=? ORDER BY updated_at",
            (status,))]

    def objective_runs(self, limit: int = 10) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM objective_runs ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )]

    def increment_objective_manual_continue(self, run_id: str) -> bool:
        """Record the live regression metric atomically."""
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE objective_runs SET manual_continue_count = "
                "manual_continue_count + 1, updated_at=? WHERE run_id=?",
                (now_iso(), run_id))
            return cursor.rowcount > 0

    def touch_objective_run(self, run_id: str, **fields) -> None:
        """Heartbeat plus whatever changed. Silent if the run is unknown."""
        allowed = {"status", "lease_executor_id", "lease_generation",
                   "lease_expiry", "next_wake", "manual_continue_count",
                   "finished_at", "summary",
                   # PRD FR-001 objective schema columns
                   "task_class", "risk_tier", "owner_id", "project_scope",
                   "memory_scope", "constraints", "required_capabilities",
                   "retry_budget", "cost_budget_tokens", "time_budget_s",
                   "blocker", "source_channel", "approvals", "evidence"}
        sets = ["updated_at=?"]
        values: list[object] = [now_iso()]
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(
                    f"objective_runs has no updatable column {key!r}")
            if key == "summary" and isinstance(value, dict):
                value = json.dumps(value, default=str)
            if key in ("constraints", "required_capabilities") and \
                    isinstance(value, (list, tuple)):
                value = json.dumps(list(value), default=str)
            sets.append(f"{key}=?")
            values.append(value)
        values.append(run_id)
        with self._tx() as conn:
            conn.execute(
                f"UPDATE objective_runs SET {', '.join(sets)} WHERE run_id=?",
                values)

    def _append_objective_json(self, run_id: str, column: str, item: dict) -> int:
        """Append one record to an append-only JSON list column, atomically.
        Returns the new length. Neither approvals nor evidence may be edited
        in place: a correction is a new record that references the old."""
        if column not in ("approvals", "evidence"):
            raise ValueError(f"not an append-only objective column: {column!r}")
        record = dict(item)
        record.setdefault("at", now_iso())
        with self._tx() as conn:
            row = conn.execute(
                f"SELECT {column} FROM objective_runs WHERE run_id=?",
                (run_id,)).fetchone()
            if row is None:
                raise LookupError(f"no objective run {run_id!r}")
            try:
                items = json.loads(row[0] or "[]")
            except ValueError:
                items = []
            record.setdefault("seq", len(items) + 1)
            items.append(record)
            conn.execute(
                f"UPDATE objective_runs SET {column}=?, updated_at=? WHERE run_id=?",
                (json.dumps(items, default=str), now_iso(), run_id))
            return len(items)

    def append_objective_evidence(self, run_id: str, *, expected: str,
                                  actual: str, method: str, passed: bool,
                                  task_id: str = "", ref: str = "") -> int:
        """FR-052 evidence ledger entry: expected -> actual -> method ->
        pass/fail, timestamped, appended to the objective."""
        return self._append_objective_json(run_id, "evidence", {
            "task_id": task_id, "expected": expected, "actual": actual,
            "method": method, "passed": bool(passed), "ref": ref})

    def append_objective_approval(self, run_id: str, *, operation: str,
                                  target: str, parameters: dict | None,
                                  decision: str, decided_by: str,
                                  nonce: str = "", expires_at: str = "",
                                  plan_hash: str = "", graph_version: int = 0,
                                  blast_radius: str = "") -> int:
        """FR-060 exact-action approval record bound to operation, target,
        parameters, objective and expiry - and, since APPROVAL-001, to the
        plan it was given for (`plan_hash`, `graph_version`) and the blast
        radius of the action (`blast_radius`)."""
        return self._append_objective_json(run_id, "approvals", {
            "operation": operation, "target": target,
            "parameters": dict(parameters or {}), "decision": decision,
            "decided_by": decided_by, "nonce": nonce, "expires_at": expires_at,
            "plan_hash": plan_hash, "graph_version": graph_version,
            "blast_radius": blast_radius})

    def objective_ledger(self, run_id: str) -> dict | None:
        """The PRD 9.2 Objective shape, assembled from the durable rows:
        header, plan steps, workers, approvals, checkpoints (events),
        evidence, budgets, current state, blocker, final result."""
        run = self.objective_run(run_id)
        if run is None:
            return None

        def loads(raw, fallback):
            if isinstance(raw, (list, dict)):
                return raw                      # already decoded by the reader
            try:
                return json.loads(raw) if isinstance(raw, str) and raw else fallback
            except ValueError:
                return fallback

        tasks = self.objective_tasks(run_id)
        events = self.objective_events(run_id)
        workers = sorted({
            str((loads(t.get("result"), {}) or {}).get("worker") or "")
            for t in tasks} - {""})
        return {
            "id": run_id,
            "owner_id": run.get("owner_id") or "owner",
            "created_at": run.get("created_at"),
            "intent": run.get("request"),
            "goal": run.get("objective_summary"),
            "desired_outcome": run.get("objective_summary"),
            "task_class": run.get("task_class") or "",
            "risk_tier": run.get("risk_tier") or "",
            "constraints": loads(run.get("constraints"), []),
            "project_scope": run.get("project_scope") or "",
            "memory_scope": run.get("memory_scope") or "user",
            "required_capabilities": loads(run.get("required_capabilities"), []),
            "plan_steps": [{
                "task_id": t["task_id"], "capability": t["capability"],
                "status": t["status"], "dependencies": loads(t.get("dependencies"), []),
                "attempts": t.get("attempts") or 0,
                "failure_kind": t.get("failure_kind"),
                "evidence": t.get("evidence") or "",
            } for t in tasks],
            "workers": workers,
            "approvals": loads(run.get("approvals"), []),
            "checkpoints": [e for e in events
                            if e.get("event") in ("run.created", "task.succeeded",
                                                  "task.failed", "run.paused",
                                                  "run.resumed", "lease.acquired",
                                                  "continuation.scheduled")],
            "evidence": loads(run.get("evidence"), []),
            "retry_budget": run.get("retry_budget") or 3,
            "cost_budget_tokens": run.get("cost_budget_tokens") or 0,
            "time_budget_s": run.get("time_budget_s") or 0,
            "current_state": run.get("status"),
            "blocker": run.get("blocker") or "",
            "final_result": loads(run.get("summary"), run.get("summary")),
            "source_channel": run.get("source_channel") or "local",
            "event_count": len(events),
        }

    def finish_objective_run(self, run_id: str, *, status: str,
                             summary: dict, finished_at: str | None = None) -> None:
        """The one writer of a run's terminal status.

        COMPLETED is a verdict over the evidence ledger, never a caller's
        word (FR-053; invariant suite A-048). `_finish` in the executor
        checks it, but the executor is one caller: anything that reaches
        this method - a tool, a repair script, a worker handing back "done"
        - is held to the same rule HERE, where the row is written. A
        COMPLETED with a succeeded top-level task that has no passing
        evidence entry is refused with the gap named; the caller decides
        what to do (PARTIAL with the gap, or record the evidence first).
        """
        if status == "COMPLETED":
            missing = self.completion_evidence_gap(run_id)
            if missing:
                raise CompletionRefused(
                    f"run {run_id} cannot be COMPLETED: succeeded task(s) "
                    f"{', '.join(missing[:3])} have no passing evidence entry")
        with self._tx() as conn:
            conn.execute(
                "UPDATE objective_runs SET status=?, summary=?, finished_at=?, "
                "updated_at=?, next_wake=NULL WHERE run_id=?",
                (status, json.dumps(summary, default=str),
                 finished_at or now_iso(), now_iso(), run_id),
            )

    def completion_evidence_gap(self, run_id: str) -> list[str]:
        """Succeeded top-level tasks with no passing evidence row - the
        list `finish_objective_run` refuses COMPLETED over. Composite
        leaves are covered by their group and are not counted."""
        tasks = [t for t in self.objective_tasks(run_id) if not t.get("parent_id")]
        succeeded = [t["task_id"] for t in tasks if t["status"] == "SUCCEEDED"]
        if not succeeded:
            return []
        ledger = self.objective_ledger(run_id) or {}
        backed = {e.get("task_id") for e in ledger.get("evidence", []) if e.get("passed")}
        return [t for t in succeeded if t not in backed]

    def save_objective_task(self, *, task_id: str, run_id: str,
                            capability: str, arguments: str,
                            dependencies: str = "[]", status: str = "QUEUED",
                            attempts: int = 0,
                            failure_kind: str | None = None,
                            evidence: str | None = None,
                            parent_id: str | None = None) -> None:
        """Write one task. Created at compile time, so the plan survives."""
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO objective_tasks (task_id, run_id, status, capability, "
                "arguments, dependencies, parent_id, attempts, failure_kind, "
                "evidence, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET status=excluded.status, attempts=excluded.attempts",
                (task_id, run_id, status, capability, arguments, dependencies,
                 parent_id, attempts, failure_kind, evidence, now_iso()),
            )

    def update_objective_task(self, task_id: str, **fields) -> None:
        allowed = {"status", "attempts", "failure_kind", "result", "evidence",
                   "blocked_by", "next_wake", "started_at", "finished_at",
                   "detail"}
        sets: list[str] = []
        values: list[object] = []
        detail_at = -1
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(
                    f"objective_tasks has no updatable column {key!r}")
            if key == "detail" and isinstance(value, dict):
                detail_at = len(values)          # merged inside the transaction
            elif key == "result" and isinstance(value, dict):
                value = json.dumps(value, default=str)
            sets.append(f"{key}=?")
            values.append(value)
        if not sets:
            return
        values.append(task_id)
        with self._tx() as conn:
            if detail_at >= 0:
                # Merge, never replace. The loop's fingerprint bookkeeping
                # (last_fingerprint, strategy_changes, hypothesis, strategy_hint)
                # is written after a failure and read by the NEXT attempt - and
                # by the next process after a restart. Every other site writes
                # its own keys ({"attempt": ..}, {"attempts": ..}) and a replacing
                # write from the success path erased the history (golden
                # journey, 2026-09-04).
                row = conn.execute(
                    "SELECT detail FROM objective_tasks WHERE task_id=?",
                    (task_id,)).fetchone()
                try:
                    existing = json.loads(row[0]) if row and row[0] else {}
                except (TypeError, ValueError):
                    existing = {}
                if not isinstance(existing, dict):
                    existing = {}
                values[detail_at] = json.dumps({**existing, **values[detail_at]},
                                               default=str)
            conn.execute(
                f"UPDATE objective_tasks SET {', '.join(sets)} WHERE task_id=?",
                values)

    def update_objective_task_if(self, task_id: str, *, expect, **fields) -> bool:
        """
        Update a task only while it is still in one of `expect`. True if it was.

        The check and the write are one statement on purpose. A live graph edit
        races the executor: the boss says "use BBC instead" at the moment the
        driver claims that task, and a read-then-write would rewrite the
        arguments of a capability that is already mid-call - changing what it
        is doing halfway through, with the evidence recorded against the new
        arguments and the work done under the old ones.

        Returning False rather than raising: losing this race is an ordinary
        outcome with a sentence to say about it ("that already started"), not
        an error.
        """
        allowed = {"status", "attempts", "failure_kind", "result", "evidence",
                   "blocked_by", "next_wake", "started_at", "finished_at",
                   "dependencies", "arguments"}
        sets, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(
                    f"objective_tasks has no updatable column {key!r}")
            if isinstance(value, dict):
                value = json.dumps(value, default=str)
            sets.append(f"{key}=?")
            values.append(value)
        if not sets:
            return False
        expected = tuple(expect)
        values.append(task_id)
        values.extend(expected)
        placeholders = ",".join("?" for _ in expected)
        with self._tx() as conn:
            cursor = conn.execute(
                f"UPDATE objective_tasks SET {', '.join(sets)}"
                f" WHERE task_id=? AND status IN ({placeholders})",
                values)
            return cursor.rowcount > 0

    def objective_tasks(self, run_id: str) -> list[dict]:
        rows = []
        for row in self._conn.execute(
            "SELECT * FROM objective_tasks WHERE run_id=? ORDER BY rowid",
            (run_id,),
        ):
            got = dict(row)
            got["dependencies"] = json.loads(got["dependencies"] or "[]")
            got["arguments"] = json.loads(got["arguments"] or "{}")
            if got.get("result"):
                try:
                    got["result"] = json.loads(got["result"])
                except json.JSONDecodeError:
                    pass
            try:
                got["detail"] = json.loads(got.get("detail") or "{}")
            except json.JSONDecodeError:
                got["detail"] = {}
            rows.append(got)
        return rows

    def objective_task(self, task_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM objective_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        got = dict(row)
        got["dependencies"] = json.loads(got["dependencies"] or "[]")
        got["arguments"] = json.loads(got["arguments"] or "{}")
        if got.get("result"):
            try:
                got["result"] = json.loads(got["result"])
            except json.JSONDecodeError:
                pass
        try:
            got["detail"] = json.loads(got.get("detail") or "{}")
        except json.JSONDecodeError:
            got["detail"] = {}
        return got

    def append_objective_event(self, run_id: str, event: str, *,
                               task_id: str | None = None,
                               detail: dict | None = None) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO objective_events (run_id, task_id, event, detail, at) "
                "VALUES (?,?,?,?,?)",
                (run_id, task_id, event,
                 json.dumps(detail, default=str) if detail else None, now_iso()),
            )
            return int(cur.lastrowid)

    def recent_objective_events(self, run_id: str, limit: int = 500) -> list[dict]:
        """The newest `limit` events of a run, oldest first. `objective_events`
        reads from the start, which on a long run misses the recent ones."""
        rows = []
        for row in self._conn.execute(
            "SELECT * FROM (SELECT * FROM objective_events WHERE run_id=? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id", (run_id, limit),
        ):
            got = dict(row)
            if got.get("detail"):
                try:
                    got["detail"] = json.loads(got["detail"])
                except json.JSONDecodeError:
                    pass
            rows.append(got)
        return rows

    def objective_events_named(self, run_id: str, names: tuple[str, ...], *,
                               after_id: int = 0,
                               mentioning: tuple[str, ...] = ()) -> list[dict]:
        """Every event of a run whose name is in `names`, oldest first, however
        old. For the few events that outlive the `recent_objective_events`
        window (a flag the person answered, the resume after it); callers pass
        names that are rare per run, so this is not capped. `after_id` keeps
        only the events after that id (where a window began). `mentioning`
        keeps only those whose detail text contains one of the strings, a
        coarse filter the caller refines, for a name that is common per run
        when the kind of it wanted is rare (the refusals among the failures)."""
        if not names:
            return []
        marks = ",".join("?" * len(names))
        where = f"run_id=? AND id>? AND event IN ({marks})"
        if mentioning:
            where += " AND (" + " OR ".join("instr(detail, ?) > 0" for _ in mentioning) + ")"
        rows = []
        for row in self._conn.execute(
            f"SELECT * FROM objective_events WHERE {where} ORDER BY id",
            (run_id, after_id, *names, *mentioning),
        ):
            got = dict(row)
            if got.get("detail"):
                try:
                    got["detail"] = json.loads(got["detail"])
                except json.JSONDecodeError:
                    pass
            rows.append(got)
        return rows

    def objective_event_counts(self, run_id: str, event: str, after_id: int = 0) -> dict[str, int]:
        """How many `event` rows each task of a run has after `after_id`, however
        many events the run holds. The aggregate for an event too common per run
        to fetch by name (every start of a task); `objective_events_named` is
        for the rare ones."""
        return {row["task_id"]: row["n"] for row in self._conn.execute(
            "SELECT task_id, COUNT(*) AS n FROM objective_events "
            "WHERE run_id=? AND id>? AND event=? AND task_id IS NOT NULL "
            "GROUP BY task_id", (run_id, after_id, event),
        )}

    def objective_events(self, run_id: str, limit: int = 1000, after_id: int = 0) -> list[dict]:
        """A run's events in order; `after_id` resumes after the last row of a prior page."""
        rows = []
        for row in self._conn.execute(
            "SELECT * FROM objective_events WHERE run_id=? AND id>? ORDER BY id "
            "LIMIT ?", (run_id, after_id, limit),
        ):
            got = dict(row)
            if got.get("detail"):
                try:
                    got["detail"] = json.loads(got["detail"])
                except json.JSONDecodeError:
                    pass
            rows.append(got)
        return rows

    # -- objective deliveries: the seam between a finished run and the boss --

    def create_objective_delivery(self, run_id: str, message: str) -> bool:
        with self._tx() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO objective_deliveries "
                "(run_id, message, created_at) VALUES (?,?,?)",
                (run_id, message, now_iso()))
            return cursor.rowcount > 0

    def pending_objective_deliveries(self, limit: int = 100, *,
                                     max_age_s: float = OBJECTIVE_DELIVERY_TTL_S
                                     ) -> list[dict]:
        """
        Finished objectives still worth announcing, oldest first.

        Same guard, same reason, as `hermes_bridge.pending_deliveries`: a
        delivery row carries no room or session, so PENDING means "say this to
        whoever turns up next". Measured on 2026-08-27, fourteen objective
        completions written at 11:09 UTC were recited into a brand-new probe
        room at 17:21 - six hours and twelve minutes later, to a conversation
        that had started none of them.

        Over-age rows become EXPIRED rather than being filtered, so the row
        says why it was never spoken.
        """
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(seconds=max_age_s)).isoformat()
        with self._tx() as conn:
            conn.execute(
                "UPDATE objective_deliveries SET delivery_state='EXPIRED' "
                "WHERE delivery_state='PENDING' AND created_at < ?", (cutoff,))
        return [dict(row) for row in self._conn.execute(
            "SELECT * FROM objective_deliveries WHERE delivery_state='PENDING' "
            "ORDER BY delivery_id LIMIT ?", (limit,))]

    def claim_objective_delivery(self, delivery_id: int) -> bool:
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE objective_deliveries SET delivery_state='DELIVERING' "
                "WHERE delivery_id=? AND delivery_state='PENDING'",
                (delivery_id,))
            return cursor.rowcount > 0

    def release_objective_delivery(self, delivery_id: int) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE objective_deliveries SET delivery_state='PENDING' "
                "WHERE delivery_id=? AND delivery_state='DELIVERING'",
                (delivery_id,))

    def mark_objective_delivered(self, delivery_id: int, *, via: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE objective_deliveries SET delivery_state='DELIVERED', "
                "delivered_at=?, delivered_via=? "
                "WHERE delivery_id=? AND delivery_state='DELIVERING'",
                (now_iso(), via, delivery_id))

    def acquire_objective_lease(self, run_id: str, *, executor_id: str,
                                expiry: str) -> bool:
        """
        Single-writer guard, one UPDATE so two writers cannot both win: only
        the current owner, or anyone once the lease is stale, may take over.
        The generation bump is what stops a double continuation after an
        orphan reconcile.
        """
        with self._tx() as conn:
            now = datetime.now().isoformat()
            cursor = conn.execute(
                "UPDATE objective_runs SET "
                "  lease_executor_id=?, lease_generation=lease_generation+1, "
                "  lease_expiry=?, updated_at=? "
                "WHERE run_id=? AND status NOT IN (?,?,?,?) "
                "  AND (lease_executor_id=? OR lease_expiry IS NULL "
                "       OR lease_expiry < ?)",
                (executor_id, expiry, now_iso(), run_id,
                 "COMPLETED", "PARTIAL", "FAILED", "CANCELLED",
                 executor_id, now),
            )
            return cursor.rowcount > 0

    def release_objective_lease(self, run_id: str, *, executor_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE objective_runs SET lease_executor_id=NULL, "
                "lease_expiry=NULL, updated_at=? WHERE run_id=? "
                "AND lease_executor_id=?",
                (now_iso(), run_id, executor_id),
            )
