"""
The privacy-safe training dataset for specialist micro-models (LAB-002).

A micro-model may learn only from what FRIDAY has already proven: an
objective that COMPLETED with passing evidence for every step it claimed,
whose data class is PUBLIC or INTERNAL, and that nobody refused. Anything
more sensitive is dropped and counted, never trusted to redaction. The owner
approves each export, and the set is split once: the evaluation half is
frozen by its sha256, and no later export can alter it or train on anything
that fingerprints like a row in it.

This module reads the history through `Store` and writes files; it owns no
table. The classes are `friday.data_class`'s, the owner's yes is a
`friday.confirmation` record bound to the exact output directory, and the
request fingerprint is `friday.routing_memory.fingerprint` - the same join key
routing memory already uses. A row holds a fingerprint and the capability
sequence, never the request text: what a specialist learns is which steps
solved which shape of request, not what anybody said.

The directory the caller names is the whole export:

    train.jsonl     rows the model may learn from
    eval.jsonl      the frozen evaluation set (written by the first build only)
    manifest.json   each file's sha256 and row count, and what was left out
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from friday import confirmation as CF
from friday import data_class as DC
from friday import observability, routing_memory

#: The classes a micro-model may learn from. Everything else is dropped.
ADMITTED = (DC.PUBLIC, DC.INTERNAL)

APPROVAL_RUN = "lab-dataset"
APPROVAL_ACTION = "lab.dataset_export"

TRAIN = "train.jsonl"
EVAL = "eval.jsonl"
MANIFEST = "manifest.json"
_FILES = (TRAIN, EVAL)

#: One run in this many (by hash of its id) is held out, the first time only.
_EVAL_ONE_IN = 5

_FAILED = ("FAILED", "CANCELLED")


class ExportRefused(Exception):
    """No recorded owner approval for exactly this export."""


class EvalSetChanged(Exception):
    """A build would alter, or found altered, the frozen evaluation set."""


class NotAnExport(Exception):
    """The directory holds no manifest this module wrote."""


class DeletionNotVerified(Exception):
    """A file of the export is still there after it was deleted."""


def approval_target(out_dir: str | Path) -> str:
    """What the owner's yes is bound to: this directory, resolved."""
    return str(Path(out_dir).resolve())


def is_eval_id(run_id: str) -> bool:
    """Whether a run is held out when the evaluation set is first frozen."""
    return int(hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:8], 16) \
        % _EVAL_ONE_IN == 0


# -- reading the history ----------------------------------------------------


def _texts(run: dict, tasks: list[dict], evidence: list[dict]) -> str:
    parts = [run.get("request") or "", run.get("objective_summary") or ""]
    for task in tasks:
        parts += [str(task.get("arguments") or ""), str(task.get("evidence") or "")]
    for item in evidence:
        parts += [str(item.get(k) or "") for k in ("expected", "actual", "method", "ref")]
    return "\n".join(parts)


def _clean(value):
    """Secrets out by token shape and key name, then every class an
    admitted row must not carry out by its own placeholder."""
    value = observability.redact(value)

    def strip(item):
        if isinstance(item, dict):
            return {k: strip(v) for k, v in item.items()}
        if isinstance(item, list):
            return [strip(v) for v in item]
        if isinstance(item, str):
            for cls in DC.CLASSES:
                if cls not in ADMITTED:
                    item, _ = DC.redact(item, cls)
        return item

    return strip(value)


def _approved(decision: str) -> bool:
    return str(decision or "").upper().startswith("APPROVED")


def _verdict(store, run: dict) -> tuple[str, dict | None]:
    """('admit', row) or (reason it stayed out, None). `reason` is one of
    failed, unverified, unapproved, or a data class name."""
    run_id = run["run_id"]
    status = run.get("status")
    if status in _FAILED:
        return "failed", None
    if status != "COMPLETED":
        return "unverified", None
    ledger = store.objective_ledger(run_id) or {}
    evidence = ledger.get("evidence") or []
    first_pass = next((i for i, e in enumerate(evidence, 1) if e.get("passed")), 0)
    if not first_pass or store.completion_evidence_gap(run_id):
        return "unverified", None
    if not all(_approved(a.get("decision")) for a in ledger.get("approvals") or []):
        return "unapproved", None
    tasks = store.objective_tasks(run_id)
    cls = DC.classify(_texts(run, tasks, evidence))
    if cls not in ADMITTED:
        return cls, None
    row = {
        "objective_id": run_id,
        "outcome_evidence_id": f"{run_id}:evidence:{first_pass}",
        "data_class": cls,
        "task_class": run.get("task_class") or "",
        "request_fingerprint": routing_memory.fingerprint(run.get("request") or ""),
        "steps": [{"capability": s["capability"], "status": s["status"]}
                  for s in ledger.get("plan_steps") or []],
        "evidence_method": evidence[first_pass - 1].get("method") or "",
    }
    return "admit", _clean(row)


def _history(store) -> tuple[dict[str, dict], dict]:
    """Every admitted row by objective id, and what was left out and why."""
    admitted: dict[str, dict] = {}
    excluded = {"failed": 0, "unapproved": 0, "unverified": 0}
    dropped: dict[str, int] = {}
    runs = sorted(store.objective_runs(limit=1_000_000), key=lambda r: r["run_id"])
    for run in runs:
        verdict, row = _verdict(store, run)
        if row is not None:
            admitted[run["run_id"]] = row
        elif verdict in excluded:
            excluded[verdict] += 1
        else:
            dropped[verdict] = dropped.get(verdict, 0) + 1
    return admitted, {"excluded": excluded,
                      "dropped_by_class": dict(sorted(dropped.items()))}


# -- files ------------------------------------------------------------------


def _dump(rows: list[dict]) -> bytes:
    return b"".join(
        json.dumps(r, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for r in rows)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _frozen(out: Path) -> list[dict] | None:
    """The evaluation rows already frozen in OUT, or None if none are.
    Refuses a file that is not exactly what the manifest recorded."""
    eval_path, manifest_path = out / EVAL, out / MANIFEST
    if not eval_path.exists() and not manifest_path.exists():
        return None
    try:
        recorded = json.loads(manifest_path.read_text(encoding="utf-8"))["files"][EVAL]["sha256"]
        data = eval_path.read_bytes()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EvalSetChanged(
            f"{out} holds an evaluation set with no readable record of its "
            f"sha256 ({type(exc).__name__}); refusing to overwrite it") from exc
    if _sha(data) != recorded:
        raise EvalSetChanged(
            f"{eval_path} no longer matches the sha256 recorded when it was frozen")
    return [json.loads(line) for line in data.decode("utf-8").splitlines()]


def build(store, out_dir: str | Path, *, approval: str, book=None) -> dict:
    """
    Build the dataset into OUT_DIR and return the manifest.

    Everything that can refuse runs before anything is written or the owner's
    yes is spent: a build that would alter the frozen evaluation set fails and
    leaves the approval usable and the files untouched.
    """
    out = Path(out_dir)
    book = book or CF.book
    admitted, left_out = _history(store)

    frozen = _frozen(out)
    if frozen is None:
        eval_rows = [r for i, r in admitted.items() if is_eval_id(i)]
        eval_bytes = _dump(eval_rows)
    else:
        eval_rows = frozen
        eval_bytes = None
        for row in frozen:
            if admitted.get(row["objective_id"]) != row:
                raise EvalSetChanged(
                    f"frozen evaluation row {row['objective_id']!r} is no longer "
                    "what the history builds; the evaluation set cannot change")
    eval_ids = {r["objective_id"] for r in eval_rows}
    eval_prints = {r["request_fingerprint"] for r in eval_rows}

    train_rows, leaked = [], 0
    for run_id, row in admitted.items():
        if run_id in eval_ids:
            continue
        if row["request_fingerprint"] in eval_prints:
            leaked += 1
            continue
        train_rows.append(row)

    verdict = book.consume(approval, run_id=APPROVAL_RUN, action=APPROVAL_ACTION,
                           target=approval_target(out))
    if not verdict.ok:
        raise ExportRefused(verdict.reason)

    train_bytes = _dump(train_rows)
    if eval_bytes is None:
        eval_bytes = (out / EVAL).read_bytes()
    manifest = {
        "version": 1,
        "files": {TRAIN: {"sha256": _sha(train_bytes), "rows": len(train_rows)},
                  EVAL: {"sha256": _sha(eval_bytes), "rows": len(eval_rows)}},
        "counts": {"admitted": len(admitted), "train": len(train_rows),
                   "eval": len(eval_rows), "rejected_eval_leak": leaked, **left_out},
    }
    out.mkdir(parents=True, exist_ok=True)
    _write(out / TRAIN, train_bytes)
    if frozen is None:
        _write(out / EVAL, eval_bytes)
    _write(out / MANIFEST, (json.dumps(manifest, sort_keys=True, indent=1) + "\n")
           .encode("utf-8"))
    return manifest


def delete_export(out_dir: str | Path) -> dict:
    """
    Delete an export this module wrote, and verify it is gone.

    Only the names the manifest lists, and only if they are this module's own
    file names: a manifest cannot point deletion at anything else. Files the
    caller keeps beside the export are left alone.
    """
    out = Path(out_dir)
    try:
        listed = list(json.loads((out / MANIFEST).read_text(encoding="utf-8"))["files"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise NotAnExport(f"{out} has no readable {MANIFEST}") from exc
    if any(name not in _FILES for name in listed):
        raise NotAnExport(f"{out / MANIFEST} lists files this export never writes")
    names = sorted([*listed, MANIFEST])
    for name in names:
        (out / name).unlink(missing_ok=True)
    left = [name for name in names if (out / name).exists()]
    if left:
        raise DeletionNotVerified(f"still present after deletion: {', '.join(left)}")
    return {"deleted": names, "verified": True}
