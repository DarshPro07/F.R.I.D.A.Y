"""AT-12 / AT-13 from the action-truth program.

AT-12 — a capability alias is one spelling of one canonical id, resolved by
every resolver (`capabilities.by_id`, `Router.invocable`,
`CapabilityRuntime.execute`), and NO alias may shadow a real capability.
AT-13 — `skill_declare_permissions` on an uncaptured skill is a TYPED
prerequisite (`PREREQUISITE_REQUIRED` naming `skill_capture`), never a bare
"no such skill" failure.

Negative controls: a fake alias that collides with a real id is caught by the
collision check; a captured skill does NOT take the prerequisite path.
"""
from __future__ import annotations

import json

import pytest

from friday import capabilities as C
from friday import capability_router as CR
from friday import capability_runtime as R
from friday import contracts as c
from friday import skill_ladder as sl
from friday.toolsets import skills as S


# --------------------------------------------------------------------------
# AT-12 alias
# --------------------------------------------------------------------------

def test_no_alias_shadows_a_real_capability():
    for alias, target in C.ALIASES.items():
        assert alias not in C.CAPABILITIES, f"alias {alias!r} collides with a real capability"
        assert target in C.CAPABILITIES, f"alias {alias!r} points at unknown {target!r}"
        assert alias != target


def test_collision_check_would_catch_a_shadowing_alias(monkeypatch):
    """Negative control: the invariant above is a real check, not a tautology."""
    bad = dict(C.ALIASES)
    bad["objective_status"] = "objective_start"      # a real id used as an alias
    monkeypatch.setattr(C, "ALIASES", bad)
    with pytest.raises(AssertionError):
        for alias in C.ALIASES:
            assert alias not in C.CAPABILITIES


def test_hallucinated_name_resolves_to_objective_start():
    assert C.canonical_id("orchestration_new_objective") == "objective_start"
    assert C.by_id("orchestration_new_objective") is C.by_id("objective_start")
    assert C.canonical_id("objective_start") == "objective_start"       # canonical is a fixed point
    assert C.by_id("no_such_thing_at_all") is None                       # unknown stays unknown


def test_router_invocable_reaches_the_canonical_tool():
    calls = []

    async def objective_start(arguments):
        calls.append(arguments)
        return "ok"

    router = CR.Router.__new__(CR.Router)
    router.all_tools = {"objective_start": objective_start}
    assert router.invocable("orchestration_new_objective") is objective_start
    assert router.invocable("objective_start") is objective_start
    assert router.invocable("orchestration_nothing") is None


def test_runtime_executes_the_alias_as_the_canonical_capability():
    """The run, the authority check and the refusal all carry the REAL id."""
    class RefuseAll:
        asked = []

        def permits(self, capability_id):
            self.asked.append(capability_id)
            return f"{capability_id} is outside this authority"

    auth = RefuseAll()
    rt = R.CapabilityRuntime(authority=auth)
    out = rt.execute("orchestration_new_objective", {"goal": "x"})
    assert out.status == c.NOT_PERMITTED
    assert auth.asked == ["objective_start"]                 # asked about the canonical id only
    assert "orchestration_new_objective" not in (out.error or "")
    assert "objective_start" in (out.error or "")


def test_objective_start_search_example_names_the_waiting_objective():
    cap = C.by_id("objective_start")
    assert any("waits for a file" in ex for ex in cap.intent_examples)
    assert any("new objective" in ex for ex in cap.intent_examples)


def test_objective_examples_reach_objective_start_through_the_router():
    """The model's own words for the M1 step ("start an objective that
    waits for the file...") must rank objective_start in the top three -
    the registry-wide reachability rule. A phrasing that OPENS with another
    capability's suffix verb (`create ...`) routes there instead (the
    2026-09-20 suite catch), so the examples lead with `start`."""
    from friday import capability_router as CR

    class FakeTool:                                     # the MCPToolset shape the router reads
        def __init__(self, name, description):
            self.info = type("Info", (), {"name": name,
                                          "raw_schema": {"description": description, "parameters": {}}})()
    router = CR.Router()
    router.load([FakeTool(cap.id, cap.description) for cap in C._ALL])
    for phrase in C.by_id("objective_start").intent_examples:
        ranked = [m["capability"] for m in router.search(phrase, limit=6)]
        assert "objective_start" in ranked[:3], (phrase, ranked[:3])


# --------------------------------------------------------------------------
# AT-13 typed prerequisite
# --------------------------------------------------------------------------

@pytest.fixture
def ladder(tmp_path, monkeypatch):
    db = tmp_path / "ladder.sqlite3"
    real = sl.SkillLadder

    class TestLadder(real):
        def __init__(self, path=None):
            super().__init__(db)
    monkeypatch.setattr(sl, "SkillLadder", TestLadder)
    return TestLadder()


MANIFEST = "risk: low\nrequests:\n  - files_read\n"


def test_declare_permissions_on_uncaptured_skill_is_a_typed_prerequisite(ladder):
    run = c.Run.create("skills", capability="skills")
    out = S.skill_declare_permissions(run, "never-captured", MANIFEST)
    assert out.status == c.NOT_PERMITTED
    assert out.error.startswith("PREREQUISITE_REQUIRED")
    assert "skill_capture" in out.error
    assert out.output["error_type"] == "PREREQUISITE_REQUIRED"
    assert out.output["prerequisite"] == "skill_capture"
    assert out.output["next_call"]["capability"] == "skill_capture"
    assert out.output["next_call"]["arguments"]["name"] == "never-captured"
    assert C.by_id("skill_capture") is not None                          # the named prerequisite exists


def test_captured_skill_does_not_take_the_prerequisite_path(ladder):
    """Negative control for AT-13: the typed branch fires only for a missing ladder entry."""
    ladder.capture("deploy-notes", "1. run tests\n2. tag",
                   criteria=["repeated_procedure"], evidence="did it thrice")
    run = c.Run.create("skills", capability="skills")
    out = S.skill_declare_permissions(run, "deploy-notes", MANIFEST)
    assert not (out.error or "").startswith("PREREQUISITE_REQUIRED")
    assert (out.output or {}).get("error_type") != "PREREQUISITE_REQUIRED"
