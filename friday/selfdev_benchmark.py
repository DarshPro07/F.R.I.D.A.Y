"""
FR-050: benchmark before promotion - the measurement a self-change is held
to.

`selfdev.SelfDevelopment.benchmark` compares `measure(sandbox)` against a
`before` dict and rejects on regression past BENCHMARK_TOLERANCE. Until now
the production loop passed no `measure`, so every candidate reached
BENCHMARKED as "skipped: no performance claim" - a gate that could not
fail. This module supplies the measurement:

  * `measure(root)` runs the cheap, deterministic parts of
    `scripts/perf_profile.py` (memory retrieval P95, simple-action latency,
    manifest bytes per capability, routing accuracy) *inside the given
    checkout* - a subprocess with `root` first on `sys.path`, so the sandbox's
    code is what gets measured, not the live tree's.
  * `baseline()` is the same measurement on the live tree, taken once per
    promotion and cached for the session.
  * `claims(files)` decides whether a change even has a performance claim:
    touching the planner, router, memory stack, store or capability
    registry does; a docs-only or test-only change does not, and is
    recorded as skipped (that is honest, not a bypass - the benchmark
    would measure nothing the change could move).

Metrics where lower is better are named so the comparison goes the right
way. A measurement that cannot run (the sandbox does not import) is a
FAILED measurement, which regresses every metric and rejects.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

#: Files whose change carries a performance claim (prefix match on the
#: repo-relative path). Anything else is measured as "no claim".
PERF_SENSITIVE = (
    "friday/planner", "friday/semantics.py", "friday/capability_router.py",
    "friday/capabilities.py", "friday/memory_stack.py", "friday/store.py",
    "friday/manifest.py", "friday/continuous.py", "friday/capability_runtime.py",
    "friday/objectives.py", "friday/golden.py", "friday/model_gateway.py",
)

#: Lower is better for these; the rest are higher-is-better.
LOWER_IS_BETTER = ("memory_p95_ms", "action_p95_s", "manifest_bytes_per_capability")

#: A small, deterministic sample: the promotion gate must finish in well
#: under a minute per side, and the numbers only need to catch a
#: regression past BENCHMARK_TOLERANCE (10%), not publish a profile.
MEMORIES = 600
QUERIES = 60
ACTION_RUNS = 6

_PROBE = r"""
import json, statistics, sys, tempfile, time
from pathlib import Path
root = sys.argv[1]
sys.path.insert(0, root)
out = {}
def p(xs, q):
    s = sorted(xs); return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]
try:
    from friday.store import Store
    from friday import memory_stack as MS
    from friday.toolsets import memory as M
    d = Path(tempfile.mkdtemp(prefix="bench-mem-"))
    store = Store(str(d / "m.sqlite3"))
    old = M.store
    M.store = lambda: store
    try:
        for i in range(%(memories)d):
            store.remember(subject=f"topic {i %% 97} item {i}", value=f"value {i} about project p{i %% 13}",
                           kind="FACT", source="bench", scope="user",
                           project_scope=f"p{i %% 13}" if i %% 3 == 0 else "")
        times = []
        for i in range(%(queries)d):
            t = time.perf_counter()
            MS.aggregate(f"project p{i %% 13} topic {i %% 97}", budget_tokens=800,
                         include_episodes=False, project_scope=f"p{i %% 13}")
            times.append((time.perf_counter() - t) * 1000)
        out["memory_p95_ms"] = round(p(times, 0.95), 2)
    finally:
        M.store = old
        store.close()
    from friday import manifest as MF
    from friday import capabilities as C
    out["manifest_bytes_per_capability"] = round(len(json.dumps(MF.summary(), default=str)) / max(1, len(C.CAPABILITIES)), 2)
    from friday import capability_router as R
    from friday import planner as P
    class _T:
        def __init__(self, name, description):
            self.name = name
            self.info = type("I", (), {"name": name, "raw_schema": {"description": description, "parameters": {}}})()
    router = R.Router(); router.load([_T(c.id, c.description) for c in C._ALL])
    total = top1 = planned = 0
    for cap in C._ALL:
        for phrase in cap.intent_examples[:1]:
            total += 1
            ranked = [m["capability"] for m in router.search(phrase, limit=3)]
            top1 += ranked[:1] == [cap.id]
            plan = P.plan_objective(phrase)
            got = plan.goals[0].capability if plan.goals else ""
            planned += got == cap.id
    out["router_top1"] = round(top1 / total, 4) if total else 0.0
    out["planner_correct"] = round(planned / total, 4) if total else 0.0
    from friday import golden as G
    cases = {c.id: c for c in G.load(G.CORPUS)}
    case = cases.get("GO-general-011") or next(iter(cases.values()))
    d2 = Path(tempfile.mkdtemp(prefix="bench-act-"))
    secs = []
    for i in range(%(runs)d):
        rep = G.run([case], workdir=d2 / f"a{i}")
        secs.append(rep.results[0].seconds)
    out["action_p95_s"] = round(p(secs, 0.95), 3)
    out["ok"] = True
except Exception as exc:
    out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
print("BENCH-JSON " + json.dumps(out))
"""


def claims(files) -> bool:
    """Does this set of changed files carry a performance claim?"""
    for f in files:
        rel = str(f).replace("\\", "/")
        if any(rel.startswith(prefix) or ("/" + prefix) in rel for prefix in PERF_SENSITIVE):
            return True
    return False


def measure(root: Path | str, *, timeout: float = 600.0, python: str | None = None) -> dict:
    """Run the probe inside `root`. A probe that cannot run is a failed
    measurement: every metric is set to the worst value so the comparison
    rejects, and the reason travels in the dict."""
    root = str(Path(root).resolve())
    if not Path(root).is_dir():
        return _failed(f"no such checkout: {root}")
    script = _PROBE % {"memories": MEMORIES, "queries": QUERIES, "runs": ACTION_RUNS}
    env = dict(os.environ)
    env.pop("ADA_DB", None)                      # never the live database
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run([python or sys.executable, "-c", script, root],
                              capture_output=True, text=True, timeout=timeout,
                              cwd=root, env=env)
    except subprocess.TimeoutExpired:
        return _failed(f"measurement timed out after {timeout:.0f}s")
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("BENCH-JSON ")), "")
    if not line:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        return _failed(f"no measurement produced (exit {proc.returncode}): {' | '.join(tail)}")
    out = json.loads(line[len("BENCH-JSON "):])
    if not out.get("ok"):
        return _failed(out.get("error", "unknown"))
    out.pop("ok", None)
    return out


def _failed(reason: str) -> dict:
    return {"memory_p95_ms": float("inf"), "action_p95_s": float("inf"),
            "manifest_bytes_per_capability": float("inf"),
            "router_top1": 0.0, "planner_correct": 0.0, "failed": reason}


_BASELINE: dict | None = None


def baseline(root: Path | str, *, refresh: bool = False) -> dict:
    """The live tree's numbers, measured once per process."""
    global _BASELINE
    if _BASELINE is None or refresh:
        _BASELINE = measure(root)
    return _BASELINE
