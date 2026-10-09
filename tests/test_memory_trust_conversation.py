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
from friday import profile as P
from friday.store import FACT, INFERENCE, PREFERENCE, Store
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


# --- the profile learner (MEM-002): the same lineage on the automatic path ---


def _extractor(monkeypatch, *candidates):
    """Stand in for the model extractor and record what it was shown."""
    shown = []

    def fake(user_text, *, assistant_text="", existing_subjects=None):
        shown.append(assistant_text)
        return list(candidates)

    monkeypatch.setattr(P, "extract_candidates", fake)
    return shown


def _claim(**kw) -> P.Candidate:
    # POSSESSIONS: read material may be kept there; the person's scopes refuse it.
    base = dict(dimension=P.POSSESSIONS, subject="user.editor", value="vim",
                kind=FACT, confidence=0.9, evidence="I use vim")
    base.update(kw)
    return P.Candidate(**base).validated()


def test_a_profile_turn_that_read_something_lands_as_read_material(db, monkeypatch):
    shown = _extractor(monkeypatch, _claim())
    outcomes = P.learn_from_turn(db, "I use vim", assistant_text="a page said so",
                                 from_read_material=True)
    assert [o.action for o in outcomes] == ["stored"]
    assert db.recall("user.editor")[0]["trust_class"] == c.READ_MATERIAL
    assert shown == [""]  # the extractor never sees the reply


def test_a_profile_turn_that_read_nothing_is_unchanged(db, monkeypatch):
    shown = _extractor(monkeypatch, _claim())
    P.learn_from_turn(db, "I use vim", assistant_text="Noted, boss.")
    assert db.recall("user.editor")[0]["trust_class"] == ""
    assert shown == ["Noted, boss."]


@pytest.mark.parametrize("trust", ["", c.PERSON])
def test_read_material_cannot_supersede_what_the_person_said(db, monkeypatch, trust):
    db.remember("user.editor", "emacs", kind=INFERENCE, source="guessed",
                confidence=0.6, trust_class=trust)
    _extractor(monkeypatch, _claim(kind=FACT))
    outcomes = P.learn_from_turn(db, "I use vim", from_read_material=True)
    assert [o.action for o in outcomes] == ["conflict"]
    assert outcomes[0].contradiction_id is not None
    rows = db.recall("user.editor")
    assert [(r["value"], r["trust_class"]) for r in rows] == [("emacs", trust)]


@pytest.mark.parametrize("trust", ["", c.PERSON])
def test_read_material_does_not_reinforce_what_the_person_said(db, monkeypatch, trust):
    db.remember("user.editor", "vim", kind=FACT, source="said", confidence=0.7,
                trust_class=trust)
    before = db.recall("user.editor")[0]
    _extractor(monkeypatch, _claim(value="vim"))
    outcomes = P.learn_from_turn(db, "I use vim", from_read_material=True)
    assert [o.action for o in outcomes] == ["rejected"]
    rows = db.recall("user.editor")
    assert [r["id"] for r in rows] == [before["id"]]
    assert rows[0]["confidence"] == before["confidence"]
    assert rows[0]["evidence_count"] == before["evidence_count"]


def test_read_material_may_replace_older_read_material(db, monkeypatch):
    db.remember("user.editor", "emacs", kind=INFERENCE, source="a page",
                confidence=0.6, trust_class=c.READ_MATERIAL)
    _extractor(monkeypatch, _claim(kind=FACT))
    outcomes = P.learn_from_turn(db, "I use vim", from_read_material=True)
    assert [o.action for o in outcomes] == ["stored"]
    rows = db.recall("user.editor")
    assert [(r["value"], r["trust_class"]) for r in rows] == [("vim", c.READ_MATERIAL)]


def test_the_person_still_replaces_read_material_in_a_clean_turn(db, monkeypatch):
    db.remember("user.editor", "emacs", kind=INFERENCE, source="a page",
                confidence=0.6, trust_class=c.READ_MATERIAL)
    _extractor(monkeypatch, _claim(kind=FACT))
    outcomes = P.learn_from_turn(db, "I use vim")
    assert [o.action for o in outcomes] == ["stored"]
    assert [r["value"] for r in db.recall("user.editor")] == ["vim"]


def test_the_tool_layer_carries_the_flag_to_the_learner(db, monkeypatch):
    from friday.toolsets import profile as PT

    seen = {}

    def spy(store, user_text, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(PT, "store", lambda: db)
    monkeypatch.setattr(P, "learn_from_turn", spy)
    PT.profile_learn_from_turn(_read_run(), "I use vim", "reply",
                               from_read_material=True,
                               engine=p.PolicyEngine(autonomy=p.GUARDED))
    assert seen["from_read_material"] is True
    assert seen["assistant_text"] == "reply"


@pytest.mark.parametrize("given, expected", [(True, True), (False, False), (None, True)])
def test_the_mcp_wrapper_fails_closed_when_the_flag_is_omitted(
        db, monkeypatch, given, expected):
    # Only the voice agent knows what the turn consumed, and it always says.
    # A direct MCP client, or the model calling this through use_capability,
    # that omits the flag must not store the person's voice (MEM-002).
    from friday.toolsets import profile as PT
    from friday.tools import profile_control as PC

    seen = {}

    def spy(store, user_text, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(PT, "store", lambda: db)
    monkeypatch.setattr(P, "learn_from_turn", spy)
    mcp = _Mcp()
    PC.register(mcp)
    arguments = {} if given is None else {"from_read_material": given}
    mcp.tools["profile_learn_from_turn"]("I use vim", "reply", **arguments)
    assert seen["from_read_material"] is expected


def test_a_profile_call_that_omits_the_flag_stores_read_material(db, monkeypatch):
    # End to end through the MCP wrapper: the row the model's own call leaves
    # behind is marked read, and cannot replace one the person wrote.
    from friday.tools import profile_control as PC
    from friday.toolsets import profile as PT

    db.remember("user.editor", "emacs", kind=FACT, source="said", confidence=0.9)
    _extractor(monkeypatch, _claim(), _claim(subject="user.shell", value="fish"))
    monkeypatch.setattr(PT, "store", lambda: db)
    mcp = _Mcp()
    PC.register(mcp)
    out = mcp.tools["profile_learn_from_turn"]("I use vim and fish", "")
    assert [row["value"] for row in db.recall("user.editor")] == ["emacs"]
    assert db.recall("user.shell")[0]["trust_class"] == c.READ_MATERIAL
    assert out["needs_user_decision"] is True


def test_the_learner_always_states_the_turns_lineage():
    import asyncio
    import json

    from friday import autolearn

    calls = []

    async def invoke(name, arguments):
        calls.append((name, arguments))
        return json.dumps({"type": "text", "text": json.dumps(
            {"status": "succeeded", "learned": [], "reinforced": [], "conflicts": []})})

    async def go():
        learner = autolearn.AutoLearner(invoke)
        learner.observe("I use vim every single day", "a page said so",
                        from_read_material=True)
        learner.observe("I also run linux on the laptop", "Noted.")
        for _ in range(2):
            await learner._learn(*learner._queue.get_nowait())

    asyncio.run(go())
    learn = [args for name, args in calls if name == "profile_learn_from_turn"]
    assert learn[0] == {"user_said": "I use vim every single day",
                        "you_replied": "a page said so", "from_read_material": True}
    # Explicit False, not an omission: the tool reads an omitted flag as read
    # material, so a clean turn has to claim to be the person's own.
    assert learn[1] == {"user_said": "I also run linux on the laptop",
                        "you_replied": "Noted.", "from_read_material": False}


def test_the_agent_hands_the_turns_lineage_to_the_learner(monkeypatch):
    from types import SimpleNamespace

    import agent_friday as A

    seen = []

    class FakeLearner:
        def observe(self, text, assistant_text="", from_read_material=False):
            seen.append(from_read_material)

    monkeypatch.setattr(A.autolearn, "last_assistant_text", lambda ctx: "")
    for read in (True, False):
        agent = SimpleNamespace(_continuity=None, _learner=FakeLearner(),
                                _read_this_turn=read, _prior_turn_read=False)
        A.FridayAgent.prepare_turn(agent, object(), "audit the workspace")
    assert seen == [True, False]


def test_a_reply_that_read_a_page_is_not_context_for_the_next_turn(monkeypatch):
    # Turn 1 reads a page and the reply restates it. Turn 2 reads nothing, so
    # its own lineage is clean - but the reply it hands the extractor as
    # context is turn 1's, and "yes" to it must not carry the page in as the
    # person's word (MEM-002).
    import asyncio

    import agent_friday as A

    seen = []

    class FakeLearner:
        def observe(self, text, assistant_text="", from_read_material=False):
            seen.append((text, assistant_text, from_read_material))

    reads = iter([True, False, False])

    async def research(*args, **kwargs):
        return next(reads)

    async def nothing(*args, **kwargs):
        return None

    restated = "That page says you prefer emacs."
    monkeypatch.setattr(A, "remember_the_project", lambda text: None)
    monkeypatch.setattr(A, "note_the_requirements", lambda *a, **k: None)
    monkeypatch.setattr(A, "research_first", research)
    monkeypatch.setattr(A, "check_what_he_asserted", nothing)
    monkeypatch.setattr(A.autolearn, "last_assistant_text", lambda ctx: restated)
    agent = A.FridayAgent.__new__(A.FridayAgent)
    agent._learner, agent._continuity = FakeLearner(), None
    agent.stop_re_reading = lambda: None
    agent._router = type("R", (), {"note_turn": lambda self: None})()

    for text in ("look up my editor", "yes, that is right", "thanks, noted"):
        asyncio.run(agent.read_before_answering(object(), text))

    assert seen == [
        ("look up my editor", restated, True),      # the turn that read
        ("yes, that is right", "", False),          # replying to a read turn
        ("thanks, noted", restated, False),         # a clean turn after a clean one
    ]
