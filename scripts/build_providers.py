"""Which model routes the build harness and the interactive session may use, and which are out of
quota right now (owner 2026-10-10: "i want both to run toghter ok caude codex and opencode";
"if usage limit of claude reach i want it to continue seesion by running other models").

Three command-line agents, each on its own sign-in; no token is ever copied between them:

    claude     Claude Code on the owner's plan    (a model id as Claude Code takes it)
    codex      the Codex CLI on its ChatGPT login ("codex:<model>")
    opencode   OpenCode on a zero-retention free model ("opencode:<provider>/<model>")

One health file is shared by the build kernel, its verifier and the session hook, so a usage
limit seen by any of them takes that provider out of every route until it resets, and nothing
retries a provider that has said it is out. The plan is
docs/plans/build-harness/2026-10-10-multi-model-continuation.md.

    python scripts/build_providers.py status
    python scripts/build_providers.py clear PROVIDER
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
HEALTH_FILE = ROOT / "data" / "build_harness" / "provider_health.json"
PROVIDERS = ("claude", "codex", "opencode")
#: OpenCode Zen's free models whose provider keeps nothing and trains on nothing
#: (opencode.ai/docs/zen, read 2026-10-10). The other free models train on prompts (big-pickle,
#: exo, mimo, ling, muse-spark) or say not to send confidential data (nemotron), so FRIDAY's
#: source goes to them only on the owner's word. Assumed: FRIDAY source is confidential.
OPENCODE_APPROVED = ("opencode/step-5-preview-free", "opencode/longcat-2.5-preview-free",
                     "opencode/space-bunny-free")
#: How long a provider stays out when its limit names no reset: Claude's window is five hours.
QUOTA_HOLD_S = 5 * 3600
RATE_HOLD_S = 10 * 60
QUOTA, RATE, AUTH = "QUOTA_EXHAUSTED", "RATE_LIMITED", "AUTH_FAILED"
_NPM = Path(os.environ.get("APPDATA", str(Path.home()))) / "npm" / "node_modules"
CODEX_JS = _NPM / "@openai" / "codex" / "bin" / "codex.js"
OPENCODE_EXE = _NPM / "opencode-ai" / "bin" / ("opencode.exe" if os.name == "nt" else "opencode")
CODEX_MODELS = Path.home() / ".codex" / "models_cache.json"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"

# Read only from a CLI's error channel (see error_texts): a worker that writes code about
# rate limits must never take its own provider out.
_QUOTA_TEXT = re.compile(r"hit your (?:[a-z]+ )*limit|usage limit|usage_limit_reached|out of (?:extra )?usage"
                         r"|spend limit|insufficient_quota|exceeded your current quota|out of credits", re.I)
_RATE_TEXT = re.compile(r"rate[ _-]?limit|too many requests|\b429\b|overloaded", re.I)
_AUTH_TEXT = re.compile(r"authentication_failed|not logged in|run /login|invalid api key|\b401\b", re.I)
_IN = re.compile(r"\bin\s+((?:\d+\s*(?:days?|hours?|hrs?|minutes?|mins?|seconds?|secs?)\b[\s,]*(?:and\s+)?)+)", re.I)
_SPAN = re.compile(r"(\d+)\s*(day|hour|hr|minute|min|second|sec)", re.I)
_AT = re.compile(r"(?:resets?|again)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b", re.I)
# Codex 0.160.0, seen 2026-10-10: "... or try again at Oct 11th, 2026 1:09 AM."
_ON = re.compile(r"(?:resets?|again)\s+(?:at|on)?\s*([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4}),?"
                 r"\s+(\d{1,2}):(\d{2})\s*([ap])\.?m\b", re.I)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_UNIT_S = {"day": 86400, "hour": 3600, "hr": 3600, "minute": 60, "min": 60, "second": 1, "sec": 1}


def _now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def split(model: str) -> tuple[str, str]:
    """'codex:gpt-5.5' -> ('codex', 'gpt-5.5'); an id without a known prefix is Claude's."""
    head, sep, tail = str(model).partition(":")
    if sep and head in ("codex", "opencode") and tail:
        return head, tail
    return "claude", str(model)


def eligible(model: str) -> tuple[bool, str]:
    """Whether a route may run FRIDAY work at all (billing and data policy, not health)."""
    provider, name = split(model)
    if provider == "opencode" and name not in OPENCODE_APPROVED:
        return False, f"{name} is not one of the zero-retention free models the owner allows"
    return True, ""


# ----------------------------------------------------------------- classify

def error_texts(events: Iterable[dict]) -> list[str]:
    """The error channel of each CLI's JSON events: Claude's error result and errored assistant
    turns, Codex's error and turn.failed, OpenCode's error. Ordinary output is never included."""
    out: list[str] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        kind = e.get("type")
        if kind == "result" and e.get("is_error"):
            out.append(f"{e.get('subtype', '')} {e.get('result', '')}")
        elif kind == "assistant" and e.get("error"):
            parts = (e.get("message") or {}).get("content") or []
            text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict))
            out.append(f"{e['error']} {text}")
        elif kind in ("error", "turn.failed"):
            err = e.get("error", e.get("message", ""))
            if isinstance(err, dict):
                err = err.get("message") or json.dumps(err, ensure_ascii=False)
            out.append(str(err))
    return out


def reset_at(text: str, now: float | None = None) -> float | None:
    """When a limit message says it resets: "try again in 2 days 3 hours" or "resets 3am"/"at 5:43 PM"
    (local time, the next such moment). None when it names no time."""
    now = _now(now)
    span = _IN.search(text or "")
    if span:
        return now + sum(int(n) * _UNIT_S[unit.lower()] for n, unit in _SPAN.findall(span.group(1)))
    full = _ON.search(text or "")
    if full and full.group(1).lower() in _MONTHS:
        mon, day, year, hh, mm, half = full.groups()
        hour = int(hh) % 12 + (12 if half.lower() == "p" else 0)
        try:
            return dt.datetime(int(year), _MONTHS.index(mon.lower()) + 1, int(day), hour, int(mm)).timestamp()
        except ValueError:
            return None
    m = _AT.search(text or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    base = dt.datetime.fromtimestamp(now)
    moment = base.replace(hour=hour, minute=int(m.group(2) or 0), second=0, microsecond=0)
    if moment.timestamp() <= now:
        moment += dt.timedelta(days=1)
    return moment.timestamp()


def classify(texts: Iterable[str] = (), events: Iterable[dict] = (), now: float | None = None) -> dict | None:
    """A usage limit, rate limit or sign-in failure, from a CLI's error channel; None when there is
    none. `texts` are error-channel strings (a hook's error_details, a failed CLI's stderr), and
    `events` are its JSON events, of which only rate_limit_event and the error channel count."""
    now = _now(now)
    events = [e for e in events if isinstance(e, dict)]
    for e in events:
        info = e.get("rate_limit_info") if e.get("type") == "rate_limit_event" else None
        if isinstance(info, dict) and info.get("status") == "rejected":
            resets = info.get("resetsAt")
            until = float(resets) if isinstance(resets, (int, float)) and resets > now else None
            return {"state": QUOTA, "until": until,
                    "detail": f"rate_limit_event rejected ({info.get('rateLimitType') or 'limit'})"}
    blob = "\n".join([*(str(t) for t in texts if t), *error_texts(events)])
    for state, pattern in ((QUOTA, _QUOTA_TEXT), (AUTH, _AUTH_TEXT), (RATE, _RATE_TEXT)):
        m = pattern.search(blob)
        if m:
            line = next((ln for ln in blob.splitlines() if pattern.search(ln)), m.group(0))
            return {"state": state, "until": reset_at(line, now) if state != AUTH else None,
                    "detail": line.strip()[:300]}
    return None


# ------------------------------------------------------------------- health

def load(path: Path | None = None) -> dict:
    try:
        data = json.loads(Path(path or HEALTH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(data: dict, path: Path | None) -> None:
    path = Path(path or HEALTH_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def mark(provider: str, state: str, *, until: float | None = None, reason: str = "", source: str = "",
         path: Path | None = None, now: float | None = None) -> dict:
    """Take a provider out until `until` (a quota or rate limit without one gets its default
    hold; a sign-in failure stays until clear(), since only the owner can sign in again)."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    if state not in (QUOTA, RATE, AUTH):
        raise ValueError(f"unknown state {state!r}")
    now = _now(now)
    hold = {QUOTA: QUOTA_HOLD_S, RATE: RATE_HOLD_S}.get(state)
    entry = {"state": state, "until": until if until is not None else (now + hold if hold else None),
             "reason": str(reason)[:300], "source": str(source)[:120], "at": now}
    data = load(path)
    data[provider] = entry
    _store(data, path)
    return entry


def clear(provider: str, path: Path | None = None) -> None:
    data = load(path)
    if data.pop(provider, None) is not None:
        _store(data, path)


def status(provider: str, now: float | None = None, path: Path | None = None) -> dict:
    """{"state": "OK"} unless the provider is out now; an expired hold has recovered."""
    entry = load(path).get(provider)
    if not isinstance(entry, dict) or entry.get("state") not in (QUOTA, RATE, AUTH):
        return {"state": "OK"}
    until = entry.get("until")
    if until is not None and _now(now) >= float(until):
        return {"state": "OK", "recovered_from": entry["state"]}
    return entry


def available(provider: str, now: float | None = None, path: Path | None = None) -> bool:
    return status(provider, now, path)["state"] == "OK"


def route(candidates: Iterable[str], now: float | None = None, path: Path | None = None) -> str | None:
    """The first candidate that may run and whose provider is not out; None parks the job."""
    for model in candidates:
        if eligible(model)[0] and available(split(model)[0], now, path):
            return model
    return None


def next_reset(providers: Iterable[str] = PROVIDERS, now: float | None = None,
               path: Path | None = None) -> float | None:
    """The earliest time one of `providers` comes back; None if one is up now or none will by itself."""
    times = []
    for provider in providers:
        entry = status(provider, now, path)
        if entry["state"] == "OK":
            return None
        if entry.get("until") is not None:
            times.append(float(entry["until"]))
    return min(times) if times else None


# ------------------------------------------------------------------ the CLIs

def _argv_override(name: str) -> list[str] | None:
    """FRIDAY_CODEX_ARGV / FRIDAY_OPENCODE_ARGV: a JSON list naming a CLI installed elsewhere
    (tests point it at a stand-in that prints the real CLI's event shapes)."""
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        argv = json.loads(raw)
    except ValueError:
        argv = None
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv)):
        raise ValueError(f"{name} must be a JSON list of strings")
    return argv


def codex_argv() -> list[str]:
    """The Codex CLI without its .cmd shim (a list argv cannot run a .cmd on Windows)."""
    override = _argv_override("FRIDAY_CODEX_ARGV")
    if override:
        return override
    if CODEX_JS.exists():
        return [shutil.which("node") or "node", str(CODEX_JS)]
    return [shutil.which("codex") or "codex"]


def opencode_argv() -> list[str]:
    override = _argv_override("FRIDAY_OPENCODE_ARGV")
    if override:
        return override
    return [str(OPENCODE_EXE)] if OPENCODE_EXE.exists() else [shutil.which("opencode") or "opencode"]


def codex_on_plan(runner=subprocess.run) -> bool:
    """Codex runs only on its ChatGPT login: an API-key login would bill per token."""
    try:
        out = runner([*codex_argv(), "login", "status"], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return "using chatgpt" in f"{out.stdout or ''}{out.stderr or ''}".lower()


def _jsonc(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def opencode_env(model: str, base: dict | None = None, config: Path | None = None,
                 data_dir: Path | None = None) -> dict:
    """OpenCode slimmed for a headless run: no plugins, skills or MCP servers (the owner's full
    config hung a run for minutes), only the opencode provider, titles on the same free model.
    `data_dir` gives the run its own session database: two runs started together on the shared
    one failed with "database is locked" (2026-10-10)."""
    env = dict(os.environ if base is None else base)
    if data_dir is not None:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        env["XDG_DATA_HOME"] = str(data_dir)
    for flag in ("PURE", "DISABLE_CLAUDE_CODE", "DISABLE_EXTERNAL_SKILLS", "DISABLE_DEFAULT_PLUGINS",
                 "DISABLE_AUTOUPDATE", "DISABLE_SHARE", "DISABLE_LSP_DOWNLOAD"):
        env[f"OPENCODE_{flag}"] = "1"
    servers = (_jsonc(config or OPENCODE_CONFIG).get("mcp") or {})
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps({
        "enabled_providers": ["opencode"], "small_model": model, "share": "disabled",
        "autoupdate": False, "mcp": {name: {"enabled": False} for name in servers}})
    return env


def _codex_models(cache: Path | None = None, config: Path | None = None) -> list[str]:
    try:
        models = json.loads(Path(cache or CODEX_MODELS).read_text(encoding="utf-8")).get("models") or []
    except (OSError, ValueError, AttributeError):
        models = []
    names = [str(m.get("slug")) for m in models
             if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
    try:
        m = re.search(r'^model\s*=\s*"([^"]+)"', Path(config or CODEX_CONFIG).read_text(encoding="utf-8"), re.M)
    except OSError:
        m = None
    if m and m.group(1) not in names:
        names.insert(0, m.group(1))
    return names


def catalogue(claude_models: Iterable[str] = (), *, codex_cache: Path | None = None,
              codex_config: Path | None = None, now: float | None = None,
              path: Path | None = None) -> list[dict]:
    """Every route the owner can pick: provider, route id, health, billing and data policy."""
    rows = []

    def add(provider: str, route_id: str, billing: str, data: str) -> None:
        ok, why = eligible(route_id)
        rows.append({"provider": provider, "route": route_id, "billing": billing, "data": data,
                     "eligible": ok, "refused": why, "health": status(provider, now, path)["state"]})

    for m in dict.fromkeys(claude_models):
        add("claude", m, "Claude plan", "Anthropic")
    for m in _codex_models(codex_cache, codex_config):
        add("codex", f"codex:{m}", "ChatGPT plan", "OpenAI")
    for m in OPENCODE_APPROVED:
        add("opencode", f"opencode:{m}", "free", "zero-retention")
    return rows


def main(argv: list[str]) -> int:
    if argv[:1] == ["status"]:
        for provider in PROVIDERS:
            entry = status(provider)
            until = entry.get("until")
            when = "" if until is None else time.strftime(" until %Y-%m-%d %H:%M", time.localtime(until))
            print(f"{provider:9s} {entry['state']}{when} {entry.get('reason', '')}".rstrip())
        return 0
    if len(argv) == 2 and argv[0] == "clear" and argv[1] in PROVIDERS:
        clear(argv[1])
        return 0
    print("usage: build_providers.py status | clear claude|codex|opencode", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
