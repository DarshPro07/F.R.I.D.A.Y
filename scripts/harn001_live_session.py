#!/usr/bin/env python3
"""
The one owner-run HARN-001 live session (owner RULING 2026-10-13,
docs/plans/build-harness/00-owner-directive-2026-10-13.md).

Run:  .venv/Scripts/python.exe scripts/harn001_live_session.py                          dry run: the plan only
      .venv/Scripts/python.exe scripts/harn001_live_session.py --execute --ack HARN-001  the live run, once
      .venv/Scripts/python.exe scripts/harn001_live_session.py --restore SNAPSHOT_DIR    repair after a killed run

The live run deploys the generated harness into the Hermes `friday` profile,
proves discovery and one model turn through FRIDAY's MCP tools, then restores
the profile byte-exact from a snapshot and writes data/harn001_live/<utc>/report.json.
It is authorized once: data/harn001_live/SPENT.json is created before the deploy
and its presence refuses every later run; only the owner deleting it
re-authorizes. Run it and --restore in the foreground with no build job
running, and never point pytest at the live profile.

Exit codes: 0 dry run / pass / restored, 1 the run failed or a restore mismatched, 2 refused.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from friday import harness_sync as H  # noqa: E402
from friday import hermes_bridge, hermes_health  # noqa: E402

PROFILE = "friday"
ACK = "HARN-001"
MCP_BASE = "http://127.0.0.1:8000"   # base URL, not /sse: FastMCP answers GET / with 404 = alive
LIVE_DIR = ROOT / "data" / "harn001_live"
SPENT = "SPENT.json"
RECORD = "SNAPSHOT.json"
TOKEN_CAP = 20000
TURN_TIMEOUT = 300
FRESH_SECONDS = 120
SNAPSHOT_FILES = ("config.yaml", "SOUL.md", "shell-hooks-allowlist.json", ".skills_prompt_snapshot.json")
HASH_ONLY = "auth.json"              # credentials: hashed to prove them untouched, never copied
ABSENT = "ABSENT"
PROMPT = ("HARN-001 synthetic check. Call the get_current_time tool exactly once, then reply with "
          "the ISO 8601 time it returned and this token verbatim: {nonce}")
_MSYS = ("MSYSTEM", "MSYS", "MSYS2_PATH_TYPE", "ORIGINAL_PATH", "SHELL", "SHLVL", "OLDPWD", "PS1")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ABSENT


def _model(profile: Path) -> tuple[str, str]:
    config = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8")) or {}
    model = config.get("model") or {}
    return str(model.get("provider") or ""), str(model.get("default") or "")


def dry_manifest(source) -> list[str]:
    """Every file a render puts in the profile, found by rendering into a throwaway dir."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "hermes"
        H.render(source, Path(tmp) / "claude", out)
        return sorted(H._prior(out)) + [H.MANIFEST]


def _touched(profile: Path, manifest: list[str]) -> list[str]:
    """Every file a deploy can write or delete: the fixed set, this render, and a prior manifest's
    files (harness_sync._apply unlinks those the new render no longer produces)."""
    return sorted(set(SNAPSHOT_FILES) | set(manifest) | set(H._prior(profile)))


def snapshot(profile: Path, snap: Path, manifest: list[str]) -> dict:
    """Copy every file the session can touch into `snap`; ABSENT marks one to delete on restore."""
    files = {}
    for rel in _touched(profile, manifest):
        files[rel] = _sha(H._inside(profile, rel))
        if files[rel] != ABSENT:
            (snap / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(H._inside(profile, rel), snap / rel)
    record = {"profile": str(profile), "files": files, HASH_ONLY: _sha(profile / HASH_ONLY),
              "manifest": manifest}
    snap.mkdir(parents=True, exist_ok=True)
    # written last: a record on disk means every copy before it completed
    (snap / RECORD).write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def restore(snap: Path) -> dict:
    """Remove what the deploy wrote, put every snapshot file back byte-exact, then re-hash."""
    record = json.loads((snap / RECORD).read_text(encoding="utf-8"))
    profile = Path(record["profile"])
    for rel in sorted(set(record["manifest"]) | set(H._prior(profile)), reverse=True):
        target = H._inside(profile, rel)
        if target.is_file():
            target.unlink()
        for parent in target.parents:
            if parent == profile or not parent.is_dir() or any(parent.iterdir()):
                break
            parent.rmdir()
    for rel, sha in record["files"].items():
        target = H._inside(profile, rel)
        if sha == ABSENT:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(snap / rel, target)
    after = {rel: _sha(H._inside(profile, rel)) for rel in record["files"]}
    mismatched = sorted(rel for rel, sha in after.items() if sha != record["files"][rel])
    if _sha(profile / HASH_ONLY) != record[HASH_ONLY]:
        mismatched.append(HASH_ONLY)
    return {"hashes_after": after, "mismatched": mismatched, "restored": not mismatched}


def merge_config(config: dict, fragment: dict) -> dict:
    """`fragment` deep-merged into a copy of `config`, with the session overlay pinned on top."""
    def merge(into: dict, add: dict) -> None:
        for key, value in add.items():
            if isinstance(value, dict) and isinstance(into.get(key), dict):
                merge(into[key], value)
            elif isinstance(value, list) and isinstance(into.get(key), list):
                into[key] = into[key] + [v for v in value if v not in into[key]]
            else:
                into[key] = copy.deepcopy(value)

    merged = copy.deepcopy(config)
    merge(merged, fragment)
    merged["agent"] = {**(merged.get("agent") or {}), "max_turns": 6}
    for task in (merged.get("auxiliary") or {}).values():
        if isinstance(task, dict):
            task["provider"] = "main"        # no side model billed to another provider
    merged["hooks_auto_accept"] = False
    merged["fallback_providers"] = []
    return merged


def deploy(source, profile: Path, claude_tmp: Path) -> None:
    H.render(source, claude_tmp, profile, allow_live=True)
    fragment = yaml.safe_load((profile / "harness.config.yaml").read_text(encoding="utf-8")) or {}
    config_path = profile / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config_path.write_text(yaml.safe_dump(merge_config(config, fragment), sort_keys=False,
                                          allow_unicode=True), encoding="utf-8")


def child_env(base: dict, home: Path) -> dict:
    """The Hermes child's env only (as hermes_bridge.py:1765-1809): no session/MSYS state, no API keys.

    shortcut: scrubs the process env only; Hermes still loads $HERMES_HOME/.env, which is neither
    scrubbed nor snapshotted. The real spend bounds are the aux provider=main overlay,
    fallback_providers=[] and the explicit --provider; upgrade if the profile .env must be fenced."""
    env = {k: v for k, v in base.items()
           if not k.upper().startswith("HERMES_") and k.upper() not in _MSYS
           and not k.upper().endswith("_API_KEY")}
    env["HERMES_HOME"] = str(home)
    env["HERMES_ACCEPT_HOOKS"] = "1"
    return env


def _fresh(text: str, now: datetime.datetime) -> bool:
    for stamp in _ISO.findall(text):
        try:
            when = datetime.datetime.fromisoformat(stamp.replace(" ", "T"))
        except ValueError:                   # model text shaped like a time but not one: not evidence
            continue
        if when.tzinfo is None:
            when = when.astimezone()         # a naive time is the runtime's local time
        if abs((now - when).total_seconds()) <= FRESH_SECONDS:
            return True
    return False


def judge(rc: int, out: str, usage: dict, nonce: str, provider: str, now: datetime.datetime) -> dict:
    totals = usage.get("total_including_auxiliary") or {}
    tokens = totals.get("total_tokens", usage.get("total_tokens"))
    aux = usage.get("auxiliary_usage") or (usage.get("auxiliary") or {}).get("by_task") or {}
    foreign = sorted({str(t["provider"]) for t in aux.values()
                      if isinstance(t, dict) and t.get("provider") not in (None, provider)})
    checks = {"rc_zero": rc == 0, "nonce_in_reply": nonce in out, "fresh_iso_time": _fresh(out, now),
              "tokens_within_cap": isinstance(tokens, int) and tokens <= TOKEN_CAP,
              "aux_providers_main_only": not foreign}
    return {"checks": checks, "tokens": tokens, "foreign_aux_providers": foreign,
            "pass": all(checks.values())}


def _run(cmd: list[str], env: dict, timeout: int) -> tuple[int, str]:
    proc = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=False)
    return proc.returncode, proc.stdout


def _turn_cmd(hermes: str, nonce: str, provider: str, model: str, usage_file: Path) -> list[str]:
    return [hermes, "-p", PROFILE, "-z", PROMPT.format(nonce=nonce), "--provider", provider,
            "--model", model, "--toolsets", "mcp-friday", "--usage-file", str(usage_file)]


def dry_run(source, profile: Path | None, live_dir: Path, hermes: str | None) -> int:
    print(f"DRY RUN - nothing written. The live run is: --execute --ack {ACK}")
    spent = live_dir / SPENT
    print(f"marker:   {spent} ({'PRESENT - a live run is refused' if spent.exists() else 'absent'})")
    print(f"run dir:  {live_dir}/<utc>/ (snapshot/, usage.json, report.json)")
    if profile is None or not (profile / "config.yaml").is_file():
        print(f"profile:  {PROFILE!r} not found - a live run is refused")
        return 0
    provider, model = _model(profile)
    manifest = dry_manifest(source)
    print(f"profile:  {profile}")
    for rel in _touched(profile, manifest):
        print(f"snapshot: {H._inside(profile, rel)} {_sha(H._inside(profile, rel))}")
    print(f"hash only (never copied): {profile / HASH_ONLY}")
    print(f"deploy:   {profile / 'config.yaml'} (merged with harness.config.yaml + session overlay)")
    for rel in manifest:
        print(f"deploy:   {H._inside(profile, rel)}")
    print(f"discover: {hermes or 'hermes'} -p {PROFILE} mcp test friday")
    print("turn:     " + " ".join(_turn_cmd(hermes or "hermes", "<nonce>", provider, model,
                                            live_dir / "<utc>" / "usage.json"))
          + f" (timeout {TURN_TIMEOUT}s, token cap {TOKEN_CAP})")
    print("restore:  every snapshot file byte-exact; ABSENT files and manifest files deleted")
    return 0


def execute(source, profile: Path, live_dir: Path, runner, probe, hermes: str) -> int:
    spent = live_dir / SPENT
    if spent.exists():
        print(f"refused: {spent} exists - the live run is spent until the owner deletes it", file=sys.stderr)
        return 2
    provider, model = _model(profile)
    problems = [p for p, bad in ((f"FRIDAY MCP does not answer at {MCP_BASE}", not probe()),
                                 ("config.yaml names no model.provider/default", not (provider and model)))
                if bad]
    if problems:
        print("refused: " + "; ".join(problems), file=sys.stderr)
        return 2
    utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = live_dir / utc
    live_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(spent, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(f"refused: {spent} exists - the live run is spent until the owner deletes it", file=sys.stderr)
        return 2
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"utc": utc, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   "run_dir": str(run_dir)}, fh)
    nonce = secrets.token_hex(8)
    report = {"utc": utc, "profile": str(profile), "provider": provider, "model": model, "nonce": nonce,
              "token_cap": TOKEN_CAP, "steps": {}, "tokens": None, "pass": False}
    record = snapshot(profile, run_dir / "snapshot", dry_manifest(source))
    report["hashes_before"] = record["files"]
    try:
        with tempfile.TemporaryDirectory() as claude_tmp:
            deploy(source, profile, Path(claude_tmp))
        report["steps"]["deploy"] = True
        env = child_env(dict(os.environ), profile)
        rc, out = runner([hermes, "-p", PROFILE, "mcp", "test", "friday"], env, TURN_TIMEOUT)
        found = {"names_get_current_time": rc == 0 and "get_current_time" in out,
                 "skills_present": (profile / "skills" / H.SKILL_GROUP).is_dir(),
                 "soul_marked": H.BEGIN_MARK in (profile / "SOUL.md").read_text(encoding="utf-8")}
        report["steps"]["discovery"] = {"mcp_test_rc": rc, **found}
        if all(found.values()):              # no model spend unless discovery held
            usage_file = run_dir / "usage.json"
            rc, out = runner(_turn_cmd(hermes, nonce, provider, model, usage_file), env, TURN_TIMEOUT)
            usage = json.loads(usage_file.read_text(encoding="utf-8")) if usage_file.is_file() else {}
            verdict = judge(rc, out, usage, nonce, provider, datetime.datetime.now(datetime.timezone.utc))
            report["steps"]["turn"] = {"rc": rc, **verdict}
            report["tokens"], report["pass"] = verdict["tokens"], verdict["pass"]
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report.update(restore(run_dir / "snapshot"))
        report["pass"] = report["pass"] and report["restored"]
        (run_dir / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"report: {run_dir / 'report.json'} pass={report['pass']} restored={report['restored']}")
    return 0 if report["pass"] else 1


def main(argv: list[str] | None = None, *, source=ROOT, live_dir: Path = LIVE_DIR, runner=_run,
         probe=None, hermes: str | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--execute", action="store_true", help="the live run (needs --ack HARN-001)")
    ap.add_argument("--ack", default="", help=f"must be {ACK} for --execute")
    ap.add_argument("--restore", metavar="SNAPSHOT_DIR", help="restore the profile after a killed run")
    args = ap.parse_args(argv)
    if args.restore:
        result = restore(Path(args.restore))
        print("restored" if result["restored"] else f"MISMATCH {result['mismatched']}")
        return 0 if result["restored"] else 1
    home = hermes_bridge.profile_home(PROFILE)
    profile = Path(home) if home else None
    hermes = hermes or shutil.which("hermes")
    if not args.execute:
        return dry_run(source, profile, live_dir, hermes)
    if args.ack != ACK:
        print(f"refused: --execute needs --ack {ACK}", file=sys.stderr)
        return 2
    if profile is None or not (profile / "config.yaml").is_file() or not hermes:
        print(f"refused: profile {PROFILE!r} or the hermes CLI is missing", file=sys.stderr)
        return 2
    if probe is None:
        probe = lambda: hermes_health.live_probe_factory(MCP_BASE)().signals["mcp_server_alive"]  # noqa: E731
    return execute(source, profile, live_dir, runner, probe, hermes)


if __name__ == "__main__":
    sys.exit(main())
