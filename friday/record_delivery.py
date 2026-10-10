"""
ORCH-004: the deferred delivery carries the bounded work-run record.

A run that outlives hermes_delegate's wait cap reaches FRIDAY later, as a
delivery the broker speaks into the conversation. `hermes_bridge.render_completion`
puts the whole stored result (up to 4000 chars) in that message. The bridge
builds the message and hands it to `WorkRunLog.create_delivery`, so this log
re-renders it there, from the stored row, as the record (status, commit,
tests, evidence references, blockers) instead. The transcript stays in the
work-run row; `hermes_status(work_run_id, full=true)` reads it on request.
"""

from __future__ import annotations

from friday import hermes_bridge as hb
from friday.handoff import work_run_record


def record_body(record: dict) -> str:
    """The record as the few labelled lines a person is told."""
    rec = work_run_record(record)
    lines = [rec["summary"]] if rec["summary"] else []
    for label, key in (("Commit", "commit"), ("Files changed", "files_changed"),
                       ("Tests", "tests"), ("Evidence", "evidence"),
                       ("Blockers", "blockers")):
        value = rec[key]
        if value:
            lines.append(f"{label}: {value if isinstance(value, str) else '; '.join(value)}")
    lines.append(f"Full transcript on request: hermes_status({rec['work_run_id']}, full=true).")
    return "\n".join(lines)


def completion_message(record: dict) -> str:
    """`render_completion`'s framing around the record instead of the result."""
    return hb.render_completion({**record, "result": record_body(record)})


class RecordDeliveryLog(hb.WorkRunLog):
    """A WorkRunLog whose completion deliveries are the record, not the transcript."""

    def create_delivery(self, work_run_id: str, *, goal: str,
                        status: str, message: str) -> str | None:
        record = self.get(work_run_id)
        if record is not None:
            message = completion_message(record)
        return super().create_delivery(work_run_id, goal=goal, status=status,
                                       message=message)
