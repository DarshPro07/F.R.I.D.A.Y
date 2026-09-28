"""
CAP-002 (PRD v7): combined capability risk is judged, not each piece alone.

Reading the person's private data is fine. Sending to a destination the run
chooses is fine. An objective that can do both can send what it reads, and
that is the exfiltration shape - so the whole capability set is judged when
the objective is compiled, the one door every objective comes through:

    came from read material   refused outright: a page that reads your files
                              and sends them somewhere is the attack itself,
                              and asking would be theatre
    nobody attending          refused: there is nobody to agree
    the person, attended      needs the person's yes - under DANGEROUS
                              autonomy their standing yes, audited

Every capability must have a policy category (seven had none, and ran
ungated: V7-D15), and every category a composition class, so nothing new can
slip past the judgement unclassified.
"""
from __future__ import annotations

import pytest

from friday import capabilities as C
from friday import confirmation as CF
from friday import contracts as c
from friday import objectives as O
from friday import policy as P
from friday import trust
from friday.store import Store

PRIVATE = "files_read"
SEND = "web_fetch"


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "composition.sqlite3")
    yield db
    db.close()


def _manifest(*caps):
    return [{"id": cap, "description": cap} for cap in caps]


def _compile(store, *caps, **lineage):
    return O.compile_objective(
        store, request="read my notes and look this up",
        tasks=[{"capability": cap, "arguments": {}} for cap in caps],
        manifest=_manifest(*caps), objective_summary="notes and lookup", **lineage)


# --- classification ---------------------------------------------------------

def test_every_capability_meets_the_policy():
    """V7-D15: a capability with no policy category never met the engine."""
    ungated = [cap.id for cap in C._ALL
               if not any(t in P.TOOL_CATEGORIES for t in cap.policy_tool_ids())]
    assert ungated == []


def _uncategorised(monkeypatch, capability_id: str) -> None:
    """Take every policy category away from one capability, the state V7-D15
    found seven in and the test above keeps the registry out of."""
    for tool_id in C.by_id(capability_id).policy_tool_ids():
        monkeypatch.delitem(P.TOOL_CATEGORIES, tool_id, raising=False)


def test_a_capability_no_category_covers_is_asked_not_run(monkeypatch):
    """Slice 2 review: the runtime asked the engine only about ids with a
    category, so one with none ran on no decision at all - even though the
    engine's own rule for an unknown tool is ASK. That rule now applies, under
    any autonomy."""
    from friday import capability_runtime as CR
    from friday.toolsets import system

    calls = []

    def ungated(run, **arguments):
        # system.py asks the engine itself; 10 of the 33 toolset modules do
        # not, so the runtime's own gate is the one that has to hold.
        calls.append(arguments)
        return c.succeeded(c.started(run.run_id, "get_current_time"), "ran")

    monkeypatch.setattr(system, "get_current_time", ungated)
    _uncategorised(monkeypatch, "get_current_time")
    result = CR.CapabilityRuntime(engine=P.PolicyEngine(autonomy=P.DANGEROUS)).execute(
        "get_current_time", {})
    assert calls == [], "a capability no policy category covers ran on no decision"
    assert result.status == c.CANCELLED, result
    assert "no declared policy category" in result.error


def test_a_capability_no_category_covers_counts_as_reading_and_sending(monkeypatch):
    """Slice 2 review: composition skipped what it could not classify, so an
    uncategorised sender escaped it. What cannot be judged is assumed able to
    do both."""
    assert O.composition_of(["get_current_time"], provenance=c.READ_MATERIAL,
                            attended=False) is None
    _uncategorised(monkeypatch, "get_current_time")
    denied = O.composition_of(["get_current_time"], provenance=c.READ_MATERIAL,
                              attended=False)
    assert denied is not None and denied.decision == P.DENY
    asked = O.composition_of(["get_current_time"], provenance=c.PERSON, attended=True)
    assert asked is not None and asked.decision == P.CONFIRM


def test_every_category_has_a_composition_class():
    classes = P.PRIVATE_READ | P.ARBITRARY_SEND | P.COMPOSITION_NEUTRAL
    assert set(P.TOOL_CATEGORIES.values()) <= classes, (
        set(P.TOOL_CATEGORIES.values()) - classes)
    assert not (P.PRIVATE_READ & P.ARBITRARY_SEND & P.COMPOSITION_NEUTRAL)


def test_opening_a_url_in_the_signed_in_browser_is_not_open_to_read_material():
    """open_in_browser acts with the person's own logins."""
    verdict = P.provenance_verdict("open_in_browser", c.READ_MATERIAL)
    assert verdict is not None and verdict.denied


# --- the judgement ------------------------------------------------------------

def _tools(*caps):
    return [t for cap in caps for t in C.by_id(cap).policy_tool_ids()]


def test_private_read_alone_or_send_alone_is_not_escalated():
    for caps in ((PRIVATE,), (SEND,), ("system_get_info", SEND)):
        assert P.composition_verdict(_tools(*caps), provenance=c.PERSON,
                                     attended=True) is None, caps


def test_private_read_with_arbitrary_send_escalates():
    verdict = P.composition_verdict(_tools(PRIVATE, SEND), provenance=c.PERSON,
                                    attended=True)
    assert verdict is not None and verdict.decision == P.CONFIRM
    assert "send what it reads" in verdict.reason


@pytest.mark.parametrize("lineage", [
    {"provenance": c.READ_MATERIAL, "attended": True},
    {"provenance": c.PERSON, "attended": False},
])
def test_the_composition_is_refused_from_read_material_or_unattended(lineage):
    verdict = P.composition_verdict(_tools(PRIVATE, SEND), **lineage)
    assert verdict is not None and verdict.decision == P.DENY


# --- at the door every objective comes through --------------------------------

def test_compiling_an_untrusted_exfiltration_shape_is_refused(store):
    with pytest.raises(O.CompileError, match="send what it reads"):
        _compile(store, PRIVATE, SEND, provenance=c.READ_MATERIAL, attended=False)


def test_compiling_it_for_the_person_needs_their_yes(store):
    with pytest.raises(O.CompositionNeedsConfirmation):
        _compile(store, PRIVATE, SEND, provenance=c.PERSON, attended=True)
    created = _compile(store, PRIVATE, SEND, provenance=c.PERSON, attended=True,
                       composition_confirmed=True)
    assert created["run_id"]


def test_objective_start_asks_and_then_spends_the_yes(store, monkeypatch):
    from friday.toolsets import objectives as OT

    OT.reset_store(store)
    try:
        engine = P.PolicyEngine(autonomy=P.FULL)
        run = c.Run.create("objectives")
        request = "read my notes and look this up"
        tasks = '[{"capability": "files_read", "arguments": {}}, {"capability": "web_fetch", "arguments": {}}]'
        asked = OT.objective_start(run, request, tasks=tasks, engine=engine)
        assert asked.status == c.CANCELLED and "APPROVAL_REQUIRED" in asked.error
        nonce = asked.output["confirm"]["nonce"]
        assert CF.book.approve(nonce).ok
        started = OT.objective_start(run, request, tasks=tasks, engine=engine, nonce=nonce)
        assert started.status == c.SUCCEEDED, started.error
        again = OT.objective_start(run, request, tasks=tasks, engine=engine, nonce=nonce)
        assert again.status != c.SUCCEEDED, "one yes started two objectives"
    finally:
        OT.reset_store(None)


def test_dangerous_autonomy_is_the_standing_yes_and_it_is_audited(store):
    from friday.toolsets import objectives as OT

    OT.reset_store(store)
    try:
        started = OT.objective_start(
            c.Run.create("objectives"), "read my notes and look this up",
            tasks='[{"capability": "files_read", "arguments": {}}, '
                  '{"capability": "web_fetch", "arguments": {}}]',
            engine=P.PolicyEngine(autonomy=P.DANGEROUS))
    finally:
        OT.reset_store(None)
    assert started.status == c.SUCCEEDED, started.error
    rows = [r for r in trust.audit().query(limit=20) if r["action"] == "objective.composition"]
    assert rows and rows[0]["decision"] == "AUTO"


def test_the_mcp_tool_spends_the_yes_on_the_run_that_asked(store):
    """The MCP wrapper makes a new Run per call; the retry carrying the nonce
    must run as the run that asked, or the yes (bound to it) cannot apply."""
    from friday.tools import objective_control
    from friday.toolsets import objectives as OT

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register

    OT.reset_store(store)
    try:
        objective_control._engine = P.PolicyEngine(autonomy=P.FULL)
        mcp = FakeMCP()
        objective_control.register(mcp)
        start = mcp.tools["objective_start"]
        tasks = ('[{"capability": "files_read", "arguments": {}}, '
                 '{"capability": "web_fetch", "arguments": {}}]')
        asked = start("read my notes and look this up", tasks=tasks)
        assert asked["status"] == c.CANCELLED
        nonce = asked["output"]["confirm"]["nonce"]
        assert CF.book.approve(nonce).ok
        started = start("read my notes and look this up", tasks=tasks, nonce=nonce)
        assert started["status"] == c.SUCCEEDED, started.get("error")
    finally:
        objective_control._engine = None
        OT.reset_store(None)
