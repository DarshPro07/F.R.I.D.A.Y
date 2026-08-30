#!/usr/bin/env python3
"""
Phase 1C golden journeys (§27): files, inside a jail.

    python scripts/golden_1c.py

Works in a temporary workspace so nothing real is touched, and finishes with
adversarial escape attempts that must all be refused.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from friday import contracts as c  # noqa: E402
from friday.fsjail import FileJail  # noqa: E402
from friday.policy import PolicyEngine  # noqa: E402
from friday.toolsets import files as F  # noqa: E402
from friday.toolsets.system import needs_approval  # noqa: E402

NOISY = {"text", "entries", "results"}


def show(label: str, result: c.ActionResult) -> bool:
    ok = result.may_claim_completion
    mark = "PASS" if ok else ("ASK " if needs_approval(result) else "FAIL")
    print(f"[{mark}] {label}")
    print(f"       status={result.status}  may_claim_completion={ok}")
    if result.verification:
        print(f"       verify: {result.verification.method}")
        print(f"               {result.verification.evidence}")
    if result.error:
        print(f"       error: {result.error[:180]}")
    if isinstance(result.output, dict):
        trimmed = {k: v for k, v in result.output.items() if k not in NOISY}
        print(f"       output: {json.dumps(trimmed, default=str)[:200]}")
    if result.artifacts:
        for artifact in result.artifacts:
            print(f"       artifact: {artifact.type} {artifact.artifact_id} "
                  f"-> {artifact.path_or_uri}")
    print()
    return ok


def refused(label: str, result: c.ActionResult) -> bool:
    ok = result.status == "failed" and not result.may_claim_completion
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    print(f"       status={result.status}  error: {(result.error or '')[:120]}\n")
    return ok


def main() -> int:
    results: list[bool] = []
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir()
        outside = Path(tmp) / "outside_secret.txt"
        outside.write_text("classified material")

        F.reset_jail(FileJail(roots=(workspace,)))
        engine = PolicyEngine()

        print("=" * 66)
        print(f"WORKSPACE: {workspace}")
        print(f"JAIL     : {F.jail().describe()}")
        print("=" * 66 + "\n")

        print("=" * 66)
        print('JOURNEY: "Create a note, then read it back and summarize."')
        print("=" * 66)
        run = c.Run.create("Create and read a note.", capability="files")

        gated = F.files_create(run, str(workspace / "notes.txt"), "x", engine=engine)
        print(f"[GATE] files.create without approval -> {gated.status} "
              f"(needs_approval={needs_approval(gated)})\n")
        results.append(needs_approval(gated))

        engine.approve_for_session("files.create")
        engine.approve_for_session("files.write")
        engine.approve_for_session("files.edit")
        engine.approve_for_session("files.copy")
        engine.approve_for_session("files.move")

        content = (
            "Arc Reactor project notes\n"
            "-------------------------\n"
            "Language: Python\n"
            "Database: SQLite\n"
            "Status: Phase 1C in progress\n"
        )
        results.append(show("files.create (approved)",
                            F.files_create(run, str(workspace / "notes.txt"),
                                           content, engine=engine)))
        read = F.files_read(run, str(workspace / "notes.txt"))
        results.append(show("files.read", read))
        if isinstance(read.output, dict):
            print("       --- file content as the agent sees it ---")
            for line in read.output["text"].splitlines():
                print(f"       | {line}")
            print()

        print("=" * 66)
        print("JOURNEY: edit, copy, move — each verified")
        print("=" * 66)
        results.append(show("files.edit",
                            F.files_edit(run, str(workspace / "notes.txt"),
                                         "Phase 1C in progress", "Phase 1C complete",
                                         engine=engine)))
        results.append(show("files.copy",
                            F.files_copy(run, str(workspace / "notes.txt"),
                                         str(workspace / "backup.txt"), engine=engine)))
        results.append(show("files.move",
                            F.files_move(run, str(workspace / "backup.txt"),
                                         str(workspace / "archive" / "backup.txt"),
                                         engine=engine)))

        print("=" * 66)
        print("JOURNEY: search and list")
        print("=" * 66)
        results.append(show("files.list", F.files_list(run, str(workspace))))
        found = F.files_search(run, "*.txt", root=str(workspace), contains="SQLite")
        results.append(show("files.search (contains 'SQLite')", found))
        if isinstance(found.output, dict):
            for hit in found.output["results"]:
                print(f"         - {hit['path']}")
            print()

        print("=" * 66)
        print("JOURNEY: the jail must refuse every escape")
        print("=" * 66)
        (workspace / ".env").write_text("OPENAI_API_KEY=sk-real-looking-secret")

        results.append(refused(
            "parent traversal",
            F.files_read(run, str(workspace / ".." / "outside_secret.txt"))))
        results.append(refused(
            "absolute path outside the root",
            F.files_read(run, str(outside))))
        results.append(refused(
            ".env inside the root (denylist)",
            F.files_read(run, str(workspace / ".env"))))
        results.append(refused(
            "write escaping the root",
            F.files_write(run, str(Path(tmp) / "escaped.txt"), "x", engine=engine)))
        results.append(refused(
            "copy to a destination outside the root",
            F.files_copy(run, str(workspace / "notes.txt"),
                         str(Path(tmp) / "leaked.txt"), engine=engine)))

        leaked = list(Path(tmp).glob("*.txt"))
        clean = {p.name for p in leaked} == {"outside_secret.txt"}
        print(f"[{'PASS' if clean else 'FAIL'}] nothing was written outside the "
              f"workspace: {[p.name for p in leaked]}\n")
        results.append(clean)

        F.reset_jail(None)

    passed = sum(1 for r in results if r)
    print("=" * 66)
    print(f"RESULT: {passed}/{len(results)} journeys behaved correctly")
    print("=" * 66)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
