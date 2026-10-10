"""
friday/executors/gemini_cli.py -- Gemini CLI as an execution runtime (FAB-002).

`gemini` signed in with the person's Google account ("Login with Google"),
run inside the isolated worktree it is given, edits auto-approved and anything
else left to the CLI's own approval policy, the work packet on stdin.

The supported sign-in is the CLI's own, so this adapter only checks that the
credential file exists; it never opens it, copies it, or hands it to anything
else. An API key is not accepted: a secret never crosses into a worker
(`execution.worker_environment`), so a key-only machine is reported as signed
out rather than quietly running on a path FRIDAY does not control.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from friday.cli_model_provider import AUTH_FAILED, classify_text
from friday.executors.work_packet import (
    DONE, FAILED, UNAVAILABLE, Receipt, WorkPacket, run_cli)

RUNTIME = "gemini-cli"

#: The packet is on stdin (it can exceed the Windows argv limit); `-p` is the
#: short instruction the CLI appends it to.
INSTRUCTION = "Carry out the work packet above."


def _count(value) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


class GeminiCliExecutor:
    """The Gemini CLI doing a work packet."""

    runtime = RUNTIME

    def locate(self) -> str | None:
        found = shutil.which("gemini")
        if found:
            return found
        shim = Path(os.environ.get("APPDATA", "")) / "npm" / "gemini.cmd"
        return str(shim) if os.environ.get("APPDATA") and shim.is_file() else None

    def credentials(self) -> Path:
        return Path.home() / ".gemini" / "oauth_creds.json"

    def signed_in(self) -> tuple[bool, str]:
        if self.credentials().is_file():
            return True, "Google sign-in present"
        return False, "not signed in: run `gemini` and choose Login with Google"

    def work_command(self, path: str) -> list[str]:
        return [path, "--output-format", "json", "--approval-mode", "auto_edit",
                "-p", INSTRUCTION]

    def run(self, packet: WorkPacket, workspace: str) -> Receipt:
        path = self.locate()
        if path is None:
            return Receipt(RUNTIME, UNAVAILABLE, warnings=[
                "the gemini CLI is not installed or not on PATH"])
        signed_in, detail = self.signed_in()
        if not signed_in:
            return Receipt(RUNTIME, UNAVAILABLE, warnings=[detail])
        try:
            code, out, err = run_cli(
                self.work_command(path), prompt=packet.prompt(),
                cwd=str(workspace), timeout_s=packet.budget.seconds,
                worker="gemini-host")
        except TimeoutError as exc:
            return Receipt(RUNTIME, FAILED, warnings=[str(exc)])
        except OSError as exc:
            return Receipt(RUNTIME, UNAVAILABLE, warnings=[
                f"the gemini CLI could not be launched: {exc}"])
        return self.receipt_from(code, out, err)

    @staticmethod
    def _document(out: str) -> dict:
        """The JSON object in stdout; the CLI may print a notice before it."""
        for text in (out, out[out.find("{"):out.rfind("}") + 1]):
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
        return {}

    def receipt_from(self, code: int, out: str, err: str) -> Receipt:
        data = self._document(out)
        stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
        models = stats.get("models") if isinstance(stats.get("models"), dict) else {}
        tokens: dict[str, int] = {}
        for entry in models.values():
            counted = entry.get("tokens") if isinstance(entry, dict) else None
            for key, value in (counted if isinstance(counted, dict) else {}).items():
                tokens[key] = tokens.get(key, 0) + _count(value)

        receipt = Receipt(
            RUNTIME, DONE, model=next(iter(models), ""),
            result=str(data.get("response") or ""), cost_usd=0.0,
            usage={"input_tokens": tokens.get("prompt", 0),
                   "output_tokens": tokens.get("candidates", 0),
                   "cached_tokens": tokens.get("cached", 0),
                   "reasoning_tokens": tokens.get("thoughts", 0)})
        error = data.get("error")
        problem = str(error.get("message") if isinstance(error, dict) else error or "")
        problem = problem or (err if code != 0 else "")
        if not data and not problem:
            problem = "gemini returned output that is not a JSON document"
        if problem or code != 0:
            kind, message = classify_text(problem or f"gemini exited with status {code}")
            receipt.status = UNAVAILABLE if kind == AUTH_FAILED else FAILED
            receipt.warnings.append(message)
        return receipt
