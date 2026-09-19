"""The skill-intelligence / self-model toolset speaks the ActionResult
contract, so a durable objective can call it and get a verified result.

These seven capabilities used to be MCP-only (plain dicts), which put
them in `capability_runtime.unresolved()` and dropped reach to 165/200.
Each function here is checked for the property that matters for reach:
it takes `run`, it verifies by read-back, and it refuses (not raises) on
bad input - because a raised exception inside an objective is classified
as a failure kind, while a refusal names its reason.
"""
from __future__ import annotations

import json

import pytest

from friday import contracts as c
from friday import skill_ladder as sl
from friday.policy import PolicyEngine
from friday.toolsets import skills as S


@pytest.fixture
def ladder(tmp_path, monkeypatch):
    """A ladder of the test's own, so nothing here touches data/ada.sqlite3."""
    db = tmp_path / "ladder.sqlite3"
    real = sl.SkillLadder

    class TestLadder(real):
        def __init__(self, path=None):
            super().__init__(db)
    monkeypatch.setattr(sl, "SkillLadder", TestLadder)
    return TestLadder()


@pytest.fixture
def switches(tmp_path, monkeypatch):
    from friday import self_model
    monkeypatch.setattr(self_model, "_switch_path", lambda: tmp_path / "switches.json")
    return tmp_path / "switches.json"


def _run(label="skills") -> c.Run:
    return c.Run.create(label, capability="skills")


def _validated(ladder, name="deploy-notes", procedure="1. run tests\n2. tag"):
    ladder.capture(name, procedure, criteria=["repeated_procedure"], evidence="did it thrice")
    # promote to VALIDATED the way the ladder does
    with ladder._connect() as db:
        db.execute("UPDATE skill_candidates SET state='VALIDATED' WHERE name=?", (name,))
    return name


class TestReach:
    """The property the whole module exists for."""

    def test_all_seven_resolve_to_this_toolset(self):
        from friday import capability_runtime as R
        res = R.resolutions()
        for cap in ("skill_declare_dependencies", "skill_revalidation_sweep", "skill_declare_permissions",
                    "skill_behavior_scenarios", "skill_behavior_grade", "self_model_snapshot",
                    "self_model_switch"):
            assert cap in res, f"{cap} is unresolved - back to MCP-only"
            assert res[cap].module == "friday.toolsets.skills"
        assert len(R.unresolved()) <= 28, R.unresolved()


class TestFingerprints:
    def test_declare_records_and_reads_back(self, ladder, tmp_path, monkeypatch):
        name = _validated(ladder)
        target = tmp_path / "dep.py"
        target.write_text("x = 1\n")
        res = S.skill_declare_dependencies(_run(), name, f"file:{target}", engine=PolicyEngine())
        assert res.status == c.SUCCEEDED, res.error
        assert res.verification.method == "ladder_readback"
        assert res.output["dependencies"] == 1 and res.output["missing"] == []

    def test_a_missing_dependency_is_reported_not_hidden(self, ladder):
        name = _validated(ladder)
        res = S.skill_declare_dependencies(_run(), name, "file:D:/nowhere/never.py", engine=PolicyEngine())
        assert res.status == c.SUCCEEDED
        assert res.output["missing"] == ["D:/nowhere/never.py"]
        assert "missing" in res.verification.evidence

    def test_unknown_skill_and_empty_dependencies_are_refusals_not_raises(self, ladder):
        assert S.skill_declare_dependencies(_run(), "ghost", "file:x", engine=PolicyEngine()).status == c.FAILED
        name = _validated(ladder)
        res = S.skill_declare_dependencies(_run(), name, "   ", engine=PolicyEngine())
        assert res.status == c.FAILED and "no dependencies" in res.error

    def test_sweep_marks_only_what_moved_and_reads_it_back(self, ladder, tmp_path):
        moved = _validated(ladder, "moves")
        still = _validated(ladder, "still")
        a, b = tmp_path / "a.py", tmp_path / "b.py"
        a.write_text("1"); b.write_text("1")
        S.skill_declare_dependencies(_run(), moved, f"file:{a}", engine=PolicyEngine())
        S.skill_declare_dependencies(_run(), still, f"file:{b}", engine=PolicyEngine())
        a.write_text("2")
        res = S.skill_revalidation_sweep(_run(), engine=PolicyEngine())
        assert res.status == c.SUCCEEDED, res.error
        assert [s["skill"] for s in res.output["stale"]] == [moved]
        assert res.output["clean"] == [still]
        assert ladder.current(moved)["state"] == "NEEDS_REVALIDATION"
        assert ladder.current(still)["state"] == "VALIDATED"


class TestPermissions:
    GOOD = "risk: LOW\npermissions:\n  capabilities: [files_read]\n"
    BAD = "risk: LOW\npermissions:\n  capabilities: ['*']\n"

    def test_a_manifest_is_recorded_and_read_back(self, ladder):
        name = _validated(ladder)
        res = S.skill_declare_permissions(_run(), name, self.GOOD, engine=PolicyEngine())
        assert res.status == c.SUCCEEDED, res.error
        assert res.verification.method == "ladder_readback" and "risk LOW" in res.verification.evidence

    def test_a_grant_shaped_manifest_is_refused_as_not_permitted(self, ladder):
        name = _validated(ladder)
        res = S.skill_declare_permissions(_run(), name, self.BAD, engine=PolicyEngine())
        assert res.status == c.NOT_PERMITTED
        assert "refused" in res.error and res.output["status"] == "refused"
        assert res.may_claim_completion is False


class TestBehaviour:
    def test_scenarios_carry_all_three_strictness_levels(self):
        res = S.skill_behavior_scenarios(_run(), "tdd", "add a parser", engine=PolicyEngine())
        assert res.status == c.SUCCEEDED
        assert {s["strictness"] for s in res.output["scenarios"]} == {"supportive", "neutral", "competing"}

    def test_scenarios_need_a_skill_and_a_task(self):
        assert S.skill_behavior_scenarios(_run(), "", "x", engine=PolicyEngine()).status == c.FAILED

    def test_grade_returns_the_verdict_and_names_its_scenarios(self):
        spec = {"skill": "tdd", "steps": [
            {"id": "red", "description": "run tests first", "detector": {"tool": "Bash", "arguments_match": "pytest"}},
        ]}
        trace = [{"tool": "Bash", "arguments": {"command": "pytest -q"}, "status": "ok", "output": "1 failed"}]
        res = S.skill_behavior_grade(_run(), json.dumps(spec),
                                     json.dumps({"supportive": trace, "neutral": trace, "competing": trace}),
                                     engine=PolicyEngine())
        assert res.status == c.SUCCEEDED, res.error
        from friday import skill_behavior as sb
        assert res.output["verdict"] in (sb.COMPLIANT, sb.NON_COMPLIANT, sb.INCOMPLETE)
        assert res.output["verdict"] == sb.COMPLIANT, "all three traces ran pytest first"
        assert "supportive" in res.verification.evidence

    def test_grade_with_a_bad_spec_is_a_refusal(self):
        res = S.skill_behavior_grade(_run(), "not json", "{}", engine=PolicyEngine())
        assert res.status == c.FAILED and "bad spec" in res.error


class TestSelfModel:
    def test_snapshot_is_a_verified_read(self):
        res = S.self_model_snapshot(_run(), engine=PolicyEngine())
        assert res.status == c.SUCCEEDED, res.error
        assert res.output["description"] and "modalities" in res.output
        assert res.verification.method == "runtime_snapshot"

    def test_switch_off_and_on_are_read_back_from_disk(self, switches):
        off = S.self_model_switch(_run(), "camera", False, "lens cap", engine=PolicyEngine())
        assert off.status == c.SUCCEEDED, off.error
        assert json.loads(switches.read_text())["camera"] == "lens cap"
        on = S.self_model_switch(_run(), "camera", True, engine=PolicyEngine())
        assert on.status == c.SUCCEEDED, on.error
        assert "camera" not in json.loads(switches.read_text())

    def test_an_unknown_switch_is_a_refusal(self, switches):
        res = S.self_model_switch(_run(), "warp_drive", False, engine=PolicyEngine())
        assert res.status == c.FAILED and "not switchable" in res.error
        assert not switches.exists() or "warp_drive" not in switches.read_text()

    def test_switch_is_gated_by_policy(self, switches):
        res = S.self_model_switch(_run(), "camera", False, engine=PolicyEngine({"DEVICE_SETTING": "DENY"}))
        assert res.status == c.CANCELLED
        assert not switches.exists()
