"""
The Agent Capability SBOM (PRD v7 SUPPLY-001).

third_party/UPSTREAM_LOCK.json covers the upstream repositories and the model
weights. The rest of what an agent runs on - Python packages, MCP servers, CLIs
- was recorded nowhere. `friday.sbom` joins them into one list in which every
component carries a source, a version and a scan state, and where the state is
"we have not looked" it says so rather than leaving the field out.
"""
from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

from friday import sbom

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "third_party" / "UPSTREAM_LOCK.json"

SECRET = "sk-should-never-appear"


def _by_name(document: dict) -> dict:
    return {(c["kind"], c["name"]): c for c in document["components"]}


def _build(**given) -> dict:
    inputs = dict(lock={}, packages=[], pins={}, hermes={}, providers=[])
    inputs.update(given)
    return sbom.build(**inputs)


def test_upstreams_and_models_come_from_the_lock_with_their_audit_state():
    lock = {
        "audited": {"url": "https://example.com/a", "commit": "a" * 40, "version": "v1.2",
                    "license_verified": "MIT", "status": "CLONED"},
        "missing": {"url": "https://example.com/m", "commit": "", "status": "NOT_CLONED"},
        "mixed": {"url": "https://example.com/x", "commit": "b" * 40, "version": "v3",
                  "license_verified": "MIT", "status": "CLONED",
                  "license_warning": "nested GPL"},
        "org/weights": {"url": "https://huggingface.co/org/weights", "commit": "c" * 40,
                        "version": "c" * 12, "license_verified": "apache-2.0",
                        "status": "MODEL_PINNED"},
    }
    found = _by_name(_build(lock=lock))
    assert found[("upstream", "audited")]["scan_state"] == sbom.AUDITED
    assert found[("upstream", "audited")]["source"] == "https://example.com/a"
    assert found[("upstream", "audited")]["version"] == "v1.2"
    assert found[("upstream", "missing")]["scan_state"] == sbom.UNSCANNED
    assert found[("upstream", "mixed")]["scan_state"] == sbom.FLAGGED
    assert found[("model", "org/weights")]["scan_state"] == sbom.AUDITED


def test_installed_packages_are_listed_and_honestly_unscanned():
    found = _by_name(_build(packages=[("httpx", "0.28.1"), ("mcp", "1.2.0")]))
    assert found[("package", "httpx")]["version"] == "0.28.1"
    assert found[("package", "httpx")]["source"] == "pypi"
    assert found[("package", "httpx")]["scan_state"] == sbom.UNSCANNED


def test_mcp_servers_take_their_scan_state_from_the_firewall_and_hide_credentials():
    pins = {
        "hermes:ok": {"state": "ACTIVE", "binding": {"endpoint": "https://ok.example.com:443"}},
        "hermes:new": {"state": "EXTERNAL_DISCOVERED", "source": "hermes",
                       "declared": {"command": ["npx", "-y", f"pkg --token {SECRET}"]}},
        "hermes:bad": {"state": "SUSPENDED", "source": "hermes"},
    }
    hermes = {"cfg-only": {"command": "uvx", "args": ["tool"], "env": {"API_KEY": SECRET}},
              "remote": {"url": f"https://user:{SECRET}@r.example.com/sse?k={SECRET}",
                         "headers": {"Authorization": f"Bearer {SECRET}"}}}
    document = _build(pins=pins, hermes=hermes)
    found = _by_name(document)
    assert found[("mcp_server", "hermes:ok")]["scan_state"] == sbom.SCANNED
    assert found[("mcp_server", "hermes:new")]["scan_state"] == sbom.UNSCANNED
    assert found[("mcp_server", "hermes:bad")]["scan_state"] == sbom.FLAGGED
    # configured in Hermes but never seen by the firewall: still listed, unscanned
    assert found[("mcp_server", "hermes:cfg-only")]["scan_state"] == sbom.UNSCANNED
    assert found[("mcp_server", "hermes:remote")]["source"] == "https://r.example.com/sse"
    assert SECRET not in json.dumps(document)


def test_clis_are_the_fabric_providers_in_cli_mode():
    providers = [
        SimpleNamespace(id="tool_a", upstream="tool-a", integration_mode="CLI",
                        commit="d" * 40, version="2.0"),
        SimpleNamespace(id="tool_b", upstream="", integration_mode="CLI",
                        commit="", version=""),
        SimpleNamespace(id="tool_c", upstream="tool-c", integration_mode="ADAPTER",
                        commit="e" * 40, version="1"),
    ]
    found = _by_name(_build(providers=providers))
    assert found[("cli", "tool_a")]["scan_state"] == sbom.AUDITED
    assert found[("cli", "tool_a")]["version"] == "2.0"
    assert found[("cli", "tool_b")]["scan_state"] == sbom.UNSCANNED
    assert ("cli", "tool_c") not in found


def test_summary_counts_every_component_by_kind_and_state():
    document = _build(packages=[("a", "1"), ("b", "2")],
                      lock={"u": {"url": "x", "commit": "f" * 40, "license_verified": "MIT",
                                  "status": "CLONED"}})
    assert document["summary"]["total"] == 3
    assert document["summary"]["by_kind"] == {"package": 2, "upstream": 1}
    assert document["summary"]["by_scan_state"] == {sbom.UNSCANNED: 2, sbom.AUDITED: 1}


def test_the_real_sbom_covers_the_lock_and_gives_every_component_its_three_fields():
    document = sbom.collect()
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    named = {c["name"] for c in document["components"] if c["kind"] in ("upstream", "model")}
    assert set(lock) <= named
    assert any(c["kind"] == "package" for c in document["components"])
    for component in document["components"]:
        assert component["source"], component
        assert component["version"], component
        assert component["scan_state"] in sbom.SCAN_STATES, component
    assert SECRET not in json.dumps(document)
