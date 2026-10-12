"""Codex/OpenCode worker shim for the build harness."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_providers as P  # noqa: E402
import build_kernel as K  # noqa: E402


PREAMBLE = ("You are a FRIDAY build worker in a git worktree (the current directory). "
            f"Python: {K.VENV_PY}. Do not commit, stash, reset or switch branches: "
            "the kernel commits. When finished write .build/evidence.json exactly "
            "as .build/packet.md asks.")
DEFAULT_STALL_S = 600


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _git(args: list[str], cwd: Path) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    return r.stdout if r.returncode == 0 else f"ERR:{r.stderr}"


def stamp(cwd: Path) -> tuple[str, str]:
    status = _git(["status", "--porcelain=v1", "-uall"], cwd)
    diff = subprocess.run(["git", "diff", "--binary"], cwd=cwd, capture_output=True,
                          timeout=60).stdout
    return status, hashlib.sha256(diff).hexdigest()


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def refusal(provider: str, model: str, why: str, verify: bool) -> int:
    sid = f"{provider}-{uuid.uuid4()}"
    result = {"type": "result", "subtype": "error_refused", "is_error": True,
              "result": why, "num_turns": 0, "total_cost_usd": 0.0,
              "session_id": sid, "provider": provider, "route": f"{provider}:{model}"}
    if verify:
        emit(result)
    else:
        emit({"type": "system", "subtype": "init", "session_id": sid,
              "model": f"{provider}:{model}", "provider": provider})
        emit({**result, "usage": {"input_tokens": 0, "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0, "output_tokens": 0},
              "uuid": str(uuid.uuid4()), "limit": None})
    return 2


def command(provider: str, model: str, prompt: str, cwd: Path) -> tuple[list[str], dict, str | None]:
    if provider == "codex":
        return ([*P.codex_argv(), "exec", "-C", str(cwd), "-m", model, "-s", "danger-full-access",
                 "--json", "--ephemeral", "-"], os.environ.copy(), prompt)
    if provider == "opencode":
        return ([*P.opencode_argv(), "run", "-m", model, "--agent", "build", "--format", "json", prompt],
                P.opencode_env(model, data_dir=cwd / ".build" / "opencode-data"), None)
    raise SystemExit(f"unsupported provider {provider}")


def _reader(stdout, lines: queue.Queue[str | None]) -> None:
    try:
        for line in stdout:
            lines.put(line)
    finally:
        lines.put(None)


def _event_from_line(line: str) -> dict:
    try:
        ev = json.loads(line)
    except ValueError:
        ev = {"type": "error", "message": line.rstrip("\r\n")}
    return ev if isinstance(ev, dict) else {"type": "error", "message": str(ev)}


def run_provider(provider: str, model: str, prompt: str, cwd: Path, wall: int, stall: int,
                 sid: str | None = None, stream: bool = False) -> tuple[list[dict], int, str | None]:
    cmd, env, stdin_text = command(provider, model, prompt, cwd)
    kwargs = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", **kwargs)
    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    events: list[dict] = []
    lines: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(target=_reader, args=(proc.stdout, lines), daemon=True)
    reader.start()
    started = last_event = time.monotonic()
    stop_reason: str | None = None
    stdout_done = False
    while True:
        now = time.monotonic()
        if proc.poll() is None:
            if now - started > wall:
                stop_reason = "wall"
                kill_tree(proc)
            elif now - last_event > stall:
                stop_reason = "stall"
                kill_tree(proc)
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            if proc.poll() is not None and stdout_done:
                break
            continue
        if line is None:
            stdout_done = True
            if proc.poll() is not None:
                break
            continue
        ev = _event_from_line(line)
        events.append(ev)
        last_event = time.monotonic()
        if stream and sid is not None:
            emit({"type": "provider_event", "provider": provider, "session_id": sid, "event": ev})
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
    reader.join(timeout=1)
    return events, int(proc.returncode or 0), stop_reason


def summarize(provider: str, events: list[dict]) -> tuple[str, int, dict]:
    text = ""
    turns = 0
    usage = {"input_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0, "output_tokens": 0}
    for ev in events:
        if provider == "codex":
            if ev.get("type") == "item.completed":
                turns += 1
                item = ev.get("item") or {}
                if item.get("type") == "agent_message" and item.get("text"):
                    text = str(item["text"])
            if ev.get("type") == "turn.completed":
                u = ev.get("usage") or {}
                usage["input_tokens"] += int(u.get("input_tokens") or 0)
                usage["cache_read_input_tokens"] += int(u.get("cached_input_tokens") or 0)
                usage["output_tokens"] += int(u.get("output_tokens") or 0)
        elif provider == "opencode":
            if ev.get("type") == "text":
                part = ev.get("part") or {}
                text = str(part.get("text") or text)
            if ev.get("type") == "step_finish":
                turns += 1
                toks = ((ev.get("part") or {}).get("tokens") or {})
                cache = toks.get("cache") or {}
                usage["input_tokens"] += int(toks.get("input") or 0)
                usage["cache_read_input_tokens"] += int(cache.get("read") or 0)
                usage["cache_creation_input_tokens"] += int(cache.get("write") or 0)
                usage["output_tokens"] += int(toks.get("output") or 0)
    return text, turns, usage


def result_obj(provider: str, model: str, sid: str, events: list[dict], rc: int, stop_reason: str | None,
               tampered: bool) -> dict:
    limit = P.classify(events=events)
    text, turns, usage = summarize(provider, events)
    if limit:
        subtype, is_error, text = "error_limit", True, "\n".join(P.error_texts(events)) or text
    elif stop_reason == "wall":
        subtype, is_error = "error_wall", True
    elif stop_reason == "stall":
        subtype, is_error = "error_stall", True
    elif tampered:
        subtype, is_error = "error_tampered", True
    elif rc:
        subtype, is_error = "error_during_execution", True
    else:
        subtype, is_error = "success", False
    return {"type": "result", "subtype": subtype, "is_error": is_error, "result": text,
            "num_turns": turns, "total_cost_usd": 0.0, "usage": usage, "session_id": sid,
            "uuid": str(uuid.uuid4()), "provider": provider, "route": f"{provider}:{model}",
            "limit": limit}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("provider", choices=("codex", "opencode"))
    ap.add_argument("model")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--read-only", action="store_true")
    ap.add_argument("--wall", type=int, default=K.DEFAULT_WALL_S)
    ap.add_argument("--stall", type=int, default=DEFAULT_STALL_S)
    ns = ap.parse_args(argv)
    eligible, why = P.eligible(f"{ns.provider}:{ns.model}")
    if not eligible:
        return refusal(ns.provider, ns.model, why, ns.verify)
    if ns.provider == "codex" and not P.codex_on_plan():
        return refusal(ns.provider, ns.model, "codex is not on a ChatGPT login", ns.verify)
    cwd = Path.cwd()
    sid = f"{ns.provider}-{uuid.uuid4()}"
    prompt = sys.stdin.read() if ns.verify else ns.prompt
    if not ns.verify:
        prompt = PREAMBLE + "\n\n" + ("Read only: change no file.\n\n" if ns.read_only else "") + prompt
        emit({"type": "system", "subtype": "init", "session_id": sid,
              "model": f"{ns.provider}:{ns.model}", "provider": ns.provider})
    before = stamp(cwd) if ns.read_only else None
    events, rc, stop_reason = run_provider(ns.provider, ns.model, prompt, cwd, int(ns.wall),
                                           int(ns.stall), sid=sid, stream=not ns.verify)
    after = stamp(cwd) if ns.read_only else None
    tampered = bool(ns.read_only and before != after)
    res = result_obj(ns.provider, ns.model, sid, events, rc, stop_reason, tampered)
    emit(res if not ns.verify else {k: v for k, v in res.items()
                                    if k in {"type", "subtype", "is_error", "result", "num_turns",
                                             "total_cost_usd", "session_id", "provider", "route"}})
    if res["subtype"] == "success":
        return 0
    if res["subtype"] == "error_limit":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
