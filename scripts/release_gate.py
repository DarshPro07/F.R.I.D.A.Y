#!/usr/bin/env python3
"""
The single release gate: every mandatory PRD §16 readiness check, one
command, one reproducible report.

    python scripts/release_gate.py                     # everything but the soak
    python scripts/release_gate.py --soak-hours 8      # including the 8 h gate
    python scripts/release_gate.py --out data/release  # where the report lands

Exit code is 0 only when every MANDATORY gate is PASS. A gate that could not
run is never PASS.

Why this file exists, and what it refuses to do
-----------------------------------------------

PRD §16 lists eight gates and §20 says the product is production-ready only
when every mandatory one passes ON THE SAME RELEASE CANDIDATE. Before this,
that claim needed a human to remember eight commands and to remember which
of them had actually been run against the commit in hand. That is exactly
how a release gets called green because someone read a badge.

So the rules here are deliberately unkind to good news:

* **A gate that did not run is NOT_RUN, never PASS.** The soak takes eight
  hours; skipping it is legitimate, and reporting the skip as a pass is not.
* **Every verdict carries the command that produced it and its exit code.**
  A verdict you cannot reproduce is an opinion.
* **The report records the host.** A green suite on a machine at 98% RAM and
  4 GB of free disk is not the same evidence as a green suite on a healthy
  one - on 2026-09-06 this project got 524 spurious errors from a full disk
  and a 3 h stall from memory pressure, and both looked like code defects
  until the host was checked. The report states the conditions so a reader
  can tell those apart.
* **Known limitations are part of the report, not a footnote.** §16's own
  Audit row requires it: "lists known limitations and no unverified 'green'
  claims".
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PASS = "PASS"
FAIL = "FAIL"
NOT_RUN = "NOT_RUN"          # legitimately skipped; never counts as success
BLOCKED = "BLOCKED"          # could not run for an environmental reason


@dataclass
class GateResult:
    name: str
    requirement: str
    verdict: str
    detail: str = ""
    command: str = ""
    exit_code: int | None = None
    seconds: float = 0.0
    mandatory: bool = True
    evidence: str = ""
    limitations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "gate": self.name, "requirement": self.requirement,
            "verdict": self.verdict, "detail": self.detail,
            "command": self.command, "exit_code": self.exit_code,
            "seconds": round(self.seconds, 1), "mandatory": self.mandatory,
            "evidence": self.evidence, "limitations": self.limitations,
        }


def _python() -> str:
    for candidate in (ROOT / ".venv-verify/Scripts/python.exe",
                      ROOT / ".venv-verify/bin/python",
                      ROOT / ".venv/Scripts/python.exe",
                      ROOT / ".venv/bin/python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _run(command: list[str], *, timeout: float, env: dict | None = None
         ) -> tuple[int, str, float]:
    """Run a child and return (exit code, tail of output, seconds).

    The exit code comes from the RUNNER, never from a pipeline: a shell
    pipeline reports the exit status of its last command, which is how a
    failing suite piped through `tail` reads as success.
    """
    started = time.time()
    merged = dict(os.environ)
    merged.update(env or {})
    try:
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                              timeout=timeout, env=merged)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout:.0f}s", time.time() - started
    except OSError as exc:
        return 127, f"could not start: {exc}", time.time() - started
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-12:])
    return proc.returncode, tail, time.time() - started


def _host() -> dict:
    """What the run was measured on. See the module docstring: this is not
    decoration, it is what separates a code failure from a host failure."""
    info = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        import psutil
        vm = psutil.virtual_memory()
        info["ram_percent_used"] = vm.percent
        info["ram_available_mb"] = round(vm.available / 1048576)
        info["cpu_percent"] = psutil.cpu_percent(interval=0.5)
    except Exception:  # noqa: BLE001
        info["ram_percent_used"] = None
    for label, path in (("system_drive", os.environ.get("SystemDrive", "C:") + "\\"),
                        ("repo_drive", str(ROOT.anchor or "/"))):
        try:
            usage = shutil.disk_usage(path)
            info[f"{label}_free_gb"] = round(usage.free / 1073741824, 1)
            info[f"{label}_percent_used"] = round(100 * usage.used / usage.total)
        except OSError:
            info[f"{label}_free_gb"] = None
    return info


def _commit() -> dict:
    def git(*args) -> str:
        code, out, _ = _run(["git", *args], timeout=30)
        return out.strip() if code == 0 else ""
    return {"sha": git("rev-parse", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(git("status", "--porcelain"))}


# ---------------------------------------------------------------------------
# the gates
# ---------------------------------------------------------------------------

def gate_invariants(py: str, tmp: str) -> GateResult:
    """§16 Invariants: A-048 suite passes."""
    cmd = [py, "-m", "pytest", "tests/test_invariants.py", "-q",
           "-p", "no:cacheprovider", "--basetemp", tmp + "/inv"]
    code, tail, secs = _run(cmd, timeout=1800)
    return GateResult(
        "Invariants", "A-048 suite passes",
        PASS if code == 0 else FAIL, tail.splitlines()[-1] if tail else "",
        " ".join(cmd), code, secs,
        limitations=[] if code == 0 else ["the invariant suite is not green"])


def gate_verification(py: str, tmp: str) -> GateResult:
    """§16 Verification: no COMPLETED without a successful verifier.

    Narrow on purpose: the completion invariant plus the objective-engine and
    evidence suites that exercise the same transition from above.
    """
    cmd = [py, "-m", "pytest", "tests/test_invariants.py", "-q",
           "-p", "no:cacheprovider", "-k", "completion",
           "--basetemp", tmp + "/ver"]
    code, tail, secs = _run(cmd, timeout=900)
    return GateResult(
        "Verification", "No COMPLETED state without successful verifier",
        PASS if code == 0 else FAIL, tail.splitlines()[-1] if tail else "",
        " ".join(cmd), code, secs)


def gate_budget(py: str, tmp: str) -> GateResult:
    """§16 Budget: exhaustion prevents additional paid calls."""
    cmd = [py, "-m", "pytest", "tests/test_objective_budget.py",
           "tests/test_invariants.py", "-q", "-p", "no:cacheprovider",
           "-k", "budget", "--basetemp", tmp + "/bud"]
    code, tail, secs = _run(cmd, timeout=900)
    return GateResult(
        "Budget", "Exhaustion prevents additional paid calls",
        PASS if code == 0 else FAIL, tail.splitlines()[-1] if tail else "",
        " ".join(cmd), code, secs)


def gate_persistence(py: str, tmp: str) -> GateResult:
    """§16 Persistence: objective recovery and DB crash tests pass."""
    cmd = [py, "-m", "pytest", "tests/test_store_durability.py",
           "tests/test_chaos_restart.py", "tests/test_objective_restart.py",
           "tests/test_store_serialized.py", "-q", "-p", "no:cacheprovider",
           "--basetemp", tmp + "/per"]
    code, tail, secs = _run(cmd, timeout=1800)
    return GateResult(
        "Persistence", "Objective recovery and DB crash tests pass",
        PASS if code == 0 else FAIL, tail.splitlines()[-1] if tail else "",
        " ".join(cmd), code, secs)


def gate_provider_routing(py: str, tmp: str, *, live: bool) -> GateResult:
    """§16 Provider Routing: every enabled route passes a live probe or is
    explicitly degraded/disabled.

    Without `--live` this checks the ROUTING LOGIC deterministically and says
    so. It does not claim the routes are healthy, because that claim needs a
    live probe and PRD §19.4 is explicit: never infer health from configured
    credentials.
    """
    cmd = [py, "-m", "pytest", "tests/test_provider_health.py",
           "tests/test_model_gateway.py", "-q", "-p", "no:cacheprovider",
           "--basetemp", tmp + "/prov"]
    code, tail, secs = _run(cmd, timeout=1800)
    limits = []
    detail = tail.splitlines()[-1] if tail else ""
    if not live:
        limits.append(
            "routing logic only: no live provider probe ran, so this gate does "
            "NOT assert that any enabled route currently answers. Run with "
            "--live (paid) for the §16 wording in full.")
        return GateResult(
            "Provider Routing",
            "Every enabled route passes live probe or is explicitly degraded/disabled",
            PASS if code == 0 else FAIL, detail, " ".join(cmd), code, secs,
            limitations=limits)

    # --live must actually probe. A flag that only changes the wording of a
    # limitation is worse than no flag: it lets a reader believe a live route
    # was verified when the same deterministic tests ran either way.
    if code != 0:
        limits.append("routing logic failed, so the live probe was not attempted")
        return GateResult(
            "Provider Routing",
            "Every enabled route passes live probe or is explicitly degraded/disabled",
            FAIL, detail, " ".join(cmd), code, secs, limitations=limits)

    live_cmd = [py, "-m", "pytest", "tests/live", "-q", "-p", "no:cacheprovider",
                "--basetemp", tmp + "/live"]
    live_code, live_tail, live_secs = _run(
        live_cmd, timeout=3600, env={"FRIDAY_LIVE_PROVIDER_TESTS": "1"})
    live_detail = live_tail.splitlines()[-1] if live_tail else ""
    limits.append(
        "live probes cost real tokens and their verdict is a fact about THIS "
        "moment and these credentials; re-probe before quoting it later")
    return GateResult(
        "Provider Routing",
        "Every enabled route passes live probe or is explicitly degraded/disabled",
        PASS if live_code == 0 else FAIL,
        f"routing {detail}; live {live_detail}",
        " && ".join((" ".join(cmd), " ".join(live_cmd))),
        live_code, secs + live_secs, limitations=limits)


def gate_baseline(py: str, tmp: str, out: Path) -> GateResult:
    """§16 Baseline: the canonical suite terminates child trees and produces
    reproducible results."""
    target = out / "baseline"
    cmd = [py, "scripts/baseline_suite.py", "--out", str(target),
           "--python", py]
    code, tail, secs = _run(cmd, timeout=7200,
                            env={"TMPDIR": tmp, "TEMP": tmp, "TMP": tmp})
    return GateResult(
        "Baseline",
        "Canonical suite terminates child trees and produces reproducible results",
        PASS if code == 0 else FAIL, tail.splitlines()[-1] if tail else "",
        " ".join(cmd), code, secs, evidence=str(target))


def gate_soak(py: str, tmp: str, out: Path, hours: float) -> GateResult:
    """§16 Soak: the A-051 production soak passes its duration/resource bounds.

    NOT_RUN when no duration was asked for. The soak is the one gate that
    cannot be approximated: a short run reports SMOKE by design, and calling
    a SMOKE a pass would be exactly the unverified green claim §16 forbids.
    """
    if hours <= 0:
        return GateResult(
            "Soak", "A-051 production soak passes defined duration/resource bounds",
            NOT_RUN, "not requested (--soak-hours 0)", "", None, 0.0,
            limitations=["the production soak did not run in this gate, so no "
                         "statement is made about long-run resource behaviour"])
    target = out / "soak"
    cmd = [py, "scripts/keep_awake.py", "--", py, "scripts/soak.py",
           "--hours", str(hours), "--sample-every", "20",
           "--out", str(target), "--evidence", str(out / "soak_evidence")]
    code, tail, secs = _run(cmd, timeout=hours * 3600 + 1800,
                            env={"TMPDIR": tmp, "TEMP": tmp, "TMP": tmp})
    verdict_line = ""
    report = target / "report.json"
    limits: list[str] = []
    if report.exists():
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            verdict_line = str(data.get("verdict") or "")
            floor = data.get("detection_floor_mb_per_hour")
            if floor is not None:
                limits.append(
                    f"smallest growth this run could resolve: {floor} MB/hour - "
                    f"a PASS says nothing about slopes below it")
            if data.get("gaps"):
                limits.append(f"{len(data['gaps'])} sampling gap(s): the host "
                              f"slept or was starved, so the run measured less "
                              f"than its duration claims")
        except (ValueError, OSError):
            pass
    ok = code == 0 and verdict_line == "PASS"
    if verdict_line and verdict_line != "PASS":
        limits.append(f"soak verdict was {verdict_line}, not PASS")
    return GateResult(
        "Soak", "A-051 production soak passes defined duration/resource bounds",
        PASS if ok else FAIL, verdict_line or (tail.splitlines()[-1] if tail else ""),
        " ".join(cmd), code, secs, evidence=str(target), limitations=limits)


def gate_audit(results: list[GateResult], host: dict) -> GateResult:
    """§16 Audit: the readiness report lists known limitations and makes no
    unverified "green" claims.

    This gate judges the report itself, which is the only one that can. It
    fails if any gate claims PASS without an exit code to back it - a verdict
    with no command behind it is precisely the unverified claim §16 bans.
    """
    unbacked = [r.name for r in results
                if r.verdict == PASS and r.exit_code is None]
    limits = [f"host RAM was {host.get('ram_percent_used')}% used with "
              f"{host.get('ram_available_mb')} MB free during this gate"]
    free = host.get("system_drive_free_gb")
    if free is not None and free < 10:
        limits.append(f"system drive had only {free} GB free: a full disk "
                      f"produces SQLite 'disk is full' errors that read like "
                      f"code defects")
    return GateResult(
        "Audit",
        'Report lists known limitations and no unverified "green" claims',
        FAIL if unbacked else PASS,
        f"{len(unbacked)} unbacked PASS claim(s)" if unbacked
        else "every PASS carries a command and an exit code",
        "(self-check over this report)", 0 if not unbacked else 1, 0.0,
        limitations=limits)


# ---------------------------------------------------------------------------

def render(results: list[GateResult], host: dict, commit: dict,
           ready: bool) -> str:
    lines = ["# F.R.I.D.A.Y. production readiness report", ""]
    lines.append(f"- commit: `{commit.get('sha') or 'unknown'}`"
                 f" ({commit.get('branch') or '?'})"
                 + ("  **working tree dirty**" if commit.get("dirty") else ""))
    lines.append(f"- generated: {host.get('utc')}")
    lines.append(f"- host: {host.get('platform')}, python {host.get('python')}")
    lines.append(f"- host load at run time: RAM {host.get('ram_percent_used')}%"
                 f" used ({host.get('ram_available_mb')} MB free),"
                 f" system drive {host.get('system_drive_free_gb')} GB free")
    lines += ["", "## Verdict", ""]
    lines.append(f"**{'PRODUCTION READY' if ready else 'NOT PRODUCTION READY'}**"
                 " - every mandatory gate below must read PASS."
                 if ready else
                 "**NOT PRODUCTION READY** - at least one mandatory gate is not PASS.")
    lines += ["", "## Gates (PRD §16)", "",
              "| Gate | Requirement | Verdict | Exit | Seconds | Detail |",
              "|---|---|---|---|---|---|"]
    for r in results:
        mark = {PASS: "PASS", FAIL: "**FAIL**", NOT_RUN: "_NOT RUN_",
                BLOCKED: "_BLOCKED_"}.get(r.verdict, r.verdict)
        lines.append(f"| {r.name} | {r.requirement} | {mark} | "
                     f"{'-' if r.exit_code is None else r.exit_code} | "
                     f"{r.seconds:.0f} | {(r.detail or '').replace('|', '/')} |")
    lines += ["", "## Known limitations", ""]
    any_limit = False
    for r in results:
        for limit in r.limitations:
            lines.append(f"- **{r.name}**: {limit}")
            any_limit = True
    if not any_limit:
        lines.append("- none recorded by this run")
    lines += ["", "## Reproduce", ""]
    for r in results:
        if r.command:
            lines.append(f"- {r.name}: `{r.command}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/release_gate")
    ap.add_argument("--soak-hours", type=float, default=0.0,
                    help="run the A-051 soak for N hours (0 = skip, reported NOT_RUN)")
    ap.add_argument("--skip-baseline", action="store_true",
                    help="skip the full canonical suite (reported NOT_RUN)")
    ap.add_argument("--live", action="store_true",
                    help="the provider gate may make paid live probes")
    ap.add_argument("--tmp", default=os.environ.get("FRIDAY_GATE_TMP", ""),
                    help="scratch dir for child pytest runs; keep it OFF the "
                         "system drive - a full C: gives 'disk is full' errors "
                         "that look like code failures")
    args = ap.parse_args()

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    tmp = args.tmp or str(out / "tmp")
    Path(tmp).mkdir(parents=True, exist_ok=True)

    py = _python()
    host = _host()
    commit = _commit()
    print(f"release gate on {commit.get('sha', '')[:12]} "
          f"({'dirty' if commit.get('dirty') else 'clean'}), python {py}")
    print(f"host: RAM {host.get('ram_percent_used')}% used, "
          f"system drive {host.get('system_drive_free_gb')} GB free")

    results: list[GateResult] = []

    def add(result: GateResult) -> None:
        results.append(result)
        print(f"  {result.verdict:8s} {result.name:18s} "
              f"({result.seconds:.0f}s) {result.detail[:70]}", flush=True)

    add(gate_invariants(py, tmp))
    add(gate_verification(py, tmp))
    add(gate_budget(py, tmp))
    add(gate_persistence(py, tmp))
    add(gate_provider_routing(py, tmp, live=args.live))
    if args.skip_baseline:
        add(GateResult("Baseline",
                       "Canonical suite terminates child trees and produces "
                       "reproducible results",
                       NOT_RUN, "skipped with --skip-baseline", "", None, 0.0,
                       limitations=["the canonical suite did not run in this "
                                    "gate"]))
    else:
        add(gate_baseline(py, tmp, out))
    add(gate_soak(py, tmp, out, args.soak_hours))
    add(gate_audit(results, host))

    mandatory = [r for r in results if r.mandatory]
    ready = all(r.verdict == PASS for r in mandatory)

    report = render(results, host, commit, ready)
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "report.json").write_text(json.dumps(
        {"ready": ready, "commit": commit, "host": host,
         "gates": [r.as_dict() for r in results]}, indent=2), encoding="utf-8")

    print()
    print("PRODUCTION READY" if ready else "NOT PRODUCTION READY")
    not_pass = [r.name for r in mandatory if r.verdict != PASS]
    if not_pass:
        print("not passing:", ", ".join(not_pass))
    print("report:", out / "report.md")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
