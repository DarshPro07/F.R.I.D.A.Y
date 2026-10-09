"""
CAP-FORGE-012: a repository's pattern can be adopted without installing it.

The acceptance has two halves and both are exercised against a real tree on
disk: an EXTRACT_PATTERN decision is recorded with the pattern described, and
the pattern is then built natively after the repository has been deleted.
"""

from __future__ import annotations

import json
import shutil
import textwrap

import pytest

from friday import forge
from friday import pattern_extraction as P
from friday.store import Store

REPO = "muellerberndt/mini-agi"

# What a setup script would do if anything ever ran it.
SENTINEL_NAME = "setup_was_executed.txt"

SETUP_PY = """
    from pathlib import Path
    from setuptools import setup

    Path(__file__).with_name({sentinel!r}).write_text("ran")
    setup(name="mini-agi", version="0.1")
"""

LICENSE_TEXT = """
    MIT License

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction.
"""

HISTORY_SOURCE = textwrap.dedent("""
    def run(ctx, items, limit):
        return {"kept": list(items)[-limit:] if limit > 0 else []}
""").strip() + "\n"


@pytest.fixture
def clone(tmp_path):
    root = tmp_path / "mini-agi"
    (root / "miniagi").mkdir(parents=True)
    (root / "LICENSE").write_text(textwrap.dedent(LICENSE_TEXT).strip() + "\n")
    (root / "README.md").write_text("# mini-agi\nA one-action loop.\n")
    (root / "setup.py").write_text(
        textwrap.dedent(SETUP_PY).format(sentinel=SENTINEL_NAME))
    (root / "miniagi" / "__init__.py").write_text("raise RuntimeError('imported')\n")
    return root


@pytest.fixture
def log(tmp_path):
    return tmp_path / "pattern_decisions.jsonl"


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        forge, "run_skillspector",
        lambda target, **_: forge.ScanReport(status=forge.SCAN_CLEAN,
                                             scanned_at="2026-10-09T00:00:00Z"))
    forge.reset_store(Store(tmp_path / "ada.sqlite3"))
    try:
        yield forge.Registry(tmp_path / "skills")
    finally:
        forge.reset_store(None)


def describe(clone, **overrides) -> P.PatternDecision:
    fields = dict(
        repository=REPO, pattern="bounded recent history",
        description=("Keep only the last N turns verbatim and drop the rest, "
                     "so the prompt stays a fixed size however long the run is."),
        elements=["fixed-size window of recent turns", "oldest turn dropped first"],
        rationale="Ten lines of FRIDAY code; the upstream runtime is a model-coupled loop.",
        not_adopted=["raw shell execution", "its memory store"],
        source_commit="0123456789abcdef0123456789abcdef01234567")
    fields.update(overrides)
    return P.from_repository(clone, **fields)


def history_spec(**overrides) -> forge.CapabilitySpec:
    raw = {
        "name": "bounded_history", "goal": "keep only the most recent items",
        "inputs": {"items": "list of strings", "limit": "integer"},
        "outputs": {"kept": "list of strings"},
        "verification": [{"inputs": {"items": ["a", "b", "c"], "limit": 2},
                          "expect": {"kept": ["b", "c"]}}],
    }
    raw.update(overrides)
    return forge.CapabilitySpec.from_dict(raw)


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

def test_extract_pattern_is_in_the_decision_set():
    assert P.EXTRACT_PATTERN == "EXTRACT_PATTERN"
    assert P.EXTRACT_PATTERN in P.DECISIONS


def test_a_decision_records_the_pattern_and_what_the_tree_says_about_itself(clone, log):
    decision = describe(clone)
    P.record(decision, path=log)

    [stored] = P.decisions(path=log)
    assert stored == decision
    assert stored.decision == "EXTRACT_PATTERN"
    assert stored.pattern == "bounded recent history"
    assert "last N turns" in stored.description
    assert stored.elements == ("fixed-size window of recent turns",
                               "oldest turn dropped first")
    assert stored.not_adopted == ("raw shell execution", "its memory store")
    assert stored.license == "MIT"
    assert stored.repo_class
    assert stored.install_runtime is False and stored.code_copied is False
    assert stored.decided_at


def test_describing_a_clone_never_runs_it(clone):
    describe(clone)
    assert not (clone / SENTINEL_NAME).exists()


def test_the_log_is_append_only_and_filterable_by_repository(clone, log):
    first = describe(clone)
    second = describe(clone, repository="someone/else", pattern="progress critic",
                      description="Ask a second prompt whether the last step moved the goal.")
    P.record(first, path=log)
    P.record(second, path=log)

    assert len(log.read_text().splitlines()) == 2
    assert [d.pattern for d in P.decisions(path=log)] == [
        "bounded recent history", "progress critic"]
    assert [d.pattern for d in P.decisions(path=log, repository="someone/else")] == [
        "progress critic"]


def test_a_corrupt_line_does_not_hide_the_good_ones(clone, log):
    P.record(describe(clone), path=log)
    with log.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
        handle.write(json.dumps({"repository": "x"}) + "\n")
    P.record(describe(clone, pattern="second pattern"), path=log)

    assert [d.pattern for d in P.decisions(path=log)] == [
        "bounded recent history", "second pattern"]


def test_no_decisions_before_any_are_recorded(log):
    assert P.decisions(path=log) == []


@pytest.mark.parametrize("overrides", [
    {"description": "   "},
    {"rationale": ""},
    {"elements": []},
    {"elements": "the whole pattern as one string"},
    {"pattern": ""},
    {"description": "x" * (P.MAX_TEXT + 1)},
    {"elements": ["part"] * (P.MAX_ITEMS + 1)},
])
def test_an_undescribed_pattern_is_refused(clone, overrides):
    with pytest.raises(P.PatternError):
        describe(clone, **overrides)


def test_installing_the_runtime_or_copying_source_is_not_this_decision():
    base = dict(repository=REPO, pattern="p", description="d", elements=("e",),
                rationale="r")
    with pytest.raises(P.PatternError, match="install_runtime"):
        P.PatternDecision(**base, install_runtime=True)
    with pytest.raises(P.PatternError, match="code_copied"):
        P.PatternDecision(**base, code_copied=True)
    with pytest.raises(P.PatternError, match="EXTRACT_PATTERN"):
        P.PatternDecision(**base, decision="WRAP")


# ---------------------------------------------------------------------------
# The native build
# ---------------------------------------------------------------------------

def test_the_pattern_is_built_natively_after_the_repository_is_gone(clone, log, registry):
    decision = describe(clone)
    P.record(decision, path=log)
    shutil.rmtree(clone)
    assert not clone.exists()

    record = P.implement_natively(decision, history_spec(), HISTORY_SOURCE,
                                  registry=registry, path=log)

    assert record["state"] == forge.VERIFIED, record["verification"]
    assert record["provenance"] == f"EXTRACT_PATTERN:{REPO}@{decision.source_commit}"
    assert record["scopes"] == []
    assert registry.history("bounded_history")[-1]["to_state"] == forge.VERIFIED


def test_a_native_build_that_fails_its_cases_is_rejected_not_kept(clone, log, registry):
    decision = describe(clone)
    P.record(decision, path=log)
    wrong = HISTORY_SOURCE.replace("[-limit:]", "[:limit]")

    record = P.implement_natively(decision, history_spec(), wrong,
                                  registry=registry, path=log)

    assert record["state"] == forge.REJECTED


def test_a_build_needs_its_decision_on_record_first(clone, log, registry):
    decision = describe(clone)

    with pytest.raises(P.PatternError, match="record it first"):
        P.implement_natively(decision, history_spec(), HISTORY_SOURCE,
                             registry=registry, path=log)
    assert registry.get("bounded_history") is None


@pytest.mark.parametrize("dependency", ["mini-agi", "mini_agi", "Mini.AGI"])
def test_a_native_build_may_not_depend_on_the_repository(clone, log, registry, dependency):
    decision = describe(clone)
    P.record(decision, path=log)

    with pytest.raises(P.PatternError, match="installing it by another name"):
        P.implement_natively(decision, history_spec(dependencies=(dependency,)),
                             HISTORY_SOURCE, registry=registry, path=log)
    assert registry.get("bounded_history") is None
