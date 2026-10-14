"""
BIN-002: what a Windows EXE or DLL says about itself, read from its bytes.

`binary_analyze` reports the PE headers, section table, import and export
tables and the entry point. The file is opened read-only through the same jail
as `files_read` and is never loaded, mapped as an image, or run. Its bytes are
parsed (pe_parse.py, `struct` only) by pe_worker.py inside an OS-enforced
quarantine (AppContainer, isolation='os', no network): the host never parses
hostile bytes. With no OS boundary the tool fails closed with a typed
`quarantine_error`; it never falls back to parsing in-process. The child's
output is untrusted too: capped, and used only if it is the parser's schema
for this file's sha256 from a clean exit with no denials.

Every finding carries `offset`, where in the file the evidence sits, and the
file's sha256, so a claim can be checked against the bytes. What the file does
not say is not stated: a header that is cut short or contradicts itself is a
typed `unparseable` result with a `reason_code`, and a bad import or export
table keeps the findings already read and reports the table in `errors`.

Strings in a binary (DLL names, function names, section names) are written by
whoever built the file. They are returned as data: printable ASCII only, each
capped at MAX_STRING, and flagged in `note` as untrusted - never an
instruction to follow.

The run is recorded through CYBER-002 (`cyber_lab.record_offline`): tier
offline, intensity passive.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from friday import contracts as c
from friday import cyber_lab, execution, quarantine
from friday.fsjail import JailError
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.files import resolve_for
from friday.toolsets.pe_parse import (MAX_ERRORS, MAX_EXPORTS, MAX_FILE_BYTES,  # noqa: F401
                                      MAX_FUNCS_PER_DLL, MAX_IMPORT_DLLS,
                                      MAX_IMPORT_FUNCTIONS, MAX_SECTIONS, MAX_STRING,
                                      NOTE, REASON_CODES, _unparseable, parse_pe)
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"
PROVIDER = "binary_analyze"
ACTION = "binary_analyze"

#: The parser child's stdout is untrusted; a report bigger than this is refused.
MAX_CHILD_OUTPUT = 4 * 1024 * 1024
_STATUSES = ("ok", "partial", "unparseable")
#: Staged next to the sample in the quarantine; pe_worker imports pe_parse there.
_WORKER_FILES = ("pe_parse.py", "pe_worker.py")
_HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return run.record(c.started(run.run_id, tool_id).finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _read(target) -> tuple[bytes | None, str, int]:
    """(bytes or None if over the cap, sha256, size). Read-only, binary."""
    with open(target, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size <= MAX_FILE_BYTES:
            data = handle.read(MAX_FILE_BYTES + 1)
            return data, hashlib.sha256(data).hexdigest(), len(data)
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
        return None, digest.hexdigest(), size


def _child_report(analysis: quarantine.Analysis, sha: str) -> tuple[dict | None, str]:
    """The parser child's report, or None and why it is refused. Its stdout is
    untrusted: it is used only when it is the parser's report for these bytes."""
    ran = analysis.result
    if analysis.denials:
        return None, f"the parser child was refused {len(analysis.denials)} action(s)"
    if ran.timed_out or ran.exit_code != 0:
        return None, (f"the parser child exited {ran.exit_code}"
                      + (" after timing out" if ran.timed_out else ""))
    out = ran.stdout or ""
    if len(out) > MAX_CHILD_OUTPUT:
        return None, f"the parser child wrote {len(out)} characters, over the {MAX_CHILD_OUTPUT} cap"
    try:
        report = json.loads(out)
    except (ValueError, RecursionError):
        return None, "the parser child's output is not JSON"
    if not (isinstance(report, dict) and report.get("status") in _STATUSES
            and report.get("sha256") == sha and isinstance(report.get("findings"), list)):
        return None, "the parser child's output is not the parser's report for this file"
    return report, ""


def _in_quarantine(data: bytes, sha: str, run_id: str, store, factory) -> tuple[dict, dict]:
    """(report, quarantine block): the bytes parsed by pe_worker inside the OS
    quarantine. Any failure is a typed quarantine_error, never an in-process parse."""
    analysis = None
    try:
        with tempfile.TemporaryDirectory(prefix="friday-pe-") as staging:
            Path(staging, "sample.bin").write_bytes(data)
            for name in _WORKER_FILES:
                shutil.copyfile(_HERE / name, Path(staging, name))
            with factory(store=store, run_id=run_id, isolation="os",
                         max_bytes=MAX_FILE_BYTES + (1 << 20)) as box:
                box.acquire(staging)
                analysis = box.run_python("pe_worker.py", "sample.bin", timeout=30)
    except (quarantine.QuarantineError, execution.ExecutionError, OSError) as exc:
        report, why = None, f"{type(exc).__name__}: {exc}"
    else:
        report, why = _child_report(analysis, sha)
    block = {"tier": analysis.strength if analysis else "", "isolation": "os",
             "denials": [d.as_dict() for d in analysis.denials] if analysis else []}
    if report is None:
        report = {**_unparseable(sha, len(data), "quarantine_error", why, 0),
                  "exit_code": analysis.result.exit_code if analysis else None}
    return report, block


def binary_analyze(run: c.Run, path: str, *,
                   engine: PolicyEngine = default_engine, store=None,
                   quarantine_factory=None) -> c.ActionResult:
    """
    Report a PE file's architecture, sections, imports, exports, entry point and
    hash, without running it. The bytes are parsed inside an OS quarantine
    (`quarantine_factory`, default quarantine.Quarantine, recording to `store`).
    """
    tool_id = "binary.analyze"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        target = resolve_for(run, path)
    except JailError as exc:
        refused = cyber_lab.record_offline(
            PROVIDER, str(path)[:500], ACTION, {}, allowed=False,
            reason=f"path refused: {exc}")
        return run.record(started.finish(
            status=c.FAILED, error=f"path refused: {exc}",
            output={"execution_scope": EXECUTION_SCOPE, "evidence": refused.to_dict()}))
    if not target.is_file():
        return run.record(c.failed(started, f"no such file: {target}"))

    try:
        data, sha, size = _read(target)
    except OSError as exc:
        return run.record(c.failed(started, f"could not read {target.name}: "
                                            f"{type(exc).__name__}: {exc}"))
    box = None
    if data is None:
        report = _unparseable(sha, size, "too_large",
                              f"{size} bytes is over the {MAX_FILE_BYTES} byte read cap", 0)
    else:
        if store is None:
            from friday import forge
            store = forge.store()
        report, box = _in_quarantine(data, sha, run.run_id, store,
                                     quarantine_factory or quarantine.Quarantine)

    evidence = cyber_lab.record_offline(
        PROVIDER, str(target), ACTION,
        {"sha256": report["sha256"], "status": report["status"],
         "findings": len(report["findings"])})
    # Host keys last, so nothing in the child's report can overwrite them.
    output = {**report, "execution_scope": EXECUTION_SCOPE, "path": str(target),
              "evidence": evidence.to_dict()}
    if box is not None:
        output["quarantine"] = box
    return run.record(c.succeeded(
        started,
        output=output,
        verification=c.Verification(
            method="binary_analyze",
            evidence=f"{target.name}: sha256 {report['sha256'][:16]}, {report['status']}, "
                     f"{len(report['findings'])} findings",
        ),
    ))
