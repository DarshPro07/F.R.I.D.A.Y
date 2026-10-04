"""A signed-in coding-agent CLI as a model-gateway transport (MODEL-012).

On a machine where no API key works, the person is usually still signed in to
a coding agent (Claude Code, Codex). That sign-in can be FRIDAY's brain: the
CLI is run as a pure text completion and its answer comes back to the gateway
like any other model reply.

This is a second transport NEXT TO the Hermes worker, not a part of Hermes
(external, untouched). `ModelGateway` dispatches a provider id it was given a
backend for to `complete()` here, and every reply keeps the worker's
`{ok, result | error}` shape, so the failover loop, telemetry and provider
health are reused as they are.

What "pure completion" means here, and what the tests pin:

* no tools (`--tools ""`), no MCP servers, no slash commands, no settings or
  hooks, no session file, one turn;
* a scratch working directory, so no project file is loaded as context;
* the prompt travels on stdin, never in argv; the only argv value that comes
  from outside this module is the model name, and it is validated first;
* the answer is returned as a string and nothing else. Nothing in this module
  or the gateway executes, evals or shell-interprets it. If the CLI reports it
  tried to use a tool anyway, that is a warning on the result, not an action.

A backend implements `locate`, `signed_in`, `command`, `parse` and
`classify_text`; `complete` is shared. Claude Code is the first; Codex and
Antigravity plug in behind the same interface.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("friday.cli_model_provider")

#: The `route_kind` the inventory advertises, next to api / subscription /
#: free_tier / local.
ROUTE_KIND = "cli"

#: Env var naming the backends the process-wide gateway may use, comma
#: separated; "off" disables the CLI transport entirely.
ENABLE_ENV = "FRIDAY_CLI_MODEL_PROVIDERS"

#: How long the "am I signed in" check may take. It is a local read, not a
#: model call.
SIGN_IN_CHECK_TIMEOUT_S = 20.0

# Error codes: the gateway's / provider_health's vocabulary.
AUTH_FAILED = "AUTH_FAILED"
INSUFFICIENT_CREDIT = "INSUFFICIENT_CREDIT"
RATE_LIMITED = "RATE_LIMITED"
PROVIDER_ERROR = "PROVIDER_ERROR"

_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,63}$")

#: Variables that would change WHICH identity the CLI answers as, or bind the
#: child to the session that launched it. A stale ANTHROPIC_API_KEY in the
#: environment would otherwise mask the sign-in this transport exists to use.
_SCRUB_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE",
              "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT",
              "HERMES_SESSION_ID", "HERMES_AGENT", "HERMES_TURN_LEASE_TIMEOUT",
              "HERMES_KANBAN_BOARD", "HERMES_SESSION_SOURCE",
              "HERMES_TUI_SIDECAR_URL", "HERMES_INTERACTIVE",
              "HERMES_MAX_ITERATIONS")

_AUTH_TEXT = re.compile(
    r"not logged in|please run /login|invalid api key|invalid x-api-key|"
    r"unauthori[sz]ed|authentication|oauth token|token (?:has )?expired|"
    r"sign(?:ed)?[ -]?in required|please sign in|\b401\b", re.I)
_CREDIT_TEXT = re.compile(r"credit balance|insufficient credit|billing", re.I)
_RATE_TEXT = re.compile(
    r"rate limit|usage limit|limit reached|too many requests|\b429\b", re.I)

_SYSTEM_PROMPT = (
    "You are the language model behind another assistant. The input is a "
    "transcript of that assistant's conversation; lines that start with "
    "System: are its instructions. Write only the assistant's next reply, as "
    "plain text. You have no tools and cannot run anything.")


class CliUnavailable(RuntimeError):
    """The CLI cannot be run at all (not installed, cannot be launched). The
    gateway turns this into `GatewayUnavailable` and moves on to the next
    route."""


class CliFailure(RuntimeError):
    """The CLI ran and did not give an answer. `code` is the gateway's
    failure vocabulary, so health and failover treat it like any provider
    error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Completion:
    """One CLI answer, in the units the gateway records."""

    text: str
    model: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_result(self, provider: str) -> dict:
        """The shape `hermes_model_gateway_worker` replies with, plus cost."""
        return {"status": "ok", "provider": provider, "model": self.model,
                "response": self.text, "finish_reason": "stop",
                "latency_ms": self.latency_ms, "cost_usd": self.cost_usd,
                "warnings": list(self.warnings),
                "usage": {"input_tokens": self.input_tokens,
                          "output_tokens": self.output_tokens,
                          "cached_tokens": self.cached_tokens,
                          "reasoning_tokens": self.reasoning_tokens}}


def render_prompt(messages: list[dict]) -> str:
    """The compiled context as ONE text prompt for stdin.

    A lone user message goes through untouched; anything with a system part or
    history becomes a labelled transcript, and `_SYSTEM_PROMPT` tells the CLI
    to write only the next reply. The system text rides on stdin, not on
    `--system-prompt`: a compiled persona can exceed the Windows argv limit.
    """
    turns = [(str(m.get("role") or "user"), str(m.get("content", ""))) for m in messages]
    if len(turns) == 1 and turns[0][0] == "user":
        return turns[0][1]
    label = {"system": "System", "user": "User", "assistant": "Assistant"}
    lines = [f"{label.get(role, role.title())}: {text}" for role, text in turns]
    lines.append("Assistant:")
    return "\n\n".join(lines)


def classify_text(text: str) -> tuple[str, str]:
    """(code, message) for a CLI error text. Auth first: a signed-out CLI says
    so in words that also mention limits and billing."""
    message = " ".join(str(text or "").split())[:500] or "the CLI reported an error"
    if _AUTH_TEXT.search(message):
        return AUTH_FAILED, message
    if _CREDIT_TEXT.search(message):
        return INSUFFICIENT_CREDIT, message
    if _RATE_TEXT.search(message):
        return RATE_LIMITED, message
    return PROVIDER_ERROR, message


def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _SCRUB_ENV}


def _scratch_dir() -> str:
    path = Path(tempfile.gettempdir()) / "friday-cli-model"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


class CliBackend:
    """A coding-agent CLI the gateway can think through."""

    id = ""
    label = ""
    default_model = ""
    #: Models the backend accepts by name; the capability registry declares
    #: each (tools=False) and `stale_declarations` checks them against this.
    models: tuple[str, ...] = ()

    # -- what a backend supplies ---------------------------------------------

    def locate(self) -> str | None:
        raise NotImplementedError

    def signed_in(self) -> tuple[bool, str]:
        """(signed in, detail). Unknown is True with a detail saying so: the
        first call classifies a sign-out properly, and refusing the route on a
        guess would be the lie."""
        raise NotImplementedError

    def command(self, path: str, model: str) -> list[str]:
        raise NotImplementedError

    def parse(self, stdout: str, *, model: str) -> Completion:
        raise NotImplementedError

    # -- shared --------------------------------------------------------------

    def entry(self) -> dict:
        """This backend as a provider-inventory row. `authenticated` is what
        the gateway offers as usable: on PATH AND signed in."""
        path = self.locate()
        if path is None:
            return self._row(False, "not installed or not on PATH")
        ok, detail = self.signed_in()
        return self._row(ok, detail)

    def _row(self, authenticated: bool, detail: str) -> dict:
        return {"id": self.id, "label": self.label, "aliases": [],
                "authenticated": authenticated, "route_kind": ROUTE_KIND,
                "default_model": self.default_model, "detail": detail}

    def complete(self, messages: list[dict], *, model: str = "",
                 timeout_s: float = 60.0) -> Completion:
        model = model or self.default_model
        if not _MODEL_NAME.match(model):
            raise CliFailure("BAD_REQUEST", f"{model[:40]!r} is not a model name")
        path = self.locate()
        if path is None:
            raise CliUnavailable(f"the {self.id} CLI is not installed or not on PATH")
        argv = self.command(path, model)
        prompt = render_prompt(messages)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv, input=prompt, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout_s,
                cwd=_scratch_dir(), env=_child_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise CliFailure(PROVIDER_ERROR,
                             f"{self.id} did not answer within {timeout_s:.0f}s") from None
        except OSError as exc:
            raise CliUnavailable(f"the {self.id} CLI could not be launched: {exc}") from exc
        elapsed = int((time.monotonic() - started) * 1000)
        if proc.returncode != 0 and not (proc.stdout or "").strip():
            code, message = classify_text(proc.stderr)
            raise CliFailure(code, message)
        completion = self.parse(proc.stdout, model=model)
        if proc.returncode != 0:
            completion.warnings.append(f"{self.id} exited with status {proc.returncode}")
        completion.latency_ms = elapsed
        return completion


class ClaudeCodeBackend(CliBackend):
    """`claude -p`: Claude Code, signed in with the person's own account."""

    id = "claude-cli"
    label = "Claude Code (signed-in CLI)"
    default_model = "sonnet"
    models = ("sonnet", "opus", "haiku")

    def locate(self) -> str | None:
        # One place that knows where the installer puts claude when the
        # service PATH does not (executors/cli.py).
        from friday.executors import cli
        return cli.claude_path()

    def signed_in(self) -> tuple[bool, str]:
        path = self.locate()
        if path is None:
            return False, "not installed or not on PATH"
        try:
            proc = subprocess.run(
                self.status_command(path), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=SIGN_IN_CHECK_TIMEOUT_S,
                cwd=_scratch_dir(), env=_child_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return True, f"sign-in not verified: {type(exc).__name__}"
        try:
            doc = json.loads(proc.stdout)
        except ValueError:
            doc = None
        if isinstance(doc, dict) and "loggedIn" in doc:
            if doc["loggedIn"]:
                return True, f"signed in ({doc.get('authMethod') or 'claude'})"
            return False, "signed out: run `claude` and sign in"
        return True, "sign-in not verified: `claude auth status` gave no verdict"

    def status_command(self, path: str) -> list[str]:
        """A local read of the sign-in state; it makes no model call."""
        return [path, "auth", "status"]

    def command(self, path: str, model: str) -> list[str]:
        return [path, "-p", "--output-format", "json", "--max-turns", "1",
                "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
                "--no-session-persistence", "--setting-sources", "",
                "--model", model, "--system-prompt", _SYSTEM_PROMPT]

    def parse(self, stdout: str, *, model: str) -> Completion:
        try:
            doc = json.loads(stdout)
        except ValueError:
            raise CliFailure(PROVIDER_ERROR, "claude returned output that is not JSON") from None
        if isinstance(doc, list):
            doc = next((d for d in reversed(doc)
                        if isinstance(d, dict) and d.get("type") == "result"), None)
        if not isinstance(doc, dict):
            raise CliFailure(PROVIDER_ERROR, "claude returned no result object")
        result = doc.get("result")
        if doc.get("is_error"):
            code, message = classify_text(result if isinstance(result, str) else "")
            raise CliFailure(code, message)
        usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
        details = usage.get("output_tokens_details")
        details = details if isinstance(details, dict) else {}
        served = doc.get("modelUsage")
        served_model = next(iter(served)) if isinstance(served, dict) and len(served) == 1 else model
        warnings = []
        denied = doc.get("permission_denials")
        if denied:
            warnings.append(f"the CLI asked to use {len(denied)} tool(s); none was run")
        if int(doc.get("num_turns") or 1) > 1:
            warnings.append(f"the CLI took {doc.get('num_turns')} turns despite --max-turns 1")
        return Completion(
            text=result if isinstance(result, str) else "",
            model=str(served_model),
            cost_usd=_number(doc.get("total_cost_usd")),
            # The CLI splits the prompt into fresh / cache-written / cache-read
            # tokens; the budget guard needs the size of the whole prompt.
            input_tokens=sum(int(usage.get(k) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
            output_tokens=int(usage.get("output_tokens") or 0),
            cached_tokens=int(usage.get("cache_read_input_tokens") or 0),
            reasoning_tokens=int(details.get("thinking_tokens") or 0),
            warnings=warnings)


def _number(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number >= 0 else 0.0


#: Every backend this module knows, by the name `ENABLE_ENV` uses.
BACKENDS: dict[str, type[CliBackend]] = {"claude": ClaudeCodeBackend}

#: Enabled when `ENABLE_ENV` is unset: the one backend that is built and proven.
DEFAULT_ENABLED = ("claude",)


def known_models() -> dict[str, tuple[str, ...]]:
    """provider id -> the model names each backend accepts, for the capability
    registry's staleness check."""
    return {cls.id: cls.models for cls in BACKENDS.values()}


def discover_backends(env: dict | None = None) -> tuple[CliBackend, ...]:
    """The backends the process-wide gateway should offer. Being listed is not
    being usable: the gateway still asks each one if it is installed and
    signed in before routing to it."""
    raw = (env if env is not None else os.environ).get(ENABLE_ENV)
    if raw is None:
        names = DEFAULT_ENABLED
    else:
        names = tuple(n.strip().lower() for n in raw.split(",") if n.strip())
        if names == ("off",):
            return ()
    unknown = [n for n in names if n not in BACKENDS]
    if unknown:
        logger.warning("%s names no known backend: %s", ENABLE_ENV, ", ".join(unknown))
    return tuple(BACKENDS[n]() for n in names if n in BACKENDS)
