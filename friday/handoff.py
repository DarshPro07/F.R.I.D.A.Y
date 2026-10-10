"""
S4b: what one delegated task hands back, as data rather than prose.

`render_completion()` already turns a work-run record into the sentence a
person hears; `Handoff` is the structured sibling of that same record - the
fields a parent (continuous.py, a digest, a future review pass) can read
without re-parsing a transcript. Built ONLY from what the work-run record
and the in-memory progress dict actually contain - an empty list here means
"nothing observed", never "nothing happened".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict

#: ORCH-004: what a worker's result costs FRIDAY. The record below is read
#: into a parent's context on every delegation; the full result (up to 4000
#: chars) stays in the work-run row, read on demand through hermes_status.
RECORD_TOKEN_CAP = 400

_ITEM_CHARS = 120
_ITEMS = 5
_CAUSE_CHARS = 300
_REPORT_LABELS = {
    "commit": "commit", "files changed": "files", "tests": "tests",
    "tests run": "tests", "evidence": "evidence", "blockers": "blockers",
    "root cause": "root_cause", "repair": "repair",
}
_LABELLED = re.compile(r"^\s*(status|commit|files changed|tests run|tests|"
                       r"evidence|blockers|root cause|repair)\s*:\s*(.*)$", re.I)
_NOTHING = {"", "none", "n/a", "na", "-", "no", "nothing"}
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


def _parse_report(text: str) -> tuple[dict[str, list[str] | str], str]:
    """The labelled lines of a worker report (TaskBundle.REPORTING_CONTRACT)
    and the prose left over. Only what the worker wrote is returned - a label
    it did not write stays empty, never guessed."""
    from friday.brain import _sensitive
    found: dict[str, list[str] | str] = {
        "commit": "", "files": [], "tests": [], "evidence": [], "blockers": [],
        "root_cause": "", "repair": ""}
    prose: list[str] = []
    for line in (text or "").splitlines():
        m = _LABELLED.match(line)
        if m is None:
            prose.append(line)
            continue
        key = _REPORT_LABELS.get(m.group(1).lower())
        value = m.group(2).strip()
        if key is None or value.lower().rstrip(".") in _NOTHING:
            continue
        if key == "commit":
            sha = _SHA.search(value)
            found["commit"] = sha.group(0) if sha else ""
            continue
        if key in ("root_cause", "repair"):
            found[key] = "" if _sensitive(value) else value[:_CAUSE_CHARS]
            continue
        parts = value.split("," if key == "files" else ";")
        found[key] = [p.strip()[:_ITEM_CHARS] for p in parts
                      if p.strip() and not _sensitive(p)][:_ITEMS]
    return found, "\n".join(prose).strip()


@dataclass
class Handoff:
    task_id: str
    agent: str = ""
    role: str = ""
    status: str = ""
    summary: str = ""
    files_read: tuple[str, ...] = ()
    files_changed: tuple[str, ...] = ()
    tests_run: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    failed_attempts: tuple[str, ...] = ()
    residual_risks: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    memory_candidates: tuple[str, ...] = ()
    skill_candidates: tuple[str, ...] = ()
    next_action: str = ""
    commit: str = ""
    evidence_refs: tuple[str, ...] = ()
    #: Where the full transcript lives (the Hermes stored session id); read
    #: on demand, never carried in the record.
    transcript_ref: str = ""
    #: FAIL-001: what the worker established about a failure and what fixed
    #: it, from the report's `Root cause:` / `Repair:` lines. Both empty means
    #: the failure was not root caused, not that it had no cause.
    root_cause: str = ""
    repair: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "Handoff":
        data = json.loads(text)
        return cls(**{k: (tuple(v) if isinstance(v, list) else v)
                       for k, v in data.items()})

    @classmethod
    def from_work_run(cls, record: dict, progress: dict | None = None) -> "Handoff":
        """
        Build a Handoff from a `hermes_work_runs` row and its live progress
        dict (`HermesSupervisor._progress[work_run_id]`).

        Summary reuses `render_completion` + the same secret guard
        `_write_outcome` already applies, so a Handoff can never carry what
        the memory writer itself would refuse.

        # ponytail: the progress dict only keeps the LAST tool line, not a
        # path history, so files_read/files_changed stay empty rather than
        # guessed from a truncated filename. Add a path list to
        # HermesSupervisor._progress if a real file trail is needed later.
        """
        from friday.hermes_bridge import render_completion
        from friday.brain import _sensitive

        progress = progress or {}
        summary = render_completion(record)[:600]
        if _sensitive(summary):
            summary = ""
        pending_question = (record.get("pending_question") or "").strip()
        report, _ = _parse_report(record.get("result") or "")
        return cls(
            task_id=record.get("work_run_id", ""),
            agent=record.get("model", "") or record.get("provider", ""),
            status=record.get("status", ""),
            summary=summary,
            next_action=pending_question,
            files_changed=tuple(report["files"]),
            tests_run=tuple(report["tests"]),
            blockers=tuple(report["blockers"]),
            commit=report["commit"],
            evidence_refs=tuple(report["evidence"]),
            transcript_ref=(record.get("hermes_stored_session_id")
                            or record.get("hermes_session_id") or ""),
            root_cause=report["root_cause"],
            repair=report["repair"],
        )


def work_run_record(record: dict) -> dict:
    """The compact record FRIDAY receives for one worker: status, commit,
    tests, evidence references, blockers - at most RECORD_TOKEN_CAP tokens
    however long the transcript. Trimming drops list tails and prose, never
    the status, commit or transcript reference."""
    from friday.brain import _sensitive
    h = Handoff.from_work_run(record)
    _, prose = _parse_report(record.get("result") or "")
    out: dict = {
        "work_run_id": h.task_id, "status": h.status, "commit": h.commit,
        "tests": list(h.tests_run), "evidence": list(h.evidence_refs),
        "blockers": list(h.blockers), "files_changed": list(h.files_changed),
        "summary": "" if _sensitive(prose) else prose[:300],
        "transcript_ref": h.transcript_ref, "next_action": h.next_action[:200],
    }
    while len(json.dumps(out)) // 4 > RECORD_TOKEN_CAP:
        lists = [k for k in ("tests", "evidence", "blockers", "files_changed")
                 if out[k]]
        if len(out["summary"]) > 60:
            out["summary"] = out["summary"][:len(out["summary"]) // 2]
        elif lists:
            out[max(lists, key=lambda k: len(json.dumps(out[k])))].pop()
        elif out["next_action"]:
            out["next_action"] = out["next_action"][:len(out["next_action"]) // 2]
        else:
            out["summary"] = ""
            break
    return out


def capture_failure(handoff: Handoff, genome=None) -> dict:
    """FAIL-001: a FAILED or BLOCKED handoff that names its root cause and
    its repair becomes a Failure Genome pattern, with nobody calling
    `failure_capture`.

    The genome's rule stands: a failure with no root cause or no repair is a
    bug report, so it is skipped, not stored. The symptom is what stopped the
    worker (its blockers), else the summary; `failed_attempts` is the failed
    approach. Secret refusal stays in `FailureGenome.capture`. The handoff's
    fields are untrusted worker output: stored as data, marked
    `SOURCE_WORKER`, insert-only (a restatement of a record already on file
    is skipped, never overwritten), and only when a test or evidence backs
    the repair.
    """
    status = (handoff.status or "").strip().lower()
    if status not in ("failed", "blocked"):
        return {"status": "skipped", "missing": [],
                "note": f"handoff status {handoff.status!r} is not a failure"}
    missing = [name for name in ("root_cause", "repair")
               if not (getattr(handoff, name) or "").strip()]
    if missing:
        return {"status": "skipped", "missing": missing,
                "note": "the worker did not report a root cause and a repair"}
    if not (handoff.tests_run or handoff.evidence_refs):
        return {"status": "skipped", "missing": ["tests_run or evidence_refs"],
                "note": "the worker named no test or evidence for the repair"}
    from friday.failure_genome import SOURCE_WORKER, FailureGenome, FailureRecord
    symptom = "; ".join(handoff.blockers) or handoff.summary
    return (genome or FailureGenome()).capture(FailureRecord(
        symptom=symptom, root_cause=handoff.root_cause, repair=handoff.repair,
        failed_approach="; ".join(handoff.failed_attempts),
        regression_test="; ".join(handoff.tests_run),
        source=SOURCE_WORKER), replace=False)
