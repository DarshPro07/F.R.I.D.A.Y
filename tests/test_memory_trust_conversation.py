"""
The conversation's own memory writes carry the lineage of the turn (MEM-002,
V7-D28). The voice agent decides it from what the turn consumed; a turn that
read a page or a tool result writes read material, which is remembered but
never in the person's voice.
"""
from __future__ import annotations

import pytest

from friday import contracts as c
from friday import policy as p
from friday.store import FACT, PREFERENCE, Store
from friday.toolsets import memory as M


@pytest.fixture
def db(tmp_path):
    store = Store(tmp_path / "m.sqlite3")
    M.reset_store(store)
    yield store
    M.reset_store(None)
    store.close()


@pytest.fixture
def engine():
    return p.PolicyEngine(autonomy=p.GUARDED)


def _read_run():
    return c.Run.create("remember", capability="memory", provenance=c.READ_MATERIAL)


def _person_run():
    return c.Run.create("remember", capability="memory")


def test_a_turn_that_read_something_writes_read_material(db, engine):
    result = M.memory_remember(_read_run(), "arc.language", "Rust", kind=FACT,
                               engine=engine)
    assert result.status == "succeeded"
    assert db.recall("arc.language")[0]["trust_class"] == c.READ_MATERIAL


def test_a_turn_that_read_nothing_writes_the_persons_row_unclassified(db, engine):
    M.memory_remember(_person_run(), "arc.language", "Rust", kind=FACT, engine=engine)
    assert db.recall("arc.language")[0]["trust_class"] == ""


def test_a_turn_that_read_something_cannot_set_a_preference(db, engine):
    result = M.memory_remember(_read_run(), "editor", "vim", kind=PREFERENCE,
                               engine=engine)
    assert result.status == "failed"
    assert db.recall("editor") == []


def test_a_turn_that_read_something_cannot_overrule_the_person(db, engine):
    M.memory_remember(_person_run(), "arc.language", "Python", kind=FACT, engine=engine)
    result = M.memory_remember(_read_run(), "arc.language", "Rust", kind=FACT,
                               engine=engine)
    assert result.status == "failed"
    assert [r["value"] for r in db.recall("arc.language")] == ["Python"]


def test_a_turn_that_read_something_cannot_record_a_decision(db, engine):
    result = M.project_record_decision(_read_run(), "arc", "use Rust", engine=engine)
    assert result.status == "failed"
    assert db.decisions("arc") == []


def test_the_tool_layer_maps_the_agents_flag_onto_the_run(db):
    from friday.tools import memory_control as MC

    seen = []

    def spy(run, *args, **kwargs):
        seen.append(run.provenance)
        return c.partial(c.started(run.run_id, "memory.remember"), "spy")

    MC._execute("remember x", spy, from_read_material=True)
    MC._execute("remember y", spy)
    assert seen == [c.READ_MATERIAL, c.PERSON]


def test_the_ledger_refuses_a_turn_that_read_something():
    from friday import brain as B
    from friday.tools import brain_control as BC

    class Mcp:
        tools = {}

        def tool(self):
            return lambda fn: self.tools.setdefault(fn.__name__, fn)

    mcp = Mcp()
    BC.register(mcp)

    class Spy:
        def remember(self, fact, *, provenance, origin, **kw):
            assert origin == B.READ_ORIGIN
            raise B.AdmissionRefused("read material")

    BC.configure(Spy())
    try:
        out = mcp.tools["brain_remember"]("a fact", "file.py:1", from_read_material=True)
    finally:
        BC.configure(None)
    assert out["status"] == "refused"


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        return lambda fn: self.tools.setdefault(fn.__name__, fn)


def test_a_direct_client_that_omits_the_flag_is_read_material(db):
    from friday.tools import memory_control as MC

    mcp = _Mcp()
    MC.register(mcp)
    remember = mcp.tools["memory_remember"]

    refused = remember("editor", "vim", kind=PREFERENCE)
    assert refused["status"] == "failed"
    assert db.recall("editor") == []

    remember("arc.language", "Rust", kind=FACT)
    assert db.recall("arc.language")[0]["trust_class"] == c.READ_MATERIAL

    decision = mcp.tools["memory_record_decision"]("arc", "use Rust")
    assert decision["status"] == "failed"
    assert db.decisions("arc") == []


def test_an_explicit_false_from_the_agent_stays_the_persons_voice(db):
    from friday.tools import memory_control as MC

    mcp = _Mcp()
    MC.register(mcp)
    mcp.tools["memory_remember"]("arc.language", "Rust", kind=FACT,
                                 from_read_material=False)
    assert db.recall("arc.language")[0]["trust_class"] == ""


def test_the_ledger_refuses_a_direct_client_that_omits_the_flag():
    from friday import brain as B
    from friday.tools import brain_control as BC

    mcp = _Mcp()
    BC.register(mcp)

    class Spy:
        origins = []

        def remember(self, fact, *, provenance, origin, **kw):
            self.origins.append(origin)
            return {"status": "ok", "id": 1}

    spy = Spy()
    BC.configure(spy)
    try:
        mcp.tools["brain_remember"]("a fact", "file.py:1")
        mcp.tools["brain_remember"]("a fact", "file.py:1", from_read_material=False)
    finally:
        BC.configure(None)
    assert spy.origins == [B.READ_ORIGIN, B.PERSON_ORIGIN]


def test_the_agent_overwrites_the_models_lineage_claim():
    import agent_friday as A

    assert {"memory_remember", "memory_record_decision",
            "brain_remember"} <= A.MEMORY_WRITES
