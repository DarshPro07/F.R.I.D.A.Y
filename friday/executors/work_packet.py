"""
friday/executors/work_packet.py -- what a CLI executor owes the manager.

PRD v7 section 46 ("CLI work packets and output contract") fixes one default
output shape for a coding worker, whichever CLI ran it. Each adapter returns a
`contracts.ActionResult` whose `output` is its own: the Claude Code executor
keeps `claude_says` and `changed_files`, the Hermes executor keeps `result`
and `work_run_id`. This module is the one place that reads those and produces
the section-46 dict, so the manager sees the same eight fields from either.

What is deliberately NOT here: a claim the adapter did not make. A field the
adapter cannot supply is empty, and `remaining_risk` says so; it is never
filled with a plausible value.
"""
from __future__ import annotations

from friday import contracts as c

#: PRD v7 section 46, "Output default", in the order the PRD lists them.
OUTPUT_FIELDS = ("status", "result", "files_changed", "tests", "evidence",
                 "artifacts", "blockers", "remaining_risk")

#: "Full narratives go to artifacts, not the manager context."
RESULT_CHARS = 600

#: Where each adapter keeps what the worker said, most specific first.
_SUMMARY_KEYS = ("claude_says", "result")


def output_of(result: c.ActionResult) -> dict:
    """The section-46 output for one executor's terminal `ActionResult`."""
    out = result.output if isinstance(result.output, dict) else {}
    risk: list[str] = []

    summary = next((str(out[key]) for key in _SUMMARY_KEYS if out.get(key)), "")
    if len(summary) > RESULT_CHARS:
        summary = summary[:RESULT_CHARS]
        risk.append(f"the result was cut to {RESULT_CHARS} characters")

    blockers = [text for text in (result.error, out.get("pending_question")) if text]

    if result.verification is None:
        risk.append("no verification was recorded for this run")
    if "changed_files" not in out:
        risk.append("this adapter did not report which files changed")
    # No executor runs the tests itself; `DevelopmentRun.verify` owns that.
    risk.append("no tests were run by the executor")

    return {
        "status": result.status,
        "result": summary,
        "files_changed": [str(path) for path in out.get("changed_files") or ()],
        "tests": [],
        "evidence": [result.verification.evidence] if result.verification else [],
        "artifacts": [artifact.path_or_uri for artifact in result.artifacts],
        "blockers": blockers,
        "remaining_risk": risk,
    }
