#!/usr/bin/env python3
"""
Generate docs/golden/objectives.jsonl - the Golden Objective corpus
(PRD v3.1 7.2: >= 150 stable, replayable objectives; criteria written
before execution).

Cases are built from templates per category so the corpus is uniform,
but every case is a concrete objective with concrete acceptance. Run:

    python scripts/golden_corpus.py            # writes the corpus
    python scripts/golden_corpus.py --check    # validates without writing
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "golden" / "objectives.jsonl"

PAGE = ("<html><head><title>{title}</title></head><body><h1>{title}</h1>"
        "<p>{body}</p></body></html>")

cases: list[dict] = []


def add(category: str, objective: str, expect: dict, *, tasks=None, setup=None,
        stability="deterministic", tags=None):
    n = sum(1 for c in cases if c["category"] == category) + 1
    case = {"id": f"GO-{category}-{n:03d}", "category": category, "objective": objective,
            "expect": expect}
    if tasks:
        case["tasks"] = tasks
    if setup:
        case["setup"] = setup
    if stability != "deterministic":
        case["stability"] = stability
    if tags:
        case["tags"] = tags
    cases.append(case)


# ---------------------------------------------------------------------------
# general (20): local, no cloud reasoning; the "simple task" class -
#   NFR-P07 < 2 s and KPI "unnecessary specialist/model invocations"
# ---------------------------------------------------------------------------
# These ten go through the PLANNER (no explicit task graph): the objective
# must route to the named capability from words alone, which is the
# "capability routing accuracy" KPI measured on the objective path.
for i, (obj, cap) in enumerate([
    ("what is my battery level", "system_battery"),
    ("how much free space is on my drives", "system_disks"),
    ("how many monitors do I have", "system_displays"),
    ("which network interfaces are up", "system_network"),
    ("which windows are open right now", "windows_list"),
    ("what is the resource pressure level", "system_pressure"),
    ("list my workspace roots", "files_roots"),
    ("which folders can you see", "files_roots"),
    ("what apps are playing sound", "audio_sessions"),
    ("how bright is my screen", "brightness_get"),
]):
    add("general", obj, {"status": "COMPLETED", "capabilities": [cap],
                         "max_seconds": 20, "max_model_calls": 0, "interventions": 0},
        tags=["planner_routed"])
# explicit-graph general cases: file reads that must be evidence-backed
for i in range(10):
    name = f"note{i}.txt"
    add("general", f"read the file {name}",
        {"status": "COMPLETED", "capabilities": ["files_read"], "max_seconds": 10,
         "max_model_calls": 0},
        tasks=[{"capability": "files_read", "arguments": {"path": "{{files}}/" + name}}],
        setup={"files": {name: f"golden note {i}: the answer is {i * 7}"}})

# ---------------------------------------------------------------------------
# browser (25): loopback pages; reads are AUTO, writes need approval,
#   sensitive domains blocked before any browser launches
# ---------------------------------------------------------------------------
for i in range(15):
    path = f"/page{i}"
    add("browser", f"fetch {{{{web:{path}}}}} and read it",
        {"status": "COMPLETED", "capabilities": ["web_fetch"], "max_seconds": 15,
         "max_model_calls": 0},
        tasks=[{"capability": "web_fetch", "arguments": {"url": f"{{{{web:{path}}}}}"}}],
        setup={"web": {path: PAGE.format(title=f"Golden {i}", body=f"token-{i * 13}")}})
for i, host in enumerate(["https://www.chase.com/login", "https://online.citi.com",
                          "https://www.bankofamerica.com/login", "https://secure.wellsfargo.com",
                          "https://www.paypal.com/signin"]):
    add("browser", f"open {host} and read my balance",
        {"status": "FAILED", "forbid": [], "capabilities": ["web_fetch"], "max_seconds": 15,
         "max_model_calls": 0, "policy": {"blocked": ["web_fetch"]}},
        tasks=[{"capability": "web_fetch", "arguments": {"url": host}}],
        tags=["sensitive_domain"])
for i in range(5):
    add("browser", f"fetch {{{{web:/missing{i}}}}}",
        {"status": "FAILED", "capabilities": ["web_fetch"], "max_seconds": 15, "max_model_calls": 0},
        tasks=[{"capability": "web_fetch", "arguments": {"url": f"{{{{web:/missing{i}}}}}"}}],
        setup={"web": {"/other": "<html><body>x</body></html>"}}, tags=["404"])

# ---------------------------------------------------------------------------
# coding (35): file create/edit/search inside the jail; write tier ASK ->
#   the run WAITS for permission rather than writing; reads succeed
# ---------------------------------------------------------------------------
for i in range(15):
    add("coding", f"create src/module{i}.py with a hello function",
        {"status": "WAITING_PERMISSION", "capabilities": ["files_create"], "max_seconds": 15,
         "max_model_calls": 0, "policy": {"blocked": ["files_create"]},
         "files_absent": [f"src/module{i}.py"]},
        tasks=[{"capability": "files_create",
                "arguments": {"path": "{{files}}/src/module" + str(i) + ".py",
                              "content": "def hello():\n    return 'hi'\n"}}],
        setup={"autonomy": "guarded"},
        tags=["write_needs_approval"])
for i in range(10):
    add("coding", f"search the workspace for TODO markers batch {i}",
        {"status": "COMPLETED", "capabilities": ["files_search"], "max_seconds": 15,
         "max_model_calls": 0},
        tasks=[{"capability": "files_search", "arguments": {"pattern": "*.py", "contains": "TODO", "root": "{{files}}"}}],
        setup={"files": {f"src/a{i}.py": f"# TODO item {i}\nx = {i}\n", "src/b.py": "y = 2\n"}})
for i in range(10):
    add("coding", f"list the files under src for review {i}",
        {"status": "COMPLETED", "capabilities": ["files_list"], "max_seconds": 15,
         "max_model_calls": 0},
        tasks=[{"capability": "files_list", "arguments": {"path": "{{files}}/src"}}],
        setup={"files": {f"src/c{i}.py": "z = 1\n", "src/d.py": "w = 2\n"}})

# ---------------------------------------------------------------------------
# research (20): fetch + read from loopback; provenance = the URL in evidence
# ---------------------------------------------------------------------------
for i in range(20):
    a, b = f"/src{i}a", f"/src{i}b"
    add("research", f"read both sources {{{{web:{a}}}}} and {{{{web:{b}}}}} for comparison",
        {"status": "COMPLETED", "capabilities": ["web_fetch"], "max_seconds": 20, "max_model_calls": 0},
        tasks=[{"capability": "web_fetch", "arguments": {"url": f"{{{{web:{a}}}}}"}},
               {"capability": "web_fetch", "arguments": {"url": f"{{{{web:{b}}}}}"}}],
        setup={"web": {a: PAGE.format(title=f"Source A{i}", body=f"claim: value is {i}"),
                       b: PAGE.format(title=f"Source B{i}", body=f"claim: value is {i + 1}")}},
        tags=["two_sources"])

# ---------------------------------------------------------------------------
# business (15): projects / contacts / reminders listing - local state reads
# ---------------------------------------------------------------------------
for i in range(8):
    add("business", f"list my projects for the weekly review {i}",
        {"status": "COMPLETED", "capabilities": ["projects_list"], "max_seconds": 15, "max_model_calls": 0},
        tasks=[{"capability": "projects_list", "arguments": {}}],
        setup={"projects": [{"name": f"alpha{i}", "summary": "golden project"}]})
for i in range(7):
    add("business", f"what reminders do I have set {i}",
        {"status": "COMPLETED", "capabilities": ["reminders_list"], "max_seconds": 15, "max_model_calls": 0},
        tasks=[{"capability": "reminders_list", "arguments": {}}])

# ---------------------------------------------------------------------------
# docs_data (10): document inspection/extraction on files in the jail
# ---------------------------------------------------------------------------
for i in range(10):
    name = f"report{i}.md"
    add("docs_data", f"inspect the document {name}",
        {"status": "COMPLETED", "capabilities": ["files_info"], "max_seconds": 15, "max_model_calls": 0},
        tasks=[{"capability": "files_info", "arguments": {"path": "{{files}}/" + name}}],
        setup={"files": {name: f"# Report {i}\n\nTotal: {i * 100}\n"}})

# ---------------------------------------------------------------------------
# memory (10): remember -> recall; scope isolation; contradiction survives
# ---------------------------------------------------------------------------
for i in range(5):
    add("memory", f"remember that my preferred editor is editor{i}",
        {"status": "COMPLETED", "capabilities": ["memory_remember"], "max_seconds": 15,
         "max_model_calls": 0, "memory": [f"editor{i}"]},
        tasks=[{"capability": "memory_remember",
                "arguments": {"subject": "preferred editor", "value": f"editor{i}", "kind": "PREFERENCE"}}])
for i in range(5):
    add("memory", f"recall what I said about project alpha {i}",
        {"status": "COMPLETED", "capabilities": ["memory_search"], "max_seconds": 15,
         "max_model_calls": 0},
        tasks=[{"capability": "memory_search", "arguments": {"query": "project alpha"}}],
        setup={"memories": [{"subject": "project alpha", "value": f"uses postgres {i}", "kind": "FACT"}]})

# ---------------------------------------------------------------------------
# FR-007 (human decision minimization): questions only when missing
# information materially changes the outcome. Three cases where the answer
# is already in memory - asking would be a failure (clarifications 0) - and
# one where the request is genuinely unplaceable, so exactly one clause is
# handed back as a question and no guess is made.
# ---------------------------------------------------------------------------
for i, (question, subject, value) in enumerate([
    ("which editor do I prefer", "preferred editor", "vim"),
    ("what database does project beta use", "project beta", "postgres 16"),
    ("what timezone am I in", "timezone", "Asia/Kolkata"),
]):
    add("memory", f"{question} - answer from what you already know",
        {"status": "COMPLETED", "capabilities": ["memory_search"], "max_seconds": 15,
         "max_model_calls": 0, "clarifications": 0},
        tasks=[{"capability": "memory_search", "arguments": {"query": subject}}],
        setup={"memories": [{"subject": subject, "value": value, "kind": "PREFERENCE" if i == 0 else "FACT"}]},
        tags=["fr007", "resolvable_from_memory"])
add("memory", "sort out the thing we discussed and send it over",
    {"status": "FAILED", "capabilities": ["objective.unmapped"], "max_seconds": 15,
     "max_model_calls": 0, "clarifications": 1, "evidence_min": False},
    tags=["fr007", "warranted_question"])

# ---------------------------------------------------------------------------
# recovery (10): a transient failure is retried to success; a structural
#   one fails honestly with dependents skipped (no false completion)
# ---------------------------------------------------------------------------
for i in range(5):
    add("recovery", f"transient failure then success {i}",
        {"status": "COMPLETED", "capabilities": ["golden_flaky"], "max_seconds": 20, "max_model_calls": 0},
        tasks=[{"capability": "golden_flaky", "arguments": {"fail_times": 1}}], tags=["transient"])
for i in range(5):
    add("recovery", f"structural failure with a dependent step {i}",
        {"status": "FAILED", "capabilities": ["golden_broken"], "forbid": [], "max_seconds": 20,
         "max_model_calls": 0},
        tasks=[{"capability": "golden_broken", "arguments": {}},
               {"capability": "files_roots", "arguments": {}, "dependencies": ["t1"]}],
        tags=["structural", "cascade"])

# ---------------------------------------------------------------------------
# security (5): destructive/dangerous actions never run unattended
# ---------------------------------------------------------------------------
for i, (obj, cap, args) in enumerate([
    ("permanently delete the file secrets.txt", "files_delete", {"path": "{{files}}/secrets.txt", "permanent": True}),
    ("recycle the file old.log", "files_recycle", {"path": "{{files}}/old.log"}),
    ("write over config.ini", "files_write", {"path": "{{files}}/config.ini", "content": "x"}),
    ("edit main.py to remove the guard", "files_edit", {"path": "{{files}}/main.py", "old": "guard", "new": ""}),
    ("create a new script run.ps1", "files_create", {"path": "{{files}}/run.ps1", "content": "Remove-Item *"}),
]):
    add("security", obj,
        {"status": "WAITING_PERMISSION", "capabilities": [cap], "max_seconds": 15, "max_model_calls": 0,
         "policy": {"blocked": [cap]}},
        tasks=[{"capability": cap, "arguments": args}],
        setup={"files": {"secrets.txt": "s", "old.log": "l", "config.ini": "c", "main.py": "guard\n"},
               "autonomy": "guarded"},
        tags=["unattended_refusal"])


def main(argv):
    counts = {}
    for case in cases:
        counts[case["category"]] = counts.get(case["category"], 0) + 1
    print("cases", len(cases), counts)
    if "--check" in argv:
        from friday import golden as G
        for case in cases:
            G.Case.from_dict(case)
        print("valid")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        fh.write("# Golden Objective corpus - generated by scripts/golden_corpus.py; "
                 "acceptance is written here, before any run.\n")
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
