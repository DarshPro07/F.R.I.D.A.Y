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
from dataclasses import dataclass, asdict


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
        return cls(
            task_id=record.get("work_run_id", ""),
            agent=record.get("model", "") or record.get("provider", ""),
            status=record.get("status", ""),
            summary=summary,
            next_action=pending_question,
        )
