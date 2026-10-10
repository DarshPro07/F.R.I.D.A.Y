"""
friday/executors/claude_packet.py -- Claude Code as a runtime for work packets
(FAB-002).

`ClaudeCodeExecutor` already runs a `TaskBundle` through the supported
`claude` sign-in, with its envelope, broker and verification. This is the
adapter that gives it the same two-argument shape the Codex and Gemini
runtimes have (`run(packet, workspace) -> Receipt`), so a coordinator can drive
all of them alike. It adds no launcher of its own.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace

from friday import contracts as c
from friday.cli_model_provider import ClaudeCodeBackend
from friday.executors import cli
from friday.executors.claude_code import ClaudeCodeExecutor
from friday.executors.work_packet import (
    DONE, FAILED, UNAVAILABLE, Receipt, WorkPacket)

RUNTIME = "claude-code"


class ClaudeCodeRuntime:
    runtime = RUNTIME

    def __init__(self, executor: ClaudeCodeExecutor,
                 backend: ClaudeCodeBackend | None = None) -> None:
        self.executor = executor
        self.backend = backend or ClaudeCodeBackend()

    def run(self, packet: WorkPacket, workspace: str) -> Receipt:
        model = self.executor.model or ""
        if self.backend.locate() is None:
            return Receipt(RUNTIME, UNAVAILABLE, model=model, warnings=[
                "the claude CLI is not installed or not on PATH"])
        signed_in, detail = self.backend.signed_in()
        if not signed_in:
            return Receipt(RUNTIME, UNAVAILABLE, model=model, warnings=[detail])

        # The coordinator already made the isolated worktree; a second one
        # inside it would hide the work from the manager that integrates it.
        bundle = replace(packet.bundle, workspace=str(workspace), isolate=False)
        try:
            result = asyncio.run(self.executor.execute(
                bundle, timeout=packet.budget.seconds))
        except TimeoutError as exc:
            return Receipt(RUNTIME, FAILED, model=model, warnings=[str(exc)])
        except cli.ExecutorUnavailable as exc:
            return Receipt(RUNTIME, UNAVAILABLE, model=model, warnings=[str(exc)])

        out = result.output if isinstance(result.output, dict) else {}
        done = result.status == c.SUCCEEDED
        cost = out.get("cost_usd")
        return Receipt(
            RUNTIME, DONE if done else FAILED, model=model,
            result=str(out.get("claude_says") or ""),
            cost_usd=float(cost) if isinstance(cost, (int, float)) else 0.0,
            usage={"turns": out["turns"]} if out.get("turns") is not None else {},
            warnings=[] if done or not result.error else [str(result.error)])
