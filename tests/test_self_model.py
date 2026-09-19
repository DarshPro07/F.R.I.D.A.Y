"""ML-05: capability claims come from runtime state, never from a static
prompt. Golden journey §87: switch the camera off, ask; switch it on, ask -
the description changes with no prompt edit.

The negative controls are the point: the two static claims that used to
live in the prompts ("look at his screen or through the camera", "You have
169 tools") must be gone, and a snapshot with no ledger must NOT read as
healthy.
"""
from __future__ import annotations

import json
import sys

import pytest

sys.path.insert(0, ".")

from friday import self_model as sm


@pytest.fixture()
def switches(tmp_path, monkeypatch):
    path = tmp_path / "switches.json"
    monkeypatch.setenv("FRIDAY_SELF_MODEL_SWITCHES", str(path))
    return path


# --------------------------------------------------------------------------
# switches: durable, atomic, named
# --------------------------------------------------------------------------

class TestSwitches:
    def test_off_then_on_round_trips_through_the_file(self, switches):
        assert sm.switches() == {}
        sm.disable("camera", "lens cap on")
        assert json.loads(switches.read_text())["camera"] == "lens cap on"
        assert sm.switches() == {"camera": "lens cap on"}
        sm.enable("camera")
        assert sm.switches() == {}

    def test_unknown_names_are_refused(self, switches):
        with pytest.raises(ValueError, match="not switchable"):
            sm.disable("microwave")
        with pytest.raises(ValueError):
            sm.enable("microwave")

    def test_a_corrupt_file_reads_as_nothing_off_not_a_crash(self, switches):
        switches.write_text("{not json", encoding="utf-8")
        assert sm.switches() == {}


# --------------------------------------------------------------------------
# the snapshot
# --------------------------------------------------------------------------

class TestSnapshot:
    def test_modalities_are_snapshot_only_never_live(self, switches):
        snap = sm.snapshot()
        screen = snap.modality("screen")
        assert screen is not None
        if screen.state == sm.AVAILABLE:
            assert "snapshot" in screen.detail
            assert "live" not in snap.describe().split("never a live feed")[0].lower()
            assert "never a live feed" in snap.describe()

    def test_families_come_from_the_fabric(self, switches):
        snap = sm.snapshot()
        names = {f.name for f in snap.families}
        from friday import fabric
        assert {r["family"] for r in fabric.family_report()} <= names

    def test_no_ledger_is_unprobed_not_healthy(self, switches, tmp_path):
        """FR-034: configuration is not health. A provider named with no
        telemetry evidence must not be described as having current
        evidence."""
        from friday.model_gateway import GatewayTelemetry
        empty = GatewayTelemetry(tmp_path / "empty.sqlite3")
        snap = sm.snapshot(providers=["openai", "anthropic"], telemetry=empty)
        assert snap.healthy_routes() == []
        assert {r.state for r in snap.routes} == {"UNPROBED"}
        assert "No model route has current health evidence" in snap.describe()

    def test_a_healthy_route_needs_a_recent_success_row(self, switches, tmp_path):
        """HEALTHY only after a success WITH visible output; the same row with
        zero output tokens is DEGRADED (transport ok is not semantic ok)."""
        from friday.model_gateway import GatewayTelemetry
        t = GatewayTelemetry(tmp_path / "t.sqlite3")
        t.record(objective_id="o", worker="probe", task_class="probe", provider="openai",
                 model="gpt-x", status="ok", output_tokens=4)
        snap = sm.snapshot(providers=["openai"], telemetry=t)
        assert snap.routes[0].state == "HEALTHY" and snap.healthy_routes() == ["openai"]
        assert "Model routes with current evidence: openai." in snap.describe()
        t2 = GatewayTelemetry(tmp_path / "t2.sqlite3")
        t2.record(objective_id="o", worker="probe", task_class="probe", provider="openai",
                  model="gpt-x", status="ok", output_tokens=0)
        snap2 = sm.snapshot(providers=["openai"], telemetry=t2)
        assert snap2.routes[0].state == "DEGRADED" and snap2.healthy_routes() == []

    def test_tool_count_none_means_do_not_recite(self, switches):
        assert "do not recite a tool count" in sm.snapshot(tool_count=None).describe()
        assert "You have 42 tools" in sm.snapshot(tool_count=42).describe()

    def test_to_dict_is_json_serialisable(self, switches):
        json.dumps(sm.snapshot(tool_count=3).to_dict())


# --------------------------------------------------------------------------
# golden journey §87: disable -> ask -> enable -> ask, no prompt edit
# --------------------------------------------------------------------------

class TestGoldenJourneySelfKnowledge:
    def test_switching_the_camera_off_changes_what_she_claims(self, switches):
        before = sm.snapshot().describe()
        sm.disable("camera", "lens cap on")
        off = sm.snapshot()
        assert off.modality("camera").state == sm.DISABLED
        assert not off.can("camera")
        text = off.describe()
        assert "cannot look at the camera" in text and "lens cap on" in text
        sm.enable("camera")
        after = sm.snapshot().describe()
        assert after == before                       # no prompt edit, same claim again

    def test_switching_a_family_off_removes_it_from_the_offer(self, switches):
        sm.disable("browser", "profile under review")
        text = sm.snapshot().describe()
        assert "browser is switched off (profile under review); do not offer it." in text

    def test_both_prompt_paths_carry_the_live_model(self, switches, monkeypatch):
        """voice_brain._persona() and agent_friday.build_instructions()
        both inject the snapshot; toggling a switch is visible in both."""
        import friday.voice_brain as V
        import agent_friday as A
        monkeypatch.setattr(V._policy if hasattr(V, "_policy") else __import__("friday.policy", fromlist=["x"]),
                            "skip_permissions", lambda: False, raising=False)
        sm.disable("camera", "lens cap on")
        assert "lens cap on" in V._persona()
        assert "lens cap on" in A.build_instructions(tool_count=7)
        assert "You have 7 tools" in A.build_instructions(tool_count=7)
        sm.enable("camera")
        assert "lens cap on" not in V._persona()
        assert "lens cap on" not in A.build_instructions()


# --------------------------------------------------------------------------
# negative controls: the static claims are gone
# --------------------------------------------------------------------------

class TestStaticClaimsAreGone:
    def test_persona_no_longer_hardcodes_screen_or_camera(self):
        import friday.voice_brain as V
        p = V.PERSONA.lower()
        assert "look at his screen" not in p and "through the camera" not in p

    def test_system_prompt_no_longer_hardcodes_a_tool_count(self):
        import re
        import agent_friday as A
        assert not re.search(r"You have \d+ tools", A.SYSTEM_PROMPT)

    def test_an_unreadable_self_model_yields_no_claim_not_a_stale_one(self, switches, monkeypatch):
        import friday.voice_brain as V
        import agent_friday as A

        def boom(**_):
            raise RuntimeError("fabric exploded")
        monkeypatch.setattr(sm, "snapshot", boom)
        assert "cannot look" not in V._persona() and "You can look" not in V._persona()
        text = A.build_instructions()
        assert "self-model could not be read" in text
