"""Chaos suite report (PRD V7 CHAOS-001 acceptance evidence).

Runs every test named in FAULT_MAP (tests/test_chaos_suite.py) and writes a
per-fault pass/fail report. A fault is green only when every test mapped to
it was collected and passed; a skipped, missing or failing test makes it red,
and the exit code is 1 unless all nine PRD faults are green.

    python scripts/chaos_report.py                  # run, write the report
    python scripts/chaos_report.py --python <exe>   # run under another interpreter
    python scripts/chaos_report.py --out-dir <dir>  # default docs/implementation
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "test_chaos_suite.py"
REPORT_NAME = "CHAOS_SUITE_REPORT"


def _literal(name: str) -> object:
    """A module-level literal from the suite, read with ast so the report does
    not import (and so cannot be broken by) the test module."""
    for node in ast.parse(SUITE.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f"{SUITE.name} defines no {name}")


def load_fault_map() -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    return tuple(_literal("PRD_FAULTS")), dict(_literal("FAULT_MAP"))


def _outcome(case: ET.Element) -> str:
    if case.find("failure") is not None or case.find("error") is not None:
        return "FAIL"
    if case.find("skipped") is not None:
        return "SKIP"
    return "PASS"


def run_pytest(python: str, node_ids: list[str], junit: Path) -> tuple[int, float]:
    cmd = [python, "-m", "pytest", *node_ids, "-q", "-p", "no:cacheprovider",
           "--tb=line", f"--junitxml={junit}"]
    started = datetime.now()
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          timeout=1800)
    return proc.returncode, (datetime.now() - started).total_seconds()


def collect_outcomes(junit: Path) -> dict[tuple[str, str], list[str]]:
    """(module stem, test function) -> the outcomes of every parametrization."""
    outcomes: dict[tuple[str, str], list[str]] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        module = case.get("classname", "").split(".")[-1]
        function = case.get("name", "").split("[")[0]
        outcomes.setdefault((module, function), []).append(_outcome(case))
    return outcomes


def judge(fault_map: dict[str, tuple[str, ...]],
          outcomes: dict[tuple[str, str], list[str]],
          evidence: dict[str, str] | None = None) -> list[dict]:
    rows = []
    for fault, node_ids in fault_map.items():
        tests, green = [], True
        for node_id in node_ids:
            rel, _, name = node_id.partition("::")
            seen = outcomes.get((Path(rel).stem, name.split("[")[0]), [])
            verdict = ("MISSING" if not seen else
                       "PASS" if all(o == "PASS" for o in seen) else
                       "FAIL" if "FAIL" in seen else "SKIP")
            green = green and verdict == "PASS"
            tests.append({"node_id": node_id, "cases": len(seen), "result": verdict})
        row = {"fault": fault, "result": "PASS" if green else "FAIL"}
        if evidence is not None:
            row["evidence"] = evidence.get(fault, "")
        rows.append({**row, "tests": tests})
    return rows


def render(rows: list[dict], *, python: str, seconds: float, pytest_exit: int) -> str:
    green = sum(r["result"] == "PASS" for r in rows)
    lines = [
        "# Chaos suite report (CHAOS-001)", "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
        f"with `{python}` in {seconds:.0f}s; pytest exit {pytest_exit}.", "",
        f"**{green}/{len(rows)} faults green.**", "",
        "| Fault | Result | Evidence | Tests (cases) |", "|---|---|---|---|",
    ]
    for row in rows:
        tests = "<br>".join(f"`{t['node_id']}` ({t['cases']}) {t['result']}"
                            for t in row["tests"])
        lines.append(f"| {row['fault']} | {row['result']} | "
                     f"{row.get('evidence', '')} | {tests} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--out-dir", default=str(ROOT / "docs" / "implementation"))
    args = parser.parse_args(argv)

    prd_faults, fault_map = load_fault_map()
    unmapped = sorted(set(prd_faults) - set(fault_map))
    if unmapped:
        print(f"unmapped PRD faults: {unmapped}", file=sys.stderr)
        return 1
    node_ids = sorted({n for ids in fault_map.values() for n in ids})
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "chaos.xml"
        exit_code, seconds = run_pytest(args.python, node_ids, junit)
        if not junit.is_file():
            print(f"pytest produced no results (exit {exit_code})", file=sys.stderr)
            return 1
        rows = judge(fault_map, collect_outcomes(junit),
                     dict(_literal("FAULT_EVIDENCE")))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{REPORT_NAME}.md").write_text(
        render(rows, python=args.python, seconds=seconds, pytest_exit=exit_code),
        encoding="utf-8")
    (out_dir / f"{REPORT_NAME}.json").write_text(
        json.dumps({"faults": rows, "pytest_exit": exit_code}, indent=2) + "\n",
        encoding="utf-8")
    red = [r["fault"] for r in rows if r["result"] != "PASS"]
    print(f"{len(rows) - len(red)}/{len(rows)} faults green"
          + (f"; red: {', '.join(red)}" if red else ""))
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
