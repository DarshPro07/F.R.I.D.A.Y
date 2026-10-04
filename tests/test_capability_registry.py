"""
The native capability record carries the registry fields, and "installed" is
never reported as "healthy" without a probe that said so.

The failure this guards against is a health column that only ever reads green:
a capability that is declared, imports, and is broken must say so, and one
nobody has probed must say that instead of borrowing the word "ready".
"""
from __future__ import annotations
from dataclasses import fields
import json
import pytest
from friday import capabilities as C
from friday import fabric

TOOL = 'get_world_news'


@pytest.fixture(autouse=True)
def _no_probes_leak():
    yield
    C._PROBES.clear()


def test_an_installed_but_failing_capability_reports_unhealthy():
    def failing():
        raise ConnectionError("feed host refused the connection")

    C.register_probe(TOOL, failing)
    report = C.health(TOOL)

    assert report["healthy"] is False
    assert report["state"] == C.UNAVAILABLE
    assert "feed host refused the connection" in report["detail"], \
        "a failure that drops its reason is a symptom with no cause"


def test_a_probe_answering_false_is_unhealthy():
    C.register_probe(TOOL, lambda: False)
    assert C.health(TOOL)["state"] == C.UNAVAILABLE
    assert C.health(TOOL)["healthy"] is False


def test_installed_without_a_probe_is_never_healthy():
    assert TOOL in C.CAPABILITIES
    report = C.health(TOOL)

    assert report["state"] == C.REGISTERED
    assert report["healthy"] is False
    assert "no health probe" in report["detail"]


def test_a_probe_that_agrees_is_the_only_route_to_ready():
    C.register_probe(TOOL, lambda: True)
    assert C.health(TOOL)["state"] == C.READY
    assert C.health(TOOL)["healthy"] is True


def test_a_degraded_probe_is_reported_but_not_called_healthy():
    C.register_probe(TOOL, lambda: {"state": C.DEGRADED,
                                    "detail": "one feed of three is down"})
    report = C.health(TOOL)

    assert report["state"] == C.DEGRADED
    assert report["healthy"] is False
    assert report["detail"] == "one feed of three is down"


def test_a_probe_cannot_claim_registered_or_an_invented_state():
    for state in (C.REGISTERED, "FINE"):
        C.register_probe(TOOL, lambda state=state: {"state": state})
        with pytest.raises(ValueError, match="unknown state"):
            C.health(TOOL)


def test_a_probe_for_an_undeclared_capability_is_refused():
    with pytest.raises(KeyError):
        C.register_probe("no_such_tool", lambda: True)
    with pytest.raises(KeyError):
        C.health("no_such_tool")


def test_unregistering_a_probe_returns_to_registered():
    C.register_probe(TOOL, lambda: True)
    C.unregister_probe(TOOL)
    assert C.health(TOOL)["state"] == C.REGISTERED


def test_the_native_vocabulary_is_the_fabrics():
    assert set(C.HEALTH_STATES) <= set(fabric.STATES)
    assert set(C.COST_CLASSES) == set(fabric.COST_ORDER)


def test_the_record_declares_every_registry_field():
    declared = {f.name for f in fields(C.Capability)}
    wanted = {"id", "version", "backend", "provenance", "permissions",
              "requires_auth", "risk", "data_classes", "os_support",
              "latency_class", "cost_class", "tests", "last_verified"}
    assert wanted <= declared, sorted(wanted - declared)


def test_defaults_claim_nothing_and_the_manifest_stays_json():
    cap = C.get(TOOL)
    assert (cap.version, cap.last_verified) == ("", "")
    assert cap.backend == "native" and cap.provenance == "first_party"
    assert cap.permissions == cap.data_classes == cap.tests == ()
    assert json.dumps(C.as_dicts()), "the manifest must serialise"


def test_a_fully_declared_record_round_trips():
    cap = C.Capability(
        id="x_probe", description="d", execution_scope="network",
        side_effect="read", version="1.2.0", backend="fabric:dummy",
        provenance="third_party", permissions=("net.read",),
        data_classes=("public",), os_support=("windows", "linux"),
        latency_class="fast", cost_class="cheap",
        tests=("tests/test_fabric.py::test_x",), last_verified="2026-10-04")
    assert cap.backend == "fabric:dummy"
    assert cap.os_support == ("windows", "linux")


@pytest.mark.parametrize("bad", [
    {"backend": ""},
    {"provenance": "found_on_a_forum"},
    {"data_classes": ("secrets",)},
    {"os_support": ("beos",)},
    {"latency_class": "eventually"},
    {"cost_class": "priceless"},
    {"last_verified": "yesterday"},
])
def test_a_malformed_registry_field_is_refused_at_import(bad):
    with pytest.raises(ValueError, match="x_probe"):
        C.Capability(id="x_probe", description="d",
                     execution_scope="network", side_effect="read", **bad)
