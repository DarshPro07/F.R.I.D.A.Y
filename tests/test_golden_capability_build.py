"""
CAPB-001 golden journey: a capability the plan needs does not exist, so it is
searched for, built through forge, enabled by the person, and the ORIGINAL
task then runs and the objective completes.

The skill's source comes from a deterministic stub author (who writes it in
production is an open decision); everything after the author is the real
path: forge's static gate, sandboxed verification in a subprocess, the
registry lifecycle, and the executor's own task states.
"""
from __future__ import annotations

import pytest

from friday import capability_builder as CB
from friday import forge as F
from friday.continuous import ContinuousTaskExecutor
from friday.contracts import PERSON
from friday.objectives import FailureKind, TaskStatus, compile_objective
from friday.store import Store

SPEC = {
    "name": "word_count",
    "goal": "count the words in a text",
    "inputs": {"text": "string"},
    "outputs": {"words": "integer"},
    "verification": [{"inputs": {"text": "one two three"},
                      "expect": {"words": 3}}],
}
GOOD = "def run(ctx, text):\n    return {'words': len(text.split())}\n"
FORBIDDEN = "import os\ndef run(ctx, text):\n    return {'words': 3}\n"
WRONG = "def run(ctx, text):\n    return {'words': 0}\n"


class Port:
    """The capability port: it knows `cap_known` and nothing else."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, capability: str, arguments: dict) -> dict:
        self.calls.append(capability)
        if capability == "cap_known":
            return {"ok": True}
        raise LookupError(f"no capability {capability!r}")


class Stub:
    """Author and approver in one place, so a test can see what they were asked."""

    def __init__(self, source: str = GOOD, *, approve: bool = True,
                 actor: str = "owner") -> None:
        self.source = source
        self.approve = approve
        self.actor = actor
        self.authored: list[CB.BuildRequest] = []
        self.asked: list[dict] = []

    def author(self, request: CB.BuildRequest):
        self.authored.append(request)
        return CB.Authored(F.CapabilitySpec.from_dict(SPEC), self.source,
                           "test:stub-author")

    def approver(self, summary: dict):
        self.asked.append(summary)
        if not self.approve:
            return None
        return CB.Approval(self.actor, (summary["scope"],))


@pytest.fixture(autouse=True)
def clean_scanner(monkeypatch):
    """The SkillSpector gate has its own tests (and is not installed
    everywhere); these exercise the build journey around it."""
    monkeypatch.setattr(
        F, "run_skillspector",
        lambda target, **_: F.ScanReport(status=F.SCAN_CLEAN,
                                         scanned_at="2026-10-09T00:00:00Z"))


@pytest.fixture
def store(tmp_path, monkeypatch) -> Store:
    monkeypatch.setattr(F, "DATA_DIR", tmp_path)
    forge_store = Store(tmp_path / "forge.db")
    F.reset_store(forge_store)
    yield Store(":memory:")
    F.reset_store(None)
    forge_store.close()


@pytest.fixture
def registry(tmp_path) -> F.Registry:
    return F.Registry(tmp_path / "skills")


def make_builder(registry, stub: Stub, **kwargs) -> CB.CapabilityBuilder:
    kwargs.setdefault("search", lambda name: [])
    return CB.CapabilityBuilder(author=stub.author, approve=stub.approver,
                                registry=registry, **kwargs)


def compile_run(store, tasks: list[dict], *, known=("cap_known",)) -> str:
    return compile_objective(
        store, request="count the words in the note", tasks=tasks,
        manifest=[{"id": name, "description": name} for name in known],
        objective_summary="count words", provenance=PERSON,
        attended=True)["run_id"]


async def drive(store, run_id: str, builder, port: Port | None = None) -> Port:
    port = port or Port()
    executor = ContinuousTaskExecutor(store, port.call, executor_id="capb",
                                      capability_builder=builder)
    try:
        await executor.start(run_id)
    finally:
        executor.stop()
    return port


def tasks_by_capability(store, run_id: str) -> dict:
    return {t["capability"]: t for t in store.objective_tasks(run_id)}


def build_events(store, run_id: str) -> list[dict]:
    return [e for e in store.objective_events(run_id) if e["event"] == CB.EVENT_BUILD]


PLAN = [{"capability": "word_count", "arguments": {"text": "alpha beta gamma delta"}},
        {"capability": "cap_known", "arguments": {}, "dependencies": ["t1"]}]


# -- the journey -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_missing_capability_is_built_and_the_original_task_completes(
        store, registry):
    stub = Stub()
    run_id = compile_run(store, PLAN)
    # The compiler wrote the unknown step off before anything ran.
    assert tasks_by_capability(store, run_id)["word_count"]["status"] == TaskStatus.FAILED

    port = await drive(store, run_id, make_builder(registry, stub))

    assert store.objective_run(run_id)["status"] == "COMPLETED"
    tasks = tasks_by_capability(store, run_id)
    original = tasks["word_count"]
    assert original["status"] == TaskStatus.SUCCEEDED
    assert original["failure_kind"] is None
    assert original["result"]["output"] == {"words": 4}      # the task's own arguments
    assert original["result"]["may_claim_completion"] is True  # recorded on the task's Run
    assert original["result"]["verification"]["method"] == "forge_skill"
    assert original["attempts"] == 1                          # kept, not reset
    assert tasks["cap_known"]["status"] == TaskStatus.SUCCEEDED   # un-skipped
    assert "word_count" not in port.calls                     # never went to the port

    skill = registry.get("word_count")
    assert skill["state"] == F.ENABLED
    assert skill["scopes"] == [CB.scope_for(run_id)]
    actors = {h["to_state"]: h["actor"] for h in registry.history("word_count")}
    assert actors[F.VERIFIED] == CB.BUILDER_ACTOR
    assert actors[F.ENABLED] == "owner"                       # a person, never forge

    (event,) = build_events(store, run_id)
    assert event["detail"]["built"] is True
    assert event["detail"]["approval"] == {"actor": "owner",
                                           "scopes": [CB.scope_for(run_id)]}
    assert stub.asked[0]["name"] == "word_count"


@pytest.mark.asyncio
async def test_a_capability_that_is_missing_only_at_call_time_is_built_too(
        store, registry):
    run_id = compile_run(store, PLAN, known=("cap_known", "word_count"))
    assert tasks_by_capability(store, run_id)["word_count"]["status"] == TaskStatus.READY

    port = await drive(store, run_id, make_builder(registry, Stub()))

    assert store.objective_run(run_id)["status"] == "COMPLETED"
    assert tasks_by_capability(store, run_id)["word_count"]["status"] == TaskStatus.SUCCEEDED
    assert port.calls.count("word_count") == 1                # the one that failed
    assert registry.get("word_count")["state"] == F.ENABLED


@pytest.mark.asyncio
async def test_the_search_is_recorded_and_handed_to_the_author(store, registry):
    stub = Stub()
    found = [{"provider": "text_tools", "match": "related", "shared_terms": ["word"]}]
    run_id = compile_run(store, PLAN)
    await drive(store, run_id, make_builder(registry, stub, search=lambda name: found))
    assert stub.authored[0].considered == tuple(found)
    assert build_events(store, run_id)[0]["detail"]["considered"] == found


@pytest.mark.asyncio
async def test_an_exact_alternative_in_the_fabric_is_not_rebuilt(store, registry):
    stub = Stub()
    exact = [{"provider": "text_tools", "match": "exact", "operations": ["word_count"]}]
    run_id = compile_run(store, PLAN)
    await drive(store, run_id, make_builder(registry, stub, search=lambda name: exact))
    assert stub.authored == []
    task = tasks_by_capability(store, run_id)["word_count"]
    assert task["status"] == TaskStatus.FAILED
    assert "text_tools already offers word_count" in task["evidence"]
    assert registry.get("word_count") is None


# -- refusals: nothing is enabled, and the run says so -------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("source, why", [(FORBIDDEN, "os"), (WRONG, "")])
async def test_a_skill_forge_refuses_is_never_enabled(store, registry, source, why):
    run_id = compile_run(store, [PLAN[0].copy(),
                                 {"capability": "cap_known", "arguments": {}}])
    port = await drive(store, run_id, make_builder(registry, Stub(source)))

    assert registry.get("word_count")["state"] == F.REJECTED
    assert store.objective_run(run_id)["status"] == "PARTIAL"
    task = tasks_by_capability(store, run_id)["word_count"]
    assert task["status"] == TaskStatus.FAILED
    assert task["failure_kind"] == FailureKind.CAPABILITY_MISSING
    assert "refused by forge" in task["evidence"] and why in task["evidence"]
    assert "word_count" not in port.calls
    assert len(build_events(store, run_id)) == 1              # bounded: one attempt


@pytest.mark.asyncio
async def test_without_the_persons_approval_the_skill_stays_registered(store, registry):
    run_id = compile_run(store, PLAN)
    await drive(store, run_id, make_builder(registry, Stub(approve=False)))
    assert registry.get("word_count")["state"] == F.REGISTERED
    assert store.objective_run(run_id)["status"] == "FAILED"
    task = tasks_by_capability(store, run_id)["word_count"]
    assert "waiting for the person's approval" in task["evidence"]


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["forge", CB.BUILDER_ACTOR, ""])
async def test_the_party_that_built_a_skill_cannot_enable_it(store, registry, actor):
    run_id = compile_run(store, PLAN)
    await drive(store, run_id, make_builder(registry, Stub(actor=actor)))
    assert registry.get("word_count")["state"] == F.REGISTERED
    assert store.objective_run(run_id)["status"] == "FAILED"
    assert tasks_by_capability(store, run_id)["word_count"]["status"] == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_an_approval_for_another_scope_enables_nothing(store, registry):
    stub = Stub()
    stub.approver = lambda summary: CB.Approval("owner", ("objective:someone-else",))
    run_id = compile_run(store, PLAN)
    await drive(store, run_id, make_builder(registry, stub))
    assert registry.get("word_count")["state"] == F.REGISTERED
    assert store.objective_run(run_id)["status"] == "FAILED"


@pytest.mark.asyncio
async def test_an_author_that_raises_fails_the_task_not_the_engine(store, registry):
    def broken(request):
        raise RuntimeError("model unavailable")

    builder = CB.CapabilityBuilder(author=broken, approve=Stub().approver,
                                   registry=registry, search=lambda name: [])
    run_id = compile_run(store, PLAN)
    await drive(store, run_id, builder)
    task = tasks_by_capability(store, run_id)["word_count"]
    assert task["status"] == TaskStatus.FAILED
    assert "the author failed: RuntimeError: model unavailable" in task["evidence"]
    assert registry.get("word_count") is None


@pytest.mark.asyncio
async def test_an_author_that_writes_the_wrong_skill_is_refused(store, registry):
    stub = Stub()
    other = F.CapabilitySpec.from_dict({**SPEC, "name": "char_count"})
    stub.author = lambda request: CB.Authored(other, GOOD, "test")
    run_id = compile_run(store, PLAN)
    await drive(store, run_id, make_builder(registry, stub))
    assert registry.get("char_count") is None
    assert registry.get("word_count") is None
    assert store.objective_run(run_id)["status"] == "FAILED"


@pytest.mark.asyncio
async def test_a_skill_enabled_for_one_objective_is_not_callable_from_another(
        store, registry):
    run_id = compile_run(store, PLAN)
    builder = make_builder(registry, Stub())
    await drive(store, run_id, builder)
    assert builder.owns("word_count", run_id)
    assert not builder.owns("word_count", "RUN-another")
    with pytest.raises(LookupError):
        await builder.call("word_count", {"text": "x"}, objective_id="RUN-another")


@pytest.mark.asyncio
async def test_a_skill_whose_file_changed_after_enabling_does_not_run(store, registry):
    run_id = compile_run(store, PLAN)
    builder = make_builder(registry, Stub())
    await drive(store, run_id, builder)
    path = registry.get("word_count")["source_path"]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(WRONG)
    with pytest.raises(RuntimeError, match="not what was verified"):
        await builder.call("word_count", {"text": "x"}, objective_id=run_id)


@pytest.mark.asyncio
async def test_an_enabled_skill_is_still_refused_when_the_envelope_blocks_it(
        store, registry):
    from dataclasses import replace

    from friday import contracts as c

    run_id = compile_run(store, PLAN)
    builder = make_builder(registry, Stub())
    await drive(store, run_id, builder)
    assert builder.owns("word_count", run_id)

    executor = ContinuousTaskExecutor(store, Port().call, executor_id="capb-block",
                                      capability_builder=builder)
    try:
        task = tasks_by_capability(store, run_id)["word_count"]
        run = executor._task_run(run_id, task)
        blocked = replace(run, envelope=replace(
            run.envelope, blocked_capabilities=("word_count",)))
        result = await executor._dispatch("word_count", {"text": "a b"}, blocked)
    finally:
        executor.stop()
    assert result["status"] == c.NOT_PERMITTED
    assert "blocked for this run" in result["error"]


@pytest.mark.asyncio
async def test_without_a_builder_a_missing_capability_stays_terminal(store):
    run_id = compile_run(store, PLAN)
    port = await drive(store, run_id, None)
    task = tasks_by_capability(store, run_id)["word_count"]
    assert task["status"] == TaskStatus.FAILED
    assert task["failure_kind"] == FailureKind.CAPABILITY_MISSING
    assert build_events(store, run_id) == []
    assert "word_count" not in port.calls


# -- the pieces ----------------------------------------------------------------


def test_the_fabric_search_names_a_provider_that_already_offers_it():
    from friday import fabric

    provider = next(p for p in fabric.registry().values() if p.operations)
    operation = provider.operations[0]
    rows = CB.search_fabric(operation)
    assert rows[0]["match"] == "exact"
    assert rows[0]["provider"] == provider.id or operation in rows[0]["operations"]
    assert all(row["match"] != "exact" for row in CB.search_fabric("zzqx_nothing_like_it"))


def test_forge_runs_a_skill_on_real_inputs_and_refuses_what_it_should(tmp_path):
    spec = F.CapabilitySpec.from_dict(SPEC)
    ok = F.invoke(GOOD, spec, {"text": "a b c d e"}, workdir=tmp_path / "ok")
    assert ok == {"ok": True, "result": {"words": 5}, "error": ""}
    assert not F.invoke(GOOD, spec, {}, workdir=tmp_path / "m")["ok"]          # missing input
    gated = F.invoke(FORBIDDEN, spec, {"text": "x"}, workdir=tmp_path / "g")
    assert not gated["ok"] and "static gate" in gated["error"]
    boom = F.invoke("def run(ctx, text):\n    raise ValueError('nope')\n", spec,
                    {"text": "x"}, workdir=tmp_path / "b")
    assert not boom["ok"] and "ValueError: nope" in boom["error"]


@pytest.mark.asyncio
async def test_two_calls_to_one_forged_skill_each_run_on_their_own_inputs(
        store, registry, monkeypatch):
    """Ready tasks run together; two calls of the same skill must not share
    the staged request, or one task reports the other's result."""
    import json
    import subprocess
    import threading
    from pathlib import Path

    real_run = subprocess.run
    both_staged = threading.Barrier(2, timeout=30)

    def run_after_both_staged(argv, **kwargs):
        request = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
        if request["spec"]["verification"][0]["expect"] == {}:   # a real call
            both_staged.wait()
        return real_run(argv, **kwargs)

    monkeypatch.setattr(F.subprocess, "run", run_after_both_staged)
    # Disjoint surfaces and room for two workers, so both start in one wave.
    run_id = compile_run(store, [
        {"capability": "word_count", "arguments": {"text": "one two"},
         "surface": ["note_a.txt"]},
        {"capability": "word_count", "arguments": {"text": "a b c d e"},
         "surface": ["note_b.txt"]}],
        known=("word_count",))       # missing at call time: compile keeps the surfaces

    class TwoWorkers:
        def worker_capacity(self, objective_id=""):
            return 2

        def active(self, kind=None):
            return 0

        def justify_parallel(self, objective_id):
            pass

    executor = ContinuousTaskExecutor(
        store, Port().call, executor_id="capb", governor=TwoWorkers,
        capability_builder=make_builder(registry, Stub()))
    try:
        await executor.start(run_id)
    finally:
        executor.stop()

    tasks = store.objective_tasks(run_id)
    assert store.objective_run(run_id)["status"] == "COMPLETED",         [(t["arguments"], t["status"], t.get("detail"), t.get("evidence")) for t in tasks]
    words = sorted((t["arguments"]["text"], t["result"]["output"]["words"])
                   for t in tasks)
    assert words == [("a b c d e", 5), ("one two", 2)]
    assert not list((F.DATA_DIR / "forge" / "run").iterdir())   # nothing left staged
