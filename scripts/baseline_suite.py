#!/usr/bin/env python3
"""
The deterministic verification gate, as one cross-platform runner (audit
A-014 / A-029): the suite in N chunks against the exact tree, one log per
chunk plus a summary with the commit, the timestamp and the per-chunk
result line. Windows first - no Bash, no MSYS process trees.

    python scripts/baseline_suite.py [--out data/baseline] [--chunks 4]
                                     [--python .venv-verify/Scripts/python.exe]
                                     [--timeout 600] [--chunk-timeout 3600]

Exit code is 0 only when every chunk passed.

Each chunk is a child pytest with a per-test `--timeout` (pytest-timeout)
and a per-chunk wall-clock ceiling; a chunk that exceeds its ceiling is
killed WITH its process tree (audit A-047 - a hung git/Hermes child must not
outlive the test that spawned it) and recorded as `TIMEOUT`, never left to
hold the run for hours.

`scripts/baseline_suite.sh` and `.ps1` are thin wrappers around this file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _default_python() -> str:
    for candidate in (ROOT / ".venv-verify" / "Scripts" / "python.exe",
                      ROOT / ".venv-verify" / "bin" / "python",
                      ROOT / ".venv" / "Scripts" / "python.exe",
                      ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


def _tree_id() -> str:
    """The commit plus a digest of the working-tree diff (tracked files),
    so `--resume` can tell 'the same tree' from 'the same commit with more
    edits on top' - the second must not inherit the first's green chunks."""
    commit = _commit()
    try:
        import hashlib
        diff = subprocess.run(["git", "diff", "HEAD", "--", ".", ":!*.log"], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=60).stdout
        untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--", "friday", "tests", "scripts"],
                                   cwd=str(ROOT), capture_output=True, text=True, timeout=60).stdout
        digest = hashlib.sha1((diff + "\n" + untracked).encode("utf-8", "replace")).hexdigest()[:10]
        return f"{commit}+{digest}" if (diff.strip() or untracked.strip()) else commit
    except Exception:  # noqa: BLE001
        return commit


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate the chunk and everything it spawned."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=60)
    else:
        try:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def run_chunk(python: str, files: list[str], log: Path, *, per_test_timeout: int,
              chunk_timeout: int, marker: str) -> tuple[int, str, float]:
    cmd = [python, "-m", "pytest", *files, "-m", marker, "-q", "-p", "no:cacheprovider",
           f"--timeout={per_test_timeout}"]
    started = time.time()
    with log.open("w", encoding="utf-8") as fh:
        kwargs = {"stdout": fh, "stderr": subprocess.STDOUT, "cwd": str(ROOT)}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **kwargs)
        try:
            code = proc.wait(timeout=chunk_timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            fh.write(f"\nTIMEOUT: chunk exceeded {chunk_timeout}s and was killed with its process tree\n")
            code = 124
    elapsed = time.time() - started
    tail = ""
    try:
        lines = [ln for ln in log.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        tail = lines[-1] if lines else ""
    except OSError:
        pass
    return code, tail, elapsed


def _host_state() -> str:
    """One line of the environment a chunk ran under, so a verdict can be
    read with its cause: on 2026-09-20 chunk2 died twice with exit
    1073807364 (STATUS_CONTROL_C_EXIT) - once from Modern Standby, once
    from a 'Critical Battery Trigger Met' shutdown (Kernel-Power 524) while
    the host sat at 555 MB free. Neither was the code."""
    parts = []
    try:
        import psutil
        vm = psutil.virtual_memory()
        parts.append(f"free_mb={int(vm.available / 1048576)}")
        batt = psutil.sensors_battery()
        if batt is not None:
            parts.append(f"ac={'yes' if batt.power_plugged else 'NO'} battery={int(batt.percent)}%")
    except Exception:  # noqa: BLE001
        parts.append("psutil=unavailable")
    return " ".join(parts)


def _green_chunks(summary: Path) -> set[int]:
    """Chunk indexes whose recorded line says exit=0, for --resume."""
    done: set[int] = set()
    try:
        for line in summary.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("chunk") and " exit=0 " in line:
                done.add(int(line[5:line.index(" ")]))
    except (OSError, ValueError):
        pass
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/baseline")
    ap.add_argument("--chunks", type=int, default=4)
    ap.add_argument("--python", default=_default_python())
    ap.add_argument("--timeout", type=int, default=600, help="per-test timeout (pytest-timeout)")
    ap.add_argument("--chunk-timeout", type=int, default=3600, help="per-chunk wall-clock ceiling")
    ap.add_argument("--marker", default="not live and not slow")
    ap.add_argument("--resume", action="store_true",
                    help="keep the chunks already recorded exit=0 in --out/summary.txt (same tree only) "
                         "and run the rest; a host reboot mid-run must not cost the green chunks")
    args = ap.parse_args(argv)

    out = (ROOT / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "tests").glob("test_*.py"))
    total = len(files)
    chunk = (total + args.chunks - 1) // args.chunks
    summary = out / "summary.txt"
    header = (f"commit={_tree_id()} files={total} chunk={chunk} "
              f"date={dt.datetime.now().astimezone().isoformat(timespec='seconds')} "
              f"python={args.python} platform={sys.platform} host[{_host_state()}]")
    keep: set[int] = set()
    if args.resume and summary.exists():
        first = summary.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        same_tree = bool(first) and first[0].split(" files=")[0] == header.split(" files=")[0] \
            and f" files={total} " in first[0]
        if same_tree:
            keep = _green_chunks(summary)
        with summary.open("a", encoding="utf-8") as fh:
            fh.write(f"resume={sorted(keep)} " + header + "\n")
    else:
        summary.write_text(header + "\n", encoding="utf-8")
    print(header + (f" resume={sorted(keep)}" if keep else ""), flush=True)

    ok = True
    for i in range(args.chunks):
        piece = files[i * chunk:(i + 1) * chunk]
        if not piece:
            continue
        if i in keep:
            print(f"chunk{i} kept (exit=0 on this tree)", flush=True)
            continue
        log = out / f"chunk{i}.log"
        code, tail, elapsed = run_chunk(args.python, piece, log, per_test_timeout=args.timeout,
                                        chunk_timeout=args.chunk_timeout, marker=args.marker)
        line = f"chunk{i} exit={code} ({elapsed:.0f}s) host[{_host_state()}] {tail}"
        with summary.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(line, flush=True)
        ok = ok and code == 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
