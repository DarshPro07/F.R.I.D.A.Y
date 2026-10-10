"""
friday/executors/codex.py -- Codex as an execution runtime (FAB-002).

`codex exec` signed in with the person's own ChatGPT account, run inside the
isolated worktree it is given, in the workspace-write sandbox, the work packet
on stdin. FRIDAY never reads, copies or replays the sign-in: the CLI finds its
own login, so the subscription is used only through the CLI that owns it.

Locating the binary and reading the sign-in state is `CodexBackend`'s job
(MODEL-012); this class adds the one thing that backend refuses to do, which is
let the CLI use tools and write files. A CLI that is missing or signed out
comes back as an UNAVAILABLE receipt, never as another runtime's work.
"""
from __future__ import annotations

import json

from friday.cli_model_provider import AUTH_FAILED, CodexBackend, classify_text
from friday.executors.work_packet import (
    DONE, FAILED, UNAVAILABLE, Receipt, WorkPacket, run_cli)

RUNTIME = "codex"


def _count(usage: dict, key: str) -> int:
    value = usage.get(key)
    return int(value) if isinstance(value, (int, float)) else 0


class CodexExecutor(CodexBackend):
    """The Codex CLI doing a work packet, not answering a prompt."""

    runtime = RUNTIME

    def work_command(self, path: str, model: str) -> list[str]:
        return [path, "exec", "--json", "--ephemeral", "--ignore-user-config",
                "--ignore-rules", "-s", "workspace-write", "-m", model, "-"]

    def run(self, packet: WorkPacket, workspace: str) -> Receipt:
        model = self.default_model
        path = self.locate()
        if path is None:
            return Receipt(RUNTIME, UNAVAILABLE, model=model, warnings=[
                "the codex CLI is not installed or not on PATH"])
        signed_in, detail = self.signed_in()
        if not signed_in:
            return Receipt(RUNTIME, UNAVAILABLE, model=model, warnings=[detail])
        try:
            code, out, err = run_cli(
                self.work_command(path, model), prompt=packet.prompt(),
                cwd=str(workspace), timeout_s=packet.budget.seconds,
                worker="codex-host")
        except TimeoutError as exc:
            return Receipt(RUNTIME, FAILED, model=model, warnings=[str(exc)])
        except OSError as exc:
            return Receipt(RUNTIME, UNAVAILABLE, model=model, warnings=[
                f"the codex CLI could not be launched: {exc}"])
        return self.receipt_from(code, out, err, model)

    def receipt_from(self, code: int, out: str, err: str, model: str) -> Receipt:
        said, usage, failed = "", {}, ""
        warnings: list[str] = []
        events = 0
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            events += 1
            kind = str(event.get("type") or "")
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if kind == "item.completed" and item.get("type") == "agent_message":
                said = str(item.get("text") or "")
            elif kind.startswith("item.") and item.get("type") == "error":
                warnings.append(" ".join(str(item.get("message") or "").split())[:200])
            elif kind == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
            elif kind == "turn.failed":
                error = event.get("error")
                failed = str(error.get("message") if isinstance(error, dict) else error or "")
            elif kind == "error" and not failed:
                failed = str(event.get("message") or "")

        receipt = Receipt(
            RUNTIME, DONE, model=model, result=said, cost_usd=0.0,
            usage={"input_tokens": _count(usage, "input_tokens"),
                   "output_tokens": _count(usage, "output_tokens"),
                   "cached_tokens": _count(usage, "cached_input_tokens"),
                   "reasoning_tokens": _count(usage, "reasoning_output_tokens")},
            warnings=warnings)
        problem = failed or (err if code != 0 else "")
        if not events and not problem:
            problem = "codex returned output that is not JSONL events"
        if problem or code != 0:
            kind, message = classify_text(problem or f"codex exited with status {code}")
            receipt.status = UNAVAILABLE if kind == AUTH_FAILED else FAILED
            receipt.warnings.append(message)
        return receipt
