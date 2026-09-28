"""
SEC-005 / NET-001 (PRD v7 sections 43-44): the execution envelope and the
network scope, as shared contracts.

An envelope is the deterministic scope one run works inside, fixed by
FRIDAY's policy before the run starts and held outside model context. The
property everything else leans on is that it only ever gets narrower: "a
worker cannot expand its own envelope". These tests pin the contract; the
enforcement tests live with each service that enforces it.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from friday import contracts as c

ROOT = Path(__file__).resolve().parent.parent
LATER = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
SOONER = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
PAST = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")


def _envelope(**overrides) -> c.Envelope:
    base = dict(
        run_id="RUN-1", objective_id="OBJ-1",
        cwd=str(ROOT / "work"),
        read_roots=(str(ROOT / "work"), str(ROOT / "docs")),
        write_roots=(str(ROOT / "work"),),
        network=c.NetworkScope(mode=c.NET_SELECTED_HOSTS,
                               hosts=("api.example.com", ".pypi.org"),
                               objective_id="OBJ-1", expires_at=LATER),
        secrets=("GITHUB_TOKEN", "PYPI_TOKEN"),
        allowed_capabilities=("files_read", "files_write", "web_fetch"),
        blocked_capabilities=("power_shutdown",),
        limits=c.Limits(memory_mb=2048, processes=16, seconds=600.0),
        token_limit=50_000, tool_call_limit=40, expires_at=LATER)
    base.update(overrides)
    return c.Envelope(**base)


# --- the network scope ----------------------------------------------------

def test_a_scope_with_an_unknown_mode_is_refused():
    with pytest.raises(c.ContractError):
        c.NetworkScope(mode="EVERYTHING")


def test_a_mode_that_names_hosts_needs_one():
    with pytest.raises(c.ContractError):
        c.NetworkScope(mode=c.NET_SELECTED_HOSTS)


@pytest.mark.parametrize("host,allowed", [
    ("api.example.com", True),
    ("pypi.org", True),                  # ".pypi.org" covers the domain itself
    ("files.pypi.org", True),            # and everything under it
    ("example.com", False),              # a parent is not the host
    ("evil-pypi.org", False),            # a suffix without the dot is not under it
    ("api.example.com.evil.test", False),
    ("", False),
])
def test_selected_hosts_allow_exactly_what_they_name(host, allowed):
    scope = c.NetworkScope(mode=c.NET_SELECTED_HOSTS,
                           hosts=("api.example.com", ".pypi.org"), expires_at=LATER)
    assert scope.allows(host) is allowed


def test_no_network_allows_nothing_and_public_read_allows_any_host():
    assert not c.NetworkScope().allows("example.com")
    public = c.NetworkScope(mode=c.NET_PUBLIC_WEB_READ)
    assert public.allows("example.com")


def test_an_expired_scope_allows_nothing():
    scope = c.NetworkScope(mode=c.NET_PUBLIC_WEB_READ, expires_at=PAST)
    assert scope.expired()
    assert not scope.allows("example.com")


# --- the envelope ---------------------------------------------------------

def test_an_envelope_belongs_to_one_run_and_a_known_provenance():
    with pytest.raises(c.ContractError):
        _envelope(run_id="")
    with pytest.raises(c.ContractError):
        _envelope(provenance="SOMEONE_SAID_SO")


def test_a_block_wins_over_an_allow():
    env = _envelope(allowed_capabilities=(c.ANY_CAPABILITY,),
                    blocked_capabilities=("power_shutdown",))
    assert env.permits("files_read") == ""
    assert "blocked" in env.permits("power_shutdown")


def test_an_empty_allowlist_allows_nothing():
    env = _envelope(allowed_capabilities=())
    assert "outside" in env.permits("files_read")


def test_an_envelope_past_its_expiry_is_expired():
    assert _envelope(expires_at=PAST).expired()
    assert not _envelope().expired()


def test_an_envelope_survives_the_store_round_trip():
    env = _envelope()
    assert c.Envelope.from_dict(env.to_dict()) == env


# --- narrow only ----------------------------------------------------------

def test_narrowing_can_tighten_every_field():
    env = _envelope()
    child = env.narrow(
        run_id="RUN-1.a",
        cwd=str(ROOT / "work" / "pkg"),
        read_roots=(str(ROOT / "work" / "pkg"),),
        write_roots=(str(ROOT / "work" / "pkg"),),
        network=c.NetworkScope(mode=c.NET_SELECTED_HOSTS, hosts=("files.pypi.org",),
                               objective_id="OBJ-1", expires_at=SOONER),
        secrets=("PYPI_TOKEN",),
        allowed_capabilities=("files_read",),
        blocked_capabilities=("power_shutdown", "web_fetch"),
        limits=c.Limits(memory_mb=1024, processes=8, seconds=300.0),
        token_limit=10_000, tool_call_limit=10, expires_at=SOONER,
        provenance=c.READ_MATERIAL, attended=False)
    assert child.run_id == "RUN-1.a" and child.objective_id == env.objective_id
    assert child.provenance == c.READ_MATERIAL and child.attended is False


@pytest.mark.parametrize("wider", [
    {"write_roots": (str(ROOT / "docs"),)},             # read-only root made writable
    {"read_roots": (str(ROOT),)},                       # a parent of a root
    {"cwd": str(ROOT)},                                 # cwd outside every root
    {"allowed_capabilities": ("files_read", "shell_run")},
    {"allowed_capabilities": (c.ANY_CAPABILITY,)},
    {"blocked_capabilities": ()},                       # a block removed
    {"secrets": ("GITHUB_TOKEN", "AWS_SECRET")},
    {"limits": c.Limits(memory_mb=8192)},
    {"token_limit": 0},                                 # 0 means unlimited
    {"tool_call_limit": 400},
    {"expires_at": ""},                                 # no expiry is later than any
    {"network": c.NetworkScope(mode=c.NET_PUBLIC_WEB_READ, expires_at=SOONER)},
    {"network": c.NetworkScope(mode=c.NET_SELECTED_HOSTS, hosts=("example.com",),
                               expires_at=SOONER)},
    {"objective_id": "OBJ-2"},
])
def test_nothing_can_be_widened(wider):
    with pytest.raises(c.ContractError, match="wider"):
        _envelope().narrow(**wider)


def test_trust_and_attendance_only_move_one_way():
    read = _envelope(provenance=c.READ_MATERIAL, attended=False)
    with pytest.raises(c.ContractError, match="wider"):
        read.narrow(provenance=c.PERSON)
    with pytest.raises(c.ContractError, match="wider"):
        read.narrow(attended=True)


def test_an_unknown_field_is_not_quietly_ignored():
    with pytest.raises(c.ContractError):
        _envelope().narrow(allowed_everything=True)


def test_there_is_no_way_to_widen():
    assert not [name for name in dir(c.Envelope)
                if name.startswith(("widen", "expand", "grant", "extend"))]


# --- one substrate, protected ---------------------------------------------

def test_execution_limits_are_the_contract():
    from friday import execution
    assert execution.Limits is c.Limits


@pytest.mark.parametrize("name", ["Limits", "Egress", "JobObject"])
def test_each_enforcement_type_is_defined_once(name):
    """`friday/sandbox.py` was a full copy of `friday/execution.py`; two
    boundaries drift, and the one nobody uses is the one that stays safe."""
    homes = []
    for path in sorted((ROOT / "friday").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        homes += [path.relative_to(ROOT).as_posix() for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == name]
    assert len(homes) == 1, homes


@pytest.mark.parametrize("path", ["friday/execution.py", "friday/contracts.py"])
def test_the_boundary_a_run_meets_is_kernel(path):
    """Self-development may not rewrite the boundary it is judged by."""
    from friday import self_upgrade as SU
    assert SU.is_kernel_path(path), path
