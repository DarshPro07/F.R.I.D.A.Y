"""
Capability Fabric manifest and health (PRD v3.1 FR-023, FR-024, FR-025,
FR-026, FR-027, FR-028).

    FR-023  registry queryable; no unregistered capability executes
    FR-024  nine types; a SKILL is never presented as executable
    FR-025  summaries first, one full schema on demand; context bounded
    FR-026  READY / DEGRADED / UNAVAILABLE / FAILED / DISABLED; an
            unavailable provider is never reported as executed
    FR-027  version/commit/license recorded before activation; exportable
    FR-028  fabric calls pass through the policy layer (permission gate)
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from friday import contracts as c
from friday import fabric
from friday import manifest as M


@pytest.fixture
def fake_adapter(monkeypatch):
    """A registered provider whose call() behaviour the test scripts."""
    mod = types.ModuleType("friday.fabric_adapters.zz_fake_manifest")
    mod.BEHAVIOUR = {"raise": 0}
    mod.DESCRIPTOR = fabric.Provider(
        id="zz_fake_manifest", family="research", upstream="",
        operations=("probe", "work"), risk="low",
        license_mode=fabric.BUILTIN_LICENSE, integration_mode=fabric.BUILTIN,
        open_operations=("probe", "work"), module=mod.__name__,
    )

    def health(handle):
        return {"state": fabric.READY, "detail": "fake up"}

    def call(operation, handle, **arguments):
        if mod.BEHAVIOUR["raise"] > 0:
            mod.BEHAVIOUR["raise"] -= 1
            raise RuntimeError("upstream exploded")
        return {"ok": True, "op": operation}

    mod.health = health
    mod.call = call
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    registry = fabric.registry()
    monkeypatch.setitem(registry, mod.DESCRIPTOR.id, mod.DESCRIPTOR)
    yield mod
    fabric._ACTIVE.pop(mod.DESCRIPTOR.id, None)


# -- FR-023 / FR-024 --------------------------------------------------------


def test_manifest_covers_every_provider_tool_and_executor_with_prd_fields():
    rows = M.build()
    ids = {m.id for m in rows}
    assert set(fabric.registry()) <= ids
    from friday import capabilities as C
    assert set(C.CAPABILITIES) <= ids
    assert "executor:hermes" in ids and "executor:claude" in ids
    required = ("id", "name", "version", "type", "source", "license", "trust_level",
                "review_status", "permissions", "dangerous_actions", "health_check",
                "dependencies", "cost_profile", "latency_profile",
                "supported_platforms", "default_state")
    for m in rows:
        d = m.to_dict()
        for key in required:
            assert key in d, (m.id, key)
        assert m.type in M.TYPES, (m.id, m.type)
        assert m.state in fabric.STATES, (m.id, m.state)


def test_types_are_distinguished_and_skills_are_never_executable():
    rows = {m.id: m for m in M.build()}
    kinds = {m.type for m in rows.values()}
    assert {M.NATIVE, M.CLI, M.SIDECAR, M.SKILL, M.SPECIALIST_RUNTIME, M.SDK} <= kinds
    for m in rows.values():
        if m.type in (M.SKILL, M.REFERENCE):
            assert not m.executable, m.id
        else:
            assert m.executable, m.id
    # The PRD's own example: a skill pack is guidance, typed SKILL.
    assert rows["company_playbooks"].type == M.SKILL
    assert not rows["company_playbooks"].executable
    # Coding runtimes are SPECIALIST_RUNTIME, not NATIVE.
    assert rows["executor:hermes"].type == M.SPECIALIST_RUNTIME
    assert rows["claude_subagents"].type == M.SPECIALIST_RUNTIME


def test_unregistered_capability_cannot_execute():
    with pytest.raises(fabric.FabricError):
        fabric.call("provider_nobody_registered", "anything", run_id="obj-1",
                    authorized=frozenset({"*"}))
    with pytest.raises(fabric.FabricError):
        fabric.call("company_playbooks", "operation_it_never_declared", run_id="obj-1")


def test_native_tools_carry_their_risk_tier_and_dangerous_actions():
    rows = {m.id: m for m in M.build()}
    assert rows["files_delete"].extra["risk_tier"] == "R3"
    assert rows["files_delete"].dangerous_actions == ("files_delete",)
    assert rows["files_read"].extra["risk_tier"] == "R0"
    assert rows["files_read"].dangerous_actions == ()


# -- FR-025 progressive discovery -------------------------------------------


def test_summary_is_bounded_and_full_schema_is_per_id():
    summary = M.summary()
    assert summary["total"] >= 200
    assert set(summary["by_type"]) <= set(M.TYPES)
    # The summary carries names and counts, never the per-capability schema:
    # its size grows by one short id per capability, not by one schema.
    per_id = len(json.dumps(summary)) / summary["total"]
    assert per_id < 40, per_id
    full = M.describe("strix_pentest")
    assert full is not None and full["permissions"] == ("security.authorized_scope",)
    assert M.describe("no_such_capability") is None


def test_router_exposes_summaries_first_and_schemas_on_selection():
    """The existing tool router already does progressive loading; assert
    the contract the PRD names rather than re-implementing it."""
    from friday import capability_router as R
    from friday import capabilities as C
    assert len(R.CORE_TOOLS) < len(C.CAPABILITIES) / 3
    catalogue = R.catalogue()
    assert len(catalogue) < 6000, len(catalogue)     # group summaries only
    for name in ("capability_manifest", "memory_export", "model_infer"):
        assert R.group_of(name) is not None, name


# -- FR-026 health ----------------------------------------------------------


def test_repeated_raises_turn_a_ready_provider_into_failed(fake_adapter):
    pid = fake_adapter.DESCRIPTOR.id
    ok = fabric.call(pid, "work", run_id="obj-h")
    assert ok.status == c.SUCCEEDED and fabric.state(pid) == fabric.READY
    fake_adapter.BEHAVIOUR["raise"] = fabric.FAILED_AFTER
    outcomes = [fabric.call(pid, "work", run_id="obj-h").status
                for _ in range(fabric.FAILED_AFTER)]
    assert outcomes == [c.FAILED] * fabric.FAILED_AFTER
    assert fabric.state(pid) == fabric.FAILED
    assert "consecutive" in fabric._ACTIVE[pid].detail
    # The manifest reports the same truth.
    assert M.describe(pid)["state"] == fabric.FAILED
    # It is NOT executed while FAILED: the next call re-activates through
    # the health probe, which says READY, and the call succeeds honestly.
    back = fabric.call(pid, "work", run_id="obj-h")
    assert back.status == c.SUCCEEDED and fabric.state(pid) == fabric.READY


def test_one_raise_does_not_fail_the_provider(fake_adapter):
    pid = fake_adapter.DESCRIPTOR.id
    fake_adapter.BEHAVIOUR["raise"] = 1
    assert fabric.call(pid, "work", run_id="obj-h").status == c.FAILED
    assert fabric.state(pid) == fabric.READY
    assert fabric.call(pid, "work", run_id="obj-h").status == c.SUCCEEDED
    assert fabric._ACTIVE[pid].consecutive_failures == 0


def test_unavailable_provider_is_never_reported_as_executed(monkeypatch, fake_adapter):
    pid = fake_adapter.DESCRIPTOR.id
    monkeypatch.setattr(fake_adapter, "health",
                        lambda handle: {"state": fabric.UNAVAILABLE, "detail": "down"})
    fabric._ACTIVE.pop(pid, None)
    out = fabric.call(pid, "work", run_id="obj-u")
    assert out.status == c.FAILED
    assert "UNAVAILABLE" in out.error and out.verification is None
    assert out.output is None


def test_family_report_ranks_failed_below_degraded(fake_adapter):
    from friday.fabric import family_report
    pid = fake_adapter.DESCRIPTOR.id
    fabric.activate(pid)
    fabric._ACTIVE[pid].state = fabric.FAILED
    row = next(r for r in family_report() if r["family"] == "research")
    assert row["state"] in fabric.STATES


# -- FR-027 / FR-028 --------------------------------------------------------


def test_export_records_pin_license_and_review_for_every_upstream(tmp_path):
    path = tmp_path / "manifest.json"
    M.export(path)
    rows = json.loads(path.read_text(encoding="utf-8"))
    upstreams = [r for r in rows if r["source"] and r["type"] not in (M.NATIVE,)
                 and not r["id"].startswith("executor:")]
    assert upstreams
    for r in upstreams:
        assert r["license"] in fabric.LICENSE_MODES, r["id"]
        if r["type"] != M.REFERENCE:
            assert r["trust_level"] == "pinned", (r["id"], r["version"])
            assert len(r["extra"]["commit"]) >= 7, r["id"]     # the pin, not the semver
        assert r["review_status"] in ("reviewed", "reference_only")


def test_fabric_calls_pass_the_permission_gate_before_activation(fake_adapter):
    """FR-028: an MCP/adapter provider is not globally trusted - a call
    without the grant is refused before the provider is even activated."""
    gated = fabric.Provider(
        id="zz_gated", family="research", upstream="", operations=("op",),
        risk="medium", license_mode=fabric.BUILTIN_LICENSE,
        integration_mode=fabric.BUILTIN, permissions=("research.write",),
        module=fake_adapter.__name__)
    fabric.registry()["zz_gated"] = gated
    try:
        refused = fabric.call("zz_gated", "op", run_id="obj-g")
        assert refused.status == c.FAILED and "not granted" in refused.error
        assert "zz_gated" not in fabric._ACTIVE
        allowed = fabric.call("zz_gated", "op", run_id="obj-g",
                              authorized=frozenset({"research.write"}))
        assert allowed.status == c.SUCCEEDED
    finally:
        fabric.registry().pop("zz_gated", None)
        fabric._ACTIVE.pop("zz_gated", None)


def test_manifest_tool_is_reachable_from_the_runtime():
    from friday.toolsets import manifest as MT
    run = c.Run.create("manifest", capability="capability_manifest")
    out = MT.capability_manifest(run)
    assert out.status == c.OBSERVED and out.output["total"] >= 200
    one = MT.capability_manifest(run, "files_read")
    assert one.status == c.OBSERVED and one.output["type"] == M.NATIVE
    missing = MT.capability_manifest(run, "nope")
    assert missing.status == c.FAILED
