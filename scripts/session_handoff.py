"""Continue a Claude Code session on another approved provider after a limit.

Controller/security decisions that are easy to regress:

* ``FRIDAY_HANDOFF_DRY_RUN=1`` on the StopFailure hook writes the handoff packet
  outside the repository and prints the would-be argv, but it never marks
  provider health or mutates the shared provider-health file.
* Headless Codex runs in ``workspace-write`` by default. The owner must set
  ``FRIDAY_HANDOFF_ALLOW_FULL_ACCESS=1`` for ``danger-full-access``.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_providers as P  # noqa: E402

ROSTER = ROOT / "docs" / "plans" / "build-harness" / "model_roster.json"
PLAN_DOC = ROOT / "docs" / "plans" / "build-harness" / "2026-10-10-multi-model-continuation.md"
MAX_PACKET = 60_000
OWNER_TITLE = "## Owner's messages (verbatim, oldest first)"
RULES = """You are continuing a Claude Code session on FRIDAY that stopped at a usage limit. Same repository, same owner. Follow AGENTS.md and .claude/rules/. Check `git log` and the state below before acting, and never redo finished work. Never touch data/ada.sqlite3. Run tests with .venv-build/Scripts/python.exe. Do not push. Never print secrets. Owner rulings bind: quote them, never paraphrase."""

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9_]+"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"xox[abprs]-[A-Za-z0-9-]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.I),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]
KEY_VALUE_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|client_secret)\s*([:=])\s*([\"']?)([^\s\"']{8,})"
)


def now() -> float:
    return time.time()


def redact(text: str) -> str:
    out = str(text)
    for pattern in SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return KEY_VALUE_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED]", out)


def _cap(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[-limit:]


def _slug(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _handoff_root(cwd: Path) -> Path:
    base = os.environ.get("FRIDAY_HANDOFF_DIR")
    if base:
        return Path(base)
    return Path.home() / ".claude" / "handoffs" / _slug(cwd)


def _roster_ids() -> list[str]:
    try:
        data = json.loads(ROSTER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ids: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).startswith("_") or not isinstance(value, list):
                continue
            for item in value:
                if isinstance(item, str) and item not in ids:
                    ids.append(item)
    return ids


def _read_jsonl(path: Path) -> dict:
    owners = collections.deque(maxlen=12)
    summaries = collections.deque(maxlen=1)
    scheduled = ""
    assistant = collections.deque(maxlen=4)
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return {"owners": owners, "summary": "", "scheduled": "", "assistant": assistant}
    with fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            typ = row.get("type")
            if typ == "user":
                msg = row.get("message") if isinstance(row.get("message"), dict) else {}
                content = msg.get("content")
                if row.get("isCompactSummary") and isinstance(content, str):
                    summaries.append(content)
                elif not row.get("isMeta") and isinstance(content, str) and not content.startswith(
                    ("<local-command-stdout>", "<local-command-caveat>", "Caveat:")
                ):
                    owners.append(content)
            elif typ == "attachment":
                att = row.get("attachment") if isinstance(row.get("attachment"), dict) else {}
                if att.get("type") == "queued_command" and att.get("commandMode") == "prompt":
                    prompt = att.get("prompt")
                    if isinstance(prompt, str):
                        owners.append(prompt)
                    elif isinstance(prompt, list):
                        owners.append("".join(p.get("text", "") for p in prompt if isinstance(p, dict)))
            elif typ == "system" and row.get("subtype") == "scheduled_task_fire" and isinstance(row.get("prompt"), str):
                scheduled = row["prompt"]
            elif typ == "assistant":
                msg = row.get("message") if isinstance(row.get("message"), dict) else {}
                parts = msg.get("content") if isinstance(msg.get("content"), list) else []
                text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
                if text:
                    assistant.append(text)
    return {"owners": owners, "summary": summaries[-1] if summaries else "", "scheduled": scheduled, "assistant": assistant}


def _git(args: list[str], cwd: Path) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"git {' '.join(args)} failed: {exc}"
    text = out.stdout if out.returncode == 0 else out.stderr
    return (text or f"git {' '.join(args)} exited {out.returncode}").strip()


def _repo_section(cwd: Path) -> str:
    status = _git(["status", "--short"], cwd).splitlines()
    shown = status[:40]
    return "\n".join([
        f"branch: {_git(['rev-parse', '--abbrev-ref', 'HEAD'], cwd)}",
        "",
        "log:",
        _git(["log", "--oneline", "-8"], cwd),
        "",
        f"status --short ({len(status)} lines):",
        "\n".join(shown),
    ]).rstrip()


def _build_loop(cwd: Path) -> str:
    path = cwd / "data" / "build_state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    compact = {"status": data.get("status"), "stop_reason": data.get("stop_reason")}
    waves = data.get("waves")
    if isinstance(waves, list) and waves:
        last = waves[-1] if isinstance(waves[-1], dict) else {}
        compact["last_wave"] = {"n": last.get("n"), "results": last.get("results")}
    return json.dumps(compact, ensure_ascii=False, sort_keys=True)


def _write_packet(text: str, cwd: Path, session_id: str) -> Path:
    root = _handoff_root(cwd)
    root.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.fromtimestamp(now()).strftime("%Y%m%d-%H%M%S")
    packet = root / f"{session_id or 'session'}-{stamp}.md"
    packet.write_text(text[:MAX_PACKET], encoding="utf-8", newline="\n")
    return packet


def build_packet(*, session_id: str = "", transcript: str | Path | None = None, cwd: str | Path | None = None,
                 route: str = "", limit_text: str = "", last_assistant_message: str = "") -> Path:
    repo = Path(cwd or os.getcwd()).resolve()
    transcript_path = Path(transcript).expanduser() if transcript else Path("")
    parsed = _read_jsonl(transcript_path) if transcript else {"owners": [], "summary": "", "scheduled": "", "assistant": []}
    assistant = [_cap(x, 2500) for x in list(parsed["assistant"])[-4:]]
    if last_assistant_message:
        assistant.append(_cap(last_assistant_message, 2500))
    reset = P.reset_at(limit_text, now()) if limit_text else None
    reset_line = "" if reset is None else f"\nreset: {dt.datetime.fromtimestamp(reset).strftime('%Y-%m-%d %H:%M')}"
    memory = Path.home() / ".claude" / "projects" / _slug(repo) / "memory" / "MEMORY.md"
    head = [
        ("# FRIDAY Claude Handoff", "\n".join([
            f"written: {dt.datetime.fromtimestamp(now()).isoformat(timespec='seconds')}",
            f"why: {limit_text or 'usage limit'}{reset_line}",
            f"source session_id: {session_id or 'session'}",
            f"transcript: {transcript_path}",
            f"repo cwd: {repo}",
            f"chosen route: {route}",
        ])),
        ("## Rules", RULES),
    ]
    tail = [
        ("## Standing scheduled instruction", _cap(parsed["scheduled"], 3000)),
        ("## Session summary (last compaction)", _cap(parsed["summary"], 24000)),
        ("## Claude's last messages", "\n\n".join(assistant)),
        ("## Repository", _repo_section(repo)),
        ("## Build loop", _cap(_build_loop(repo), 3000)),
        ("## Where to look", f"- transcript: {transcript_path}\n- memory: {memory}\n- plan: {PLAN_DOC}"),
    ]
    owners = [_cap(x, 4000) for x in parsed["owners"]]

    def render(blocks: list[str]) -> str:
        sections = [*head, (OWNER_TITLE, "\n\n".join(blocks)), *tail]
        return "\n\n".join(f"{title}\n\n{redact(body)}".rstrip() for title, body in sections) + "\n"

    text = render(owners)
    while len(text) > MAX_PACKET and owners:
        owners.pop(0)  # the oldest owner message goes first, so the newest context survives
        text = render(owners)
    return _write_packet(text, repo, session_id)


def _rows() -> list[dict]:
    return P.catalogue(_roster_ids(), now=now())


def _health_label(row: dict) -> str:
    label = row.get("health", "OK")
    entry = P.status(row["provider"], now=now())
    until = entry.get("until")
    if label != "OK" and until is not None:
        label = f"{label} until {dt.datetime.fromtimestamp(float(until)).strftime('%H:%M')}"
    return label


def cmd_models(args: argparse.Namespace) -> int:
    rows = _rows()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    route_w = max(42, *(len(str(row["route"])) + 2 for row in rows))
    print(f"{'#':<3}{'route':<{route_w}}{'provider':<10}{'billing':<15}{'data':<16}health")
    for i, row in enumerate(rows, 1):
        note = " (switch with /model)" if row["provider"] == "claude" else ""
        print(f"{i:<3}{row['route']:<{route_w}}{row['provider']:<10}{row['billing']:<15}{row['data']:<16}{_health_label(row)}{note}")
    print("\nContinue with: python scripts/session_handoff.py use <#>")
    print("Inside Claude Code: ! .venv-build/Scripts/python.exe scripts/session_handoff.py use <#>")
    return 0


def _resolve_route(value: str) -> str:
    rows = _rows()
    if value.isdigit():
        idx = int(value)
        if 1 <= idx <= len(rows):
            return rows[idx - 1]["route"]
    return value


def _configured_codex_model() -> str:
    try:
        text = Path(P.CODEX_CONFIG).read_text(encoding="utf-8")
    except OSError:
        return "gpt-5.5"
    m = re.search(r'^model\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else "gpt-5.5"


def _default_routes() -> list[str]:
    raw = os.environ.get("FRIDAY_HANDOFF_ROUTES")
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    # Owner 2026-10-12: "if nt possible leave opencode ok we will use chatgpt and claude ok". OpenCode
    # routes stay available, but only when FRIDAY_HANDOFF_ROUTES names them.
    return [f"codex:{_configured_codex_model()}"]


def _validate_route(route: str) -> tuple[bool, str]:
    ok, why = P.eligible(route)
    if not ok:
        return False, why
    provider, model = P.split(route)
    entry = P.status(provider, now=now())
    if entry["state"] != "OK":
        until = entry.get("until")
        when = "unknown" if until is None else dt.datetime.fromtimestamp(float(until)).strftime("%Y-%m-%d %H:%M")
        return False, f"provider {provider} is out until {when}"
    if provider == "claude":
        return False, f"use /model {model} in Claude Code"
    if provider == "codex" and not P.codex_on_plan():
        return False, "Codex is not on its ChatGPT login; refusing a billed route"
    return True, ""


def _argv_for(route: str, repo: Path, packet: Path, *, headless: bool) -> tuple[list[str], dict | None, str | None]:
    provider, model = P.split(route)
    prompt = f"Read {packet} and continue the session it describes."
    if provider == "codex":
        if headless:
            sandbox = "danger-full-access" if os.environ.get("FRIDAY_HANDOFF_ALLOW_FULL_ACCESS") == "1" else "workspace-write"
            return [*P.codex_argv(), "exec", "-C", str(repo), "-m", model, "-s", sandbox, "--json", "-"], None, prompt
        return [*P.codex_argv(), "-C", str(repo), "-m", model, prompt], None, None
    if headless:
        return [*P.opencode_argv(), "run", "-m", model, "--agent", "build", "--format", "json", prompt], P.opencode_env(model, data_dir=packet.parent / "opencode-data"), None
    return [*P.opencode_argv(), str(repo), "-m", model, "--prompt", prompt], None, None


def spawn(argv: list[str], *, cwd: Path, env: dict | None, interactive: bool, log_path: Path | None,
           stdin_text: str | None) -> int:
    flags = 0
    stdin = subprocess.DEVNULL
    stdout = stderr = None
    log = None
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_CONSOLE if interactive else subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("ab")
        stdout = stderr = log
    if stdin_text is not None:
        stdin = subprocess.PIPE
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=stdin, stdout=stdout, stderr=stderr,
                                creationflags=flags, text=True, encoding="utf-8", errors="replace")
    finally:
        if log is not None:
            log.close()
    if stdin_text is not None and proc.stdin:
        proc.stdin.write(stdin_text)
        proc.stdin.close()
    return int(proc.pid)


def _append_launch(cwd: Path, *, session_id: str, route: str, mode: str, pid: int, packet: Path) -> None:
    path = _handoff_root(cwd) / "launches.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": now(), "session_id": session_id, "route": route, "mode": mode, "pid": pid, "packet": str(packet)}
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _already_launched(cwd: Path, session_id: str) -> bool:
    entry = P.status("claude")
    threshold = float(entry.get("at", 0)) - 60
    path = _handoff_root(cwd) / "launches.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("session_id") == session_id and float(row.get("ts", 0)) >= threshold:
            return True
    return False


def _launch(route: str, repo: Path, packet: Path, *, session_id: str, headless: bool, dry_run: bool) -> int:
    argv, env, stdin_text = _argv_for(route, repo, packet, headless=headless)
    mode = "headless" if headless else "interactive"
    if dry_run:
        print(" ".join(argv))
        print(f"packet: {packet}")
        return 0
    if os.name != "nt" and not headless:
        print(" ".join(argv))
        print(f"packet: {packet}")
        return 0
    pid = spawn(argv, cwd=repo, env=env, interactive=not headless, log_path=packet.with_suffix(packet.suffix + ".log") if headless else None,
                stdin_text=stdin_text)
    _append_launch(repo, session_id=session_id, route=route, mode=mode, pid=pid, packet=packet)
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    route = _resolve_route(args.route)
    ok, why = _validate_route(route)
    if not ok:
        print(why, file=sys.stderr)
        return 2
    repo = ROOT
    packet = build_packet(session_id="session", cwd=repo, route=route)
    return _launch(route, repo, packet, session_id="session", headless=args.headless, dry_run=args.dry_run)


def _pick_route() -> str | None:
    candidates = _default_routes()
    if not P.codex_on_plan():
        candidates = [r for r in candidates if P.split(r)[0] != "codex"]
    return P.route(candidates, now=now())


def cmd_packet(args: argparse.Namespace) -> int:
    packet = build_packet(session_id="session", transcript=args.transcript, cwd=ROOT, route="")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(packet.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        print(out)
    else:
        print(packet)
    return 0


def cmd_on_limit(_args: argparse.Namespace, stdin_text: str | None = None) -> int:
    raw = stdin_text if stdin_text is not None else sys.stdin.read(1024 * 1024 + 1)
    raw = raw[:1024 * 1024]
    try:
        event = json.loads(raw)
    except ValueError:
        print("invalid hook JSON", file=sys.stderr)
        return 0
    if not isinstance(event, dict) or event.get("hook_event_name") != "StopFailure" or event.get("error") != "rate_limit":
        return 0
    repo = Path(event.get("cwd") or ROOT).resolve()
    session_id = str(event.get("session_id") or "session")
    details = str(event.get("error_details") or "")
    limit = P.classify(texts=[details], now=now()) or {"state": P.QUOTA, "until": None, "detail": details}
    dry = os.environ.get("FRIDAY_HANDOFF_DRY_RUN") == "1"
    if not dry:
        P.mark("claude", limit["state"], until=limit.get("until"), reason=details[:300], source=f"session {session_id}",
               now=now())
    if _already_launched(repo, session_id):
        return 0
    route = _pick_route()
    packet = build_packet(session_id=session_id, transcript=event.get("transcript_path"), cwd=repo, route=route or "",
                          limit_text=details, last_assistant_message=str(event.get("last_assistant_message") or ""))
    if not route:
        reset = P.next_reset(("claude",))
        when = "unknown" if reset is None else dt.datetime.fromtimestamp(reset).strftime("%Y-%m-%d %H:%M")
        print(f"no route available; Claude resets at {when}", file=sys.stderr)
        return 0
    return _launch(route, repo, packet, session_id=session_id,
                   headless=os.environ.get("FRIDAY_HANDOFF_MODE", "interactive") == "headless", dry_run=dry)


def main(argv: list[str] | None = None, *, stdin_text: str | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    models = sub.add_parser("models")
    models.add_argument("--json", action="store_true")
    use = sub.add_parser("use")
    use.add_argument("route")
    use.add_argument("--headless", action="store_true")
    use.add_argument("--dry-run", action="store_true")
    packet = sub.add_parser("packet")
    packet.add_argument("--transcript")
    packet.add_argument("--out")
    sub.add_parser("on-limit")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "models":
            return cmd_models(args)
        if args.cmd == "use":
            return cmd_use(args)
        if args.cmd == "packet":
            return cmd_packet(args)
        if args.cmd == "on-limit":
            return cmd_on_limit(args, stdin_text=stdin_text)
    except Exception as exc:  # The Claude hook must never break Claude Code.
        message = f"session_handoff error: {exc}"
        print(message, file=sys.stderr)
        try:
            root = _handoff_root(Path(os.getcwd()).resolve())
            root.mkdir(parents=True, exist_ok=True)
            with (root / "errors.log").open("a", encoding="utf-8", newline="\n") as f:
                f.write(message + "\n")
        except OSError:
            pass
        return 0 if getattr(args, "cmd", "") == "on-limit" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
