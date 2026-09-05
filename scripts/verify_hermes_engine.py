#!/usr/bin/env python3
"""
Independent validation of HERMES_EXECUTION_ENGINE (PRD: FRIDAY delegates
serious coding work to Hermes; final gate item 5).

Starts a Hermes gateway under Friday's own `HermesSupervisor` (the
production seam - not a shortcut), delegates ONE bounded coding task into
a scratch git repository via `TaskBundle`, waits for the turn, and then
checks the OUTCOME on disk (file content, git status) rather than trusting
the agent's completion text. Writes the evidence with a provenance block.

    python scripts/verify_hermes_engine.py [--out data/hermes/engine_validation.json]

Environment-limited exits (recorded, not hidden): Hermes not installed,
gateway fails to become ready, no model provider configured.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TASK_GOAL = ("In this repository, create a file named answer.txt containing exactly the "
             "single line 'friday-hermes-ok' (no quotes) and nothing else. Do not create "
             "or modify any other file. Do not commit.")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, timeout=60).stdout.strip()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "hermes" / "engine_validation.json"))
    ap.add_argument("--timeout", type=float, default=420.0)
    args = ap.parse_args(argv)

    from friday import hermes_bridge as hb
    from friday import golden as G

    report: dict = {"provenance": G.provenance(), "task": TASK_GOAL, "steps": []}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def finish(verdict: str, **extra) -> int:
        report["verdict"] = verdict
        report.update(extra)
        out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k not in ("provenance", "steps")}, default=str))
        print("report:", out)
        return 0 if verdict == "VERIFIED" else 1

    located = hb.locate()
    report["located"] = located
    if not located:
        return finish("ENVIRONMENT_LIMITED", reason="Hermes not installed (hermes_bridge.locate() is None)")

    # A fresh HERMES_HOME: the profile's lease/session state must not be
    # shared with whatever Hermes session is driving this machine right now
    # (tool-lease contention stalls a child gateway for minutes).
    home = Path(tempfile.mkdtemp(prefix="friday-hermes-verify-"))
    os.environ["HERMES_HOME"] = str(home)
    # Provider keys come from the machine environment / Friday's .env, never
    # from this script. If none are configured the gateway will say so.

    repo = Path(tempfile.mkdtemp(prefix="friday-hermes-repo-"))
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("scratch repository for the execution-engine probe\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=verify@friday", "-c", "user.name=friday-verify", "commit", "-q", "-m", "seed")
    report["repo"] = str(repo)

    events: list[dict] = []

    def _on_event(kind, sid, payload):
        events.append({"t": round(time.time(), 3), "event": kind, "session": sid,
                       "keys": sorted(payload.keys())[:8] if isinstance(payload, dict) else str(payload)[:60]})

    sup = hb.HermesSupervisor(on_event=_on_event)
    t0 = time.time()
    try:
        sup.start()
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        return finish("ENVIRONMENT_LIMITED", reason=f"gateway did not start: {type(exc).__name__}: {exc}")
    report["steps"].append({"gateway_ready_s": round(time.time() - t0, 2), "health": sup.health()})

    bundle = hb.TaskBundle(
        goal=TASK_GOAL,
        user_outcome="answer.txt exists with the exact expected content",
        acceptance=("answer.txt contains exactly 'friday-hermes-ok'", "no other file changed"),
        constraints=("do not commit", "do not touch files other than answer.txt"),
        allowed_paths=(str(repo),),
        verification=("cat answer.txt",),
        iteration_budget=6,
        token_budget="LOW",
    )
    t1 = time.time()
    try:
        result = sup.delegate(bundle, friday_run_id="verify-engine", workspace=str(repo),
                              wait=True, turn_timeout=args.timeout, share_memory=False)
    except Exception as exc:  # noqa: BLE001
        sup.stop()
        return finish("FAILED", reason=f"delegate raised {type(exc).__name__}: {exc}",
                      events=events[-20:])
    elapsed = round(time.time() - t1, 2)
    record = result.get("result") or {}
    report["steps"].append({"delegate_s": elapsed, "work_run_id": result.get("work_run_id"),
                            "session_id": result.get("session_id"),
                            "record_status": record.get("status"),
                            "record_keys": sorted(record.keys())[:30]})
    try:
        report["usage"] = sup.usage(result["work_run_id"])
    except Exception as exc:  # noqa: BLE001
        report["usage"] = {"error": str(exc)}
    try:
        report["progress"] = sup.progress(result["work_run_id"])
    except Exception as exc:  # noqa: BLE001
        report["progress"] = {"error": str(exc)}
    sup.stop()

    # The outcome, judged from disk - not from what the agent said.
    answer = repo / "answer.txt"
    content = answer.read_text(encoding="utf-8") if answer.exists() else None
    status = _git(repo, "status", "--porcelain")
    changed = [line[3:] for line in status.splitlines() if line.strip()]
    committed = _git(repo, "rev-list", "--count", "HEAD")
    report["outcome"] = {"answer_exists": answer.exists(), "answer_content": content,
                         "git_changes": changed, "commits": committed}
    ok = (content is not None and content.strip() == "friday-hermes-ok"
          and set(changed) <= {"answer.txt"} and committed == "1")
    report["events_tail"] = events[-30:]
    if ok:
        return finish("VERIFIED", elapsed_s=elapsed)
    if record.get("status") in ("", None) or "provider" in json.dumps(record, default=str).lower() and not content:
        return finish("ENVIRONMENT_LIMITED" if not content and record.get("status") != "COMPLETED" else "FAILED",
                      reason=f"record status {record.get('status')!r}; answer={content!r}; changes={changed}")
    return finish("FAILED", reason=f"outcome mismatch: answer={content!r}, changes={changed}, commits={committed}")


if __name__ == "__main__":
    sys.exit(main())
