"""
EVO-002: the Evolution Archive keeps every experiment, failures included, and
finds a rejected one before it is run again; it is never part of the person's
memory.

Real archive on a tmp directory. The memory proof drives FRIDAY's own recall
surface (the memory toolset's recall and search, and the memory stack's
aggregate) over a real Store.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading

import pytest

from friday import contracts as c
from friday import evolution_archive as A
from friday import memory_stack
from friday.store import FACT, Store
from friday.toolsets import memory as M


def _exp(**over):
    base = dict(
        objective="Reduce tool-routing latency",
        parent_version="router-v4",
        candidate_version="router-v5",
        hypothesis="New retrieval strategy improves routing",
        scope=["tool_router"],
        baseline={"task_success": 0.82, "latency_ms": 90},
        candidate={"task_success": 0.80, "latency_ms": 61},
        evaluation={"frozen_benchmark": True, "negative_controls": "passed",
                    "held_out_tests": "failed", "security_review": "passed"},
        decision=A.REJECT,
        rollback_version="router-v4",
    )
    base.update(over)
    return base


@pytest.fixture
def archive(tmp_path):
    return A.EvolutionArchive(tmp_path / "evolution_archive")


# -- the record ---------------------------------------------------------------


def test_a_record_keeps_every_field_the_owner_named(archive):
    rec = archive.record(**_exp())
    got = archive.get(rec["id"])
    for key in ("objective", "parent_version", "candidate_version", "hypothesis",
                "scope", "baseline", "candidate", "evaluation", "decision",
                "rollback_version", "fingerprint", "recorded_at"):
        assert key in got, key
    assert got["baseline"] == {"task_success": 0.82, "latency_ms": 90}
    assert got["candidate"]["latency_ms"] == 61
    assert got["decision"] == A.REJECT and got["rollback_version"] == "router-v4"


def test_failed_candidates_are_kept_beside_promoted_ones(archive):
    archive.record(**_exp())
    archive.record(**_exp(hypothesis="Cache the manifest", decision=A.PROMOTE,
                          candidate_version="router-v6"))
    decisions = sorted(r["decision"] for r in archive.query())
    assert decisions == [A.PROMOTE, A.REJECT]


def test_the_archive_survives_a_restart(tmp_path):
    root = tmp_path / "arch"
    first = A.EvolutionArchive(root).record(**_exp())
    again = A.EvolutionArchive(root)
    assert again.get(first["id"])["hypothesis"] == _exp()["hypothesis"]
    assert again.find_rejected(parent_version="router-v4",
                               hypothesis=_exp()["hypothesis"], scope=["tool_router"])


@pytest.mark.parametrize("field,value", [
    ("hypothesis", ""), ("parent_version", "  "), ("decision", "MAYBE"),
    ("rollback_version", ""), ("scope", []), ("baseline", {}), ("candidate", "fast"),
    ("baseline", {"latency_ms": float("nan")}),
])
def test_an_incomplete_or_malformed_record_is_refused(archive, field, value):
    with pytest.raises(A.ArchiveError):
        archive.record(**_exp(**{field: value}))
    assert archive.query() == []


def test_records_from_concurrent_writers_are_all_kept(archive):
    def write(i):
        archive.record(**_exp(hypothesis=f"idea {i}"))

    threads = [threading.Thread(target=write, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [r["id"] for r in archive.query()]
    assert len(ids) == 12 and len(set(ids)) == 12


def test_a_torn_archive_line_fails_closed_not_silent(tmp_path):
    arch = A.EvolutionArchive(tmp_path / "arch")
    arch.record(**_exp())
    with open(arch.path, "ab") as f:
        f.write(b'{"id": "x", "decis')
    with pytest.raises(A.ArchiveError, match="line 2"):
        arch.query()
    with pytest.raises(A.ArchiveError):
        arch.find_rejected(parent_version="router-v4", hypothesis="anything",
                           scope=["tool_router"])


# -- a rejected candidate is found before the same experiment runs again -----


def test_a_rejected_experiment_is_found_before_it_runs_again(archive):
    rec = archive.record(**_exp())
    hit = archive.find_rejected(parent_version="router-v4",
                                hypothesis="New retrieval strategy improves routing",
                                scope=["tool_router"])
    assert hit and hit["id"] == rec["id"]
    with pytest.raises(A.AlreadyRejected) as caught:
        archive.require_untried(parent_version="router-v4",
                                hypothesis="New retrieval strategy improves routing",
                                scope=["tool_router"])
    assert caught.value.record["id"] == rec["id"]


def test_the_fingerprint_ignores_wording_noise_and_scope_order(archive):
    archive.record(**_exp(scope=["tool_router", "context_compiler"]))
    hit = archive.find_rejected(
        parent_version="router-v4",
        hypothesis="  new RETRIEVAL   strategy improves routing ",
        scope=["context_compiler", "tool_router", "tool_router"])
    assert hit


def test_a_different_parent_hypothesis_or_scope_is_a_different_experiment(archive):
    archive.record(**_exp())
    same = dict(parent_version="router-v4",
                hypothesis="New retrieval strategy improves routing", scope=["tool_router"])
    assert archive.find_rejected(**same)
    for change in ({"parent_version": "router-v5"},
                   {"hypothesis": "Cache the tool manifest"},
                   {"scope": ["model_gateway"]}):
        assert archive.find_rejected(**{**same, **change}) is None, change
    assert archive.require_untried(**{**same, "parent_version": "router-v5"}) is None


def test_the_candidate_version_label_does_not_dodge_the_check(archive):
    archive.record(**_exp(candidate_version="router-v5"))
    assert archive.find_rejected(parent_version="router-v4",
                                 hypothesis=_exp()["hypothesis"], scope=["tool_router"])


def test_a_promoted_experiment_does_not_block_a_rerun(archive):
    archive.record(**_exp(decision=A.PROMOTE))
    assert archive.find_rejected(parent_version="router-v4",
                                 hypothesis=_exp()["hypothesis"],
                                 scope=["tool_router"]) is None


def test_query_finds_by_text_and_decision(archive):
    archive.record(**_exp())
    archive.record(**_exp(hypothesis="Cache the manifest", decision=A.PROMOTE))
    assert [r["hypothesis"] for r in archive.query("manifest")] == ["Cache the manifest"]
    assert len(archive.query(decision=A.REJECT)) == 1
    assert archive.query("no such thing") == []


# -- archive records never appear in recall of the person's memory ------------


@pytest.fixture
def memory(tmp_path):
    # The memory stack's preferences and relations tiers read the database named
    # by ADA_DB (ui_server._db_path), so the person's memory lives exactly there;
    # a Store anywhere else would leave those recall paths looking at nothing.
    s = Store(os.environ["ADA_DB"])
    M.reset_store(s)
    yield s
    M.reset_store(None)


def _recall_paths(token):
    """Every recall path the person's memory offers, asked for `token`; one
    string per path so a path that cannot see the memory is caught by name."""
    run = c.Run.create("recall", capability="memory")
    paths = {}
    for name, fn in (("memory_recall", M.memory_recall), ("memory_search", M.memory_search)):
        out = fn(run, token)
        # a failed recall ("nothing recorded") is an empty answer, not a crash;
        # the negative control below proves each leg can answer with a hit
        paths[name] = json.dumps(out.output["memories"]) if out.status == c.SUCCEEDED else ""
    paths["search_memories"] = json.dumps(M.store().search_memories(token))
    paths["recall"] = json.dumps(M.store().recall(token))
    paths["recall_scoped"] = json.dumps(M.store().recall_scoped(needle=token))
    # aggregate echoes the query back as "task"; that is the question, not recall
    context = {k: v for k, v in memory_stack.aggregate(token).items() if k != "task"}
    paths["aggregate"] = json.dumps(context, default=str)
    return paths


def _recall_everything(token):
    return " | ".join(_recall_paths(token).values())


def test_archive_records_never_appear_in_recall_of_the_persons_memory(tmp_path, memory):
    token = "zyxwv-routing-experiment"
    memory.remember("editor", "vscode", kind=FACT, source="user")
    arch = A.EvolutionArchive(tmp_path / "evolution_archive")
    arch.record(**_exp(hypothesis=f"{token} improves routing"))
    # recall itself creates its own lazy tables (failure_genome); warm it up first
    # so the snapshot isolates what the ARCHIVE adds to the memory database
    _recall_everything(token)
    tables_before = {r[0] for r in memory._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    arch.record(**_exp(hypothesis=f"{token} again", objective=token, decision=A.PROMOTE))
    arch.find_rejected(parent_version="router-v4", hypothesis=token, scope=["tool_router"])

    # the archive is found where it should be ...
    assert len(arch.query(token)) == 2
    # ... and nowhere in the person's memory, by any recall path
    assert token not in _recall_everything(token)
    assert memory.search_memories(token) == []
    assert memory.recall_scoped(needle=token) == []
    assert memory._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    # the archive added nothing to the memory database and lives in its own file
    tables_after = {r[0] for r in memory._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables_after == tables_before
    assert arch.path != memory.path


def test_the_recall_probe_does_see_a_record_that_was_written_to_memory(tmp_path, memory):
    """Negative control: the same probe finds the token when it IS in memory,
    so its silence about the archive means something."""
    token = "qwertz-leak-control"
    # scope "preferences" is what the aggregate stack's preferences tier reads
    memory.remember("engineering.note", f"{token} improves routing", kind=FACT,
                    source="user", scope="preferences")
    paths = _recall_paths(token)
    # each leg is checked alone: one blind path must fail here, not hide behind
    # the others (the store path and the ADA_DB path must be the same database)
    for name in ("memory_recall", "memory_search", "search_memories", "aggregate"):
        assert token in paths[name], f"{name} cannot see the person's memory"


def test_the_archive_module_does_not_reach_the_memory_stack():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(A))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    forbidden = {"friday.store", "friday.memory_stack", "friday.brain",
                 "friday.toolsets.memory"}
    assert not (imported & forbidden), imported & forbidden


def test_the_aggregate_probe_reads_the_same_database_as_the_fixture_store(memory):
    from friday import ui_server

    assert os.path.samefile(ui_server._db_path(), memory.path)


# -- two processes share one archive without tearing a line ------------------

_CHILD = textwrap.dedent("""
    import sys
    from friday import evolution_archive as A
    root, lock_timeout, tag, count = sys.argv[1], float(sys.argv[2]), sys.argv[3], int(sys.argv[4])
    arch = A.EvolutionArchive(root, lock_timeout=lock_timeout)
    try:
        for i in range(count):
            arch.record(objective="o", parent_version="p", candidate_version=f"c{i}",
                        hypothesis=f"{tag} idea {i} " + "x" * 400, scope=["s"],
                        baseline={"m": 1}, candidate={"m": 2}, evaluation={"e": "ok"},
                        decision=A.REJECT, rollback_version="p")
    except A.ArchiveError as exc:
        print("BUSY", exc)
        sys.exit(3)
""")


def _child(root, lock_timeout, tag, count):
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(root), str(lock_timeout), tag, str(count)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=os.getcwd(), env={**os.environ, "PYTHONPATH": os.getcwd()})


def test_a_lock_held_by_another_process_blocks_a_writer_until_it_times_out(archive):
    archive.record(**_exp())
    with archive._locked():                       # this process holds the archive
        child = _child(archive.root, 0.4, "blocked", 1)
        out, _ = child.communicate(timeout=60)
    assert child.returncode == 3 and "BUSY" in out, out
    assert len(archive.query()) == 1              # the blocked writer wrote nothing
    # once released, another process can write
    ok = _child(archive.root, 10, "after", 1)
    out, _ = ok.communicate(timeout=60)
    assert ok.returncode == 0, out
    assert len(archive.query()) == 2


def test_records_from_concurrent_processes_are_all_kept_and_none_torn(archive):
    archive.record(**_exp())
    kids = [_child(archive.root, 60, f"proc{n}", 15) for n in range(4)]
    for k in kids:
        out, _ = k.communicate(timeout=180)
        assert k.returncode == 0, out
    rows = archive.query()                        # raises ArchiveError on a torn line
    assert len(rows) == 1 + 4 * 15
    assert len({r["id"] for r in rows}) == len(rows)
