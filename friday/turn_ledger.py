"""
The live evidence ledger (ACT-001): what a turn actually did, as a Run.

`honesty.audit` judges a sentence against a `contracts.Run`. Both live paths
(the LiveKit voice agent and the browser brain) call tools that hand back a
dict or text, never a Run, so until now the audit existed only in the library and the
product gated on a single "something acted" flag. A flag cannot tell
`files_write foo.txt` from "I created bar.txt", and cannot tell a recorded
failed attempt from an invented one.

One ledger per turn. Every capability call is recorded as an ActionResult
carrying the full correlation key (workspace, objective, version, action,
target, executor, operation, timestamp); the Run declares the first three, so
`audit` holds every result to them without being asked. `refusal` is the one
call both paths make on the sentence about to be spoken.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from friday import contracts as c
from friday import honesty

#: Argument names whose value is the thing a call acted on, in preference order.
_TARGET_KEYS = ("path", "file", "filename", "url", "target", "destination",
                "name", "to", "recipient")

_WORKSPACE = str(Path(__file__).resolve().parents[1])

#: Proof tokens a tool's own answer can carry. Copied into the evidence only
#: when the tool returned them; never synthesised.
_PROOF_RE = re.compile(
    r"\b(?:sha256|message_id|post_state|diff_files|tests_passed|readback)"
    r"\s*[=:]\s*[A-Za-z0-9_.\-]+", re.I)


def _version() -> str:
    try:
        from importlib import metadata
        return metadata.version("friday")
    except Exception:  # noqa: BLE001 - an uninstalled tree still has a version key
        return "source-tree"


def target_of(arguments) -> str:
    """The one thing these arguments say the call acts on, or ''."""
    if not isinstance(arguments, dict):
        return ""
    for key in _TARGET_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class TurnLedger:
    """Everything one turn did, recorded as it happens."""

    def __init__(self, *, executor: str, turn_id: str = "") -> None:
        run_id = c.new_run_id()
        self.executor = executor
        self.run = c.Run(
            run_id=run_id, request=turn_id or run_id,
            correlation=c.Correlation(workspace=_WORKSPACE,
                                      objective=turn_id or run_id,
                                      version=_version()))

    def record(self, capability: str, arguments=None, result=None, *,
               error: str = "", operation: str = "") -> c.ActionResult:
        """
        Record one finished call from what the tool itself returned.

        `result` is the raw return - a dict, or JSON text of one - never a
        summary of it. A typed result (`ActionResult.to_dict()`) keeps its own
        status, error, verification and artifacts; a dict that reports an
        error is a failure; anything else succeeded, with evidence drawn only
        from what came back. A verification is never invented: a success that
        brings none is PARTIAL, and cannot back a claim.
        """
        now = c.now_iso()
        raw = _parsed(result)
        output = raw.get("output") if _typed_status(raw) else raw
        corr = c.Correlation(
            workspace=_WORKSPACE, objective=self.run.correlation.objective,
            version=self.run.correlation.version, action=capability,
            target=target_of(arguments) or _path_of(output), executor=self.executor,
            operation=operation or capability, timestamp=now)
        common = dict(run_id=self.run.run_id, tool_id=capability,
                      started_at=now, completed_at=now, correlation=corr)
        if error:
            res = c.ActionResult(status=c.FAILED, error=error[:300], **common)
        elif _typed_status(raw):
            res = self._typed(raw, capability, common)
        elif _reports_failure(raw):
            res = c.ActionResult(
                status=c.FAILED, output=raw,
                error=str(raw.get("error") or "tool reported it may not claim "
                          "completion")[:300], **common)
        else:
            res = _untyped(result, raw, common)
        self.run.results.append(res)
        return res

    def _typed(self, raw: dict, capability: str, common: dict) -> c.ActionResult:
        """The tool's own ActionResult, re-homed onto this turn's run."""
        status = raw["status"]
        error = str(raw.get("error") or "")[:300]
        if status == c.FAILED and not error.strip():
            error = "tool reported failed"
        verification = _verification_of(raw.get("verification"))
        if status in c.CLAIMABLE and (verification is None
                                      or raw.get("may_claim_completion") is False):
            status = c.PARTIAL
        artifacts = () if verification is None else tuple(
            c.new_artifact(run_id=self.run.run_id, type=a["type"],
                           title=str(a.get("title") or a["path_or_uri"]),
                           path_or_uri=str(a["path_or_uri"]),
                           producer=str(a.get("producer") or capability),
                           verification=verification)
            for a in raw.get("artifacts") or ()
            if isinstance(a, dict) and a.get("type") in c.ARTIFACT_TYPES
            and str(a.get("path_or_uri") or "").strip())
        effects = raw.get("side_effects")
        return c.ActionResult(
            status=status, output=raw.get("output"), error=error or None,
            artifacts=artifacts, verification=verification,
            side_effects=tuple(str(s) for s in effects if s)
            if isinstance(effects, (list, tuple)) else (),
            **common)

    def audit(self, text: str) -> honesty.ClaimAudit:
        return honesty.audit(text, self.run, strict_proof=True)


def _parsed(result) -> dict:
    """The result as a dict when it is one or JSON text of one, else {}."""
    if isinstance(result, dict):
        return result
    if isinstance(result, str) and result.lstrip().startswith("{"):
        try:
            value = json.loads(result)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _typed_status(raw: dict) -> bool:
    return raw.get("status") in c.ACTION_STATUSES


def _reports_failure(raw: dict) -> bool:
    return bool(raw.get("error")) or raw.get("may_claim_completion") is False


def _path_of(output) -> str:
    if isinstance(output, dict) and isinstance(output.get("path"), str):
        return output["path"].strip()
    return ""


def _verification_of(value) -> c.Verification | None:
    """The tool's own Verification, or None if it did not send a complete one."""
    if not isinstance(value, dict):
        return None
    method = str(value.get("method") or "")
    evidence = str(value.get("evidence") or "")
    if not (method.strip() and evidence.strip()):
        return None
    checked_at = value.get("checked_at")
    if isinstance(checked_at, str) and checked_at:
        return c.Verification(method=method, evidence=evidence, checked_at=checked_at)
    return c.Verification(method=method, evidence=evidence)


def _untyped(result, raw: dict, common: dict) -> c.ActionResult:
    """A call that returned something other than an ActionResult."""
    full = json.dumps(raw, default=str) if raw else str(result or "")
    text = full[:300]
    proof = " ".join(dict.fromkeys(_PROOF_RE.findall(full)))
    scalars = " ".join(f"{k}={v}" for k, v in raw.items()
                       if isinstance(v, (str, int, float, bool)) and str(v).strip())
    evidence = f"{proof} {scalars} {text}".strip()
    if not evidence:
        return c.ActionResult(status=c.PARTIAL, output={"result": text}, **common)
    return c.ActionResult(
        status=c.SUCCEEDED, output={"result": text},
        verification=c.Verification(method="tool_return", evidence=evidence),
        **common)


def refusal(text: str, ledger: TurnLedger | None) -> str | None:
    """The correction to say instead of `text`, or None to let it through."""
    if ledger is None or not text:
        return None
    verdict = ledger.audit(text)
    if verdict.ok:
        return None
    return ("I can't stand behind that, boss - " + verdict.reason +
            ". " + honesty.safe_alternative(ledger.run))
