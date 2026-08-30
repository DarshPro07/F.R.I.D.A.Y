"""
The Capability Fabric's contract, proved against the deterministic dummy.

Every assertion here is about the fabric, not about an upstream. That is the
point of `fabric_adapters/dummy`: when one of these fails it is a fact about
this code, and when a real provider's test fails later it is a fact about that
provider.
"""

from __future__ import annotations

import pytest

from friday import contracts as c
from friday import fabric
from friday.fabric_adapters import dummy, dummy_backup


@pytest.fixture(autouse=True)
def clean_fabric():
    """Each test starts with nothing activated and the dummy behaving."""
    dummy.BEHAVIOUR = "ok"
    dummy.STARTS = 0
    dummy_backup.CALLS = 0
    fabric.reload()
    yield
    fabric.deactivate_all()
    dummy.BEHAVIOUR = "ok"


# --- registry --------------------------------------------------------------


def test_registry_is_discovered_not_written_down():
    """A new adapter module appears without editing a table."""
    assert "dummy" in fabric.registry()
    assert fabric.get("dummy").module == "friday.fabric_adapters.dummy"


def test_registering_starts_nothing():
    """NON_NEGOTIABLE 10: installed is not running."""
    fabric.registry()
    assert dummy.STARTS == 0
    assert fabric.state("dummy") == fabric.REGISTERED


def test_unknown_provider_names_itself():
    with pytest.raises(fabric.FabricError, match="no such provider"):
        fabric.get("does-not-exist")


# --- descriptor invariants -------------------------------------------------


def test_copyleft_cannot_declare_an_importing_integration_mode():
    """
    NON_NEGOTIABLE 9, enforced at construction.

    This is the test that stops an AGPL upstream being imported into a
    proprietary process by whoever writes the adapter next.
    """
    with pytest.raises(fabric.FabricError, match="isolated integration mode"):
        fabric.Provider(
            id="bad", family="media", upstream="openmontage",
            operations=("render",), risk="low",
            license_mode=fabric.COPYLEFT,
            integration_mode=fabric.ADAPTER,
            commit="0" * 40,
        )


def test_copyleft_sidecar_is_allowed():
    provider = fabric.Provider(
        id="ok", family="media", upstream="openmontage",
        operations=("render",), risk="low",
        license_mode=fabric.COPYLEFT,
        integration_mode=fabric.SIDECAR,
        commit="0" * 40,
    )
    assert provider.imported is False


def test_an_upstream_must_be_pinned():
    """NON_NEGOTIABLE 6: unpinned means unaudited."""
    with pytest.raises(fabric.FabricError, match="not pinned"):
        fabric.Provider(
            id="unpinned", family="scraping", upstream="scrapling",
            operations=("fetch",), risk="low",
            license_mode=fabric.PERMISSIVE,
            integration_mode=fabric.ADAPTER,
        )


def test_reference_only_needs_no_pin():
    """Something we only read for patterns has no runtime to pin."""
    provider = fabric.Provider(
        id="ref", family="orchestration", upstream="crewai",
        operations=("read",), risk="low",
        license_mode=fabric.PERMISSIVE,
        integration_mode=fabric.REFERENCE_ONLY,
    )
    assert provider.integration_mode == fabric.REFERENCE_ONLY


def test_a_provider_must_declare_operations():
    with pytest.raises(fabric.FabricError, match="declares no operations"):
        fabric.Provider(
            id="silent", family="search", upstream="",
            operations=(), risk="low",
            license_mode=fabric.BUILTIN_LICENSE,
            integration_mode=fabric.BUILTIN,
        )


def test_every_registered_fallback_exists():
    """A failover path that points at nothing is a 3am LookupError."""
    for provider in fabric.registry().values():
        for target in provider.fallbacks:
            assert target in fabric.registry()


# --- lifecycle -------------------------------------------------------------


def test_activation_is_lazy_and_idempotent():
    assert dummy.STARTS == 0
    first = fabric.activate("dummy")
    second = fabric.activate("dummy")
    assert first.state == fabric.READY
    assert second is first
    assert dummy.STARTS == 1, "activate ran start() twice"


def test_a_provider_that_will_not_start_is_recorded_not_raised():
    """NON_NEGOTIABLE 15: an optional provider failing is not a crash."""
    dummy.BEHAVIOUR = "start_fails"
    activation = fabric.activate("dummy")
    assert activation.state == fabric.UNAVAILABLE
    assert "told not to start" in activation.detail


def test_health_probe_raising_is_contained():
    dummy.BEHAVIOUR = "health_raises"
    assert fabric.activate("dummy").state == fabric.UNAVAILABLE


def test_degraded_is_reported_as_degraded_not_ready():
    dummy.BEHAVIOUR = "degraded"
    assert fabric.activate("dummy").state == fabric.DEGRADED


def test_auth_required_is_its_own_state():
    """A missing credential is a user boundary, not a broken provider."""
    dummy.BEHAVIOUR = "auth"
    assert fabric.activate("dummy").state == fabric.AUTH_REQUIRED


def test_deactivate_is_safe_when_nothing_is_up():
    fabric.deactivate("dummy")          # must not raise


def test_health_without_a_probe_makes_the_weak_claim_out_loud():
    """
    An adapter with no health probe is READY on the strength of importing,
    and the detail says exactly that rather than implying it was checked.
    """
    module = type(dummy)("stub")
    module.DESCRIPTOR = fabric.Provider(
        id="probeless", family="diagnostic", upstream="",
        operations=("ping",), risk="low",
        license_mode=fabric.BUILTIN_LICENSE,
        integration_mode=fabric.BUILTIN,
        module="tests.probeless_stub",
    )
    import sys
    sys.modules["tests.probeless_stub"] = module
    try:
        fabric._REGISTRY = dict(fabric.registry())
        fabric._REGISTRY["probeless"] = module.DESCRIPTOR
        probe = fabric.activate("probeless")
        assert probe.state == fabric.READY
        assert "no health probe declared" in probe.detail
    finally:
        del sys.modules["tests.probeless_stub"]
        fabric.reload()


# --- routing ---------------------------------------------------------------


def test_router_prefers_the_cheaper_provider():
    """
    The selection rule, minimally: free beats cheap at equal risk. dummy is
    `free` and dummy_backup is `cheap`, so dummy wins.
    """
    order = [p.id for p in fabric.candidates("diagnostic", "ping")]
    assert order == ["dummy", "dummy_backup"]


def test_router_skips_a_provider_that_cannot_come_up():
    dummy.BEHAVIOUR = "start_fails"
    chosen = fabric.select("diagnostic", "ping")
    assert chosen is not None and chosen.id == "dummy_backup"


def test_router_filters_by_operation():
    assert fabric.candidates("diagnostic", "no_such_op") == ()


def test_router_filters_by_permission():
    """A provider whose permission is not granted is not a candidate."""
    fabric._REGISTRY = dict(fabric.registry())
    fabric._REGISTRY["gated"] = fabric.Provider(
        id="gated", family="diagnostic", upstream="",
        operations=("ping",), risk="low",
        license_mode=fabric.BUILTIN_LICENSE,
        integration_mode=fabric.BUILTIN,
        permissions=("security.pentest",),
        module="friday.fabric_adapters.dummy_backup",
    )
    try:
        allowed = [p.id for p in fabric.candidates(
            "diagnostic", "ping", authorized=frozenset())]
        assert "gated" not in allowed
        granted = [p.id for p in fabric.candidates(
            "diagnostic", "ping", authorized=frozenset({"security.pentest"}))]
        assert "gated" in granted
    finally:
        fabric.reload()


def test_allow_model_false_excludes_model_backed_providers():
    fabric._REGISTRY = dict(fabric.registry())
    fabric._REGISTRY["thinks"] = fabric.Provider(
        id="thinks", family="diagnostic", upstream="",
        operations=("ping",), risk="low",
        license_mode=fabric.BUILTIN_LICENSE,
        integration_mode=fabric.BUILTIN,
        model_required=True,
        module="friday.fabric_adapters.dummy_backup",
    )
    try:
        cheap = [p.id for p in fabric.candidates(
            "diagnostic", "ping", allow_model=False)]
        assert "thinks" not in cheap
    finally:
        fabric.reload()


def test_reference_only_is_never_routed_to():
    fabric._REGISTRY = dict(fabric.registry())
    fabric._REGISTRY["paper"] = fabric.Provider(
        id="paper", family="diagnostic", upstream="crewai",
        operations=("ping",), risk="low",
        license_mode=fabric.PERMISSIVE,
        integration_mode=fabric.REFERENCE_ONLY,
        module="friday.fabric_adapters.dummy_backup",
    )
    try:
        assert "paper" not in [p.id for p in fabric.candidates("diagnostic", "ping")]
        assert fabric.state("paper") == fabric.REFERENCE_ONLY
    finally:
        fabric.reload()


# --- invocation and honesty ------------------------------------------------


def test_call_returns_an_honest_envelope_correlated_to_the_run():
    result = fabric.call("dummy", "echo", run_id="OBJ-42", value="hello")
    assert result.status == c.SUCCEEDED
    assert result.output == "hello"
    assert result.run_id == "OBJ-42", "a fabric call must name the objective"
    assert result.tool_id == "fabric.dummy.echo"
    assert result.verification is not None


def test_a_succeeded_result_cannot_exist_without_verification():
    """The contracts invariant still holds through the fabric."""
    result = fabric.call("dummy", "ping", run_id="OBJ-1")
    assert result.status == c.SUCCEEDED and result.verification is not None
    assert "dummy.ping returned" in result.verification.evidence


def test_calling_an_undeclared_operation_is_a_construction_error():
    with pytest.raises(fabric.FabricError, match="does not declare operation"):
        fabric.call("dummy", "launch_missiles")


def test_a_raising_provider_becomes_a_failed_result_not_an_exception():
    dummy.BEHAVIOUR = "call_raises"
    result = fabric.call("dummy", "ping", run_id="OBJ-9")
    assert result.status == c.FAILED
    assert "told to raise" in result.error
    assert result.run_id == "OBJ-9", "the objective survives the provider"


def test_calling_an_unavailable_provider_says_which_state_it_is_in():
    dummy.BEHAVIOUR = "unavailable"
    result = fabric.call("dummy", "ping")
    assert result.status == c.FAILED
    assert fabric.UNAVAILABLE in result.error


# --- fallback --------------------------------------------------------------


def test_fallback_walks_past_a_broken_provider_and_records_it():
    dummy.BEHAVIOUR = "call_raises"
    result = fabric.call_with_fallback("diagnostic", "ping", run_id="OBJ-7")
    assert result.status == c.SUCCEEDED
    assert result.output == "pong-from-backup"
    assert dummy_backup.CALLS == 1
    assert any("fell back past dummy" in s for s in result.side_effects), \
        "a silent failover is an unexplained cost"


def test_fallback_does_not_run_the_backup_when_the_first_provider_works():
    result = fabric.call_with_fallback("diagnostic", "ping")
    assert result.output == "pong"
    assert dummy_backup.CALLS == 0, "duplicate provider call for one request"


def test_an_exhausted_chain_names_every_provider_it_tried():
    dummy.BEHAVIOUR = "call_raises"
    result = fabric.call_with_fallback("diagnostic", "no_such_op")
    assert result.status == c.FAILED
    assert "no provider available" in result.error


# --- reporting -------------------------------------------------------------


def test_family_report_names_families_not_brands():
    """
    The user asks for outcomes. `family_report` is the user-facing surface and
    must not leak provider ids into it.
    """
    report = fabric.family_report()
    assert {r["family"] for r in report} <= set(fabric.FAMILIES)
    flattened = str(report)
    assert "dummy" not in flattened


def test_provider_report_is_the_diagnostic_surface_and_does_name_brands():
    report = fabric.report()
    assert any(row["provider"] == "dummy" for row in report)
    assert all("license_mode" in row and "commit" in row for row in report)


def test_dormant_providers_read_as_ready_to_the_user():
    """
    REGISTERED means "nobody has needed it yet", which is the design, not a
    degradation. The user hears READY.
    """
    assert fabric.state("dummy") == fabric.REGISTERED
    row = next(r for r in fabric.family_report() if r["family"] == "diagnostic")
    assert row["state"] == fabric.READY


def test_process_singleton_reports_data_rather_than_guessing():
    result = fabric.processes()
    assert set(result) >= {"supported", "providers", "duplicates"}
    assert result["duplicates"] == []
