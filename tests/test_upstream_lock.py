"""
The upstream lock as an audit record.

NON_NEGOTIABLE 6 - every upstream is untrusted until audited and pinned - is
enforced for *registered* providers by `fabric.Provider.__post_init__`. That
only covers upstreams someone has already written an adapter for. These tests
cover the other end: everything the operator asked for is accounted for,
pinned to something immutable, and carries a licence decision, whether or not
an adapter exists yet.

The distinction matters because the failure mode is silence. A repository that
was requested and never audited does not raise anything; it simply is not in
the lock, and the gap is invisible until someone integrates it on an
assumption.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from friday import fabric

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "third_party" / "UPSTREAM_LOCK.json"
NEW_SET_PATH = ROOT / "docs" / "integrations" / "NEW_UPSTREAM_SET.json"

#: A pin has to be a full object name. A branch or a tag moves, and an audit
#: against something that moves is an audit of nothing.
IMMUTABLE = re.compile(r"^[0-9a-f]{40}$")

MOVING = {"main", "master", "latest", "HEAD", "trunk", "develop", ""}


@pytest.fixture(scope="module")
def lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def new_set() -> dict:
    return json.loads(NEW_SET_PATH.read_text(encoding="utf-8"))


# --- coverage --------------------------------------------------------------


def test_every_requested_upstream_is_in_the_lock(lock):
    """29 requested, 29 accounted for. The set difference is the test."""
    from scripts.new_upstream_set import REQUESTED, slug

    missing = sorted({slug(url) for url in REQUESTED} - set(lock))
    assert not missing, f"requested but absent from the lock: {missing}"


def test_the_new_set_is_exactly_the_requested_minus_the_build_pack():
    """
    Computed, not recalled. If a normalisation alias breaks, this fails with a
    count rather than quietly dropping an upstream from the audit.
    """
    from scripts.new_upstream_set import EXPECTED_NEW, difference

    new, _, _ = difference()
    assert len(new) == EXPECTED_NEW


def test_no_upstream_was_staged_without_being_requested(lock):
    """A clone nobody asked for is an unreviewed dependency."""
    from scripts.new_upstream_set import REQUESTED, difference, slug

    _, _, unrequested = difference()
    assert not unrequested, f"staged but never requested: {sorted(unrequested)}"
    assert set(lock) <= {slug(url) for url in REQUESTED}


# --- pins ------------------------------------------------------------------


def test_every_locked_upstream_is_pinned_to_an_immutable_commit(lock):
    unpinned = sorted(name for name, row in lock.items()
                      if not IMMUTABLE.match(row.get("commit", "")))
    assert not unpinned, f"not pinned to a full SHA: {unpinned}"


def test_no_lock_entry_points_at_a_moving_ref(lock):
    moving = sorted(name for name, row in lock.items()
                    if row.get("commit", "") in MOVING)
    assert not moving, f"pinned to a moving ref: {moving}"


def test_a_registered_provider_pin_matches_the_lock(lock):
    """
    The descriptor and the lock are two records of one fact. When they drift,
    the adapter is auditing a commit that is not the one on disk.
    """
    for provider in fabric.registry().values():
        if not provider.upstream or provider.integration_mode == fabric.REFERENCE_ONLY:
            continue
        assert provider.upstream in lock, (
            f"{provider.id} names upstream {provider.upstream!r}, "
            f"which is not in the lock")
        assert provider.commit == lock[provider.upstream]["commit"], (
            f"{provider.id} pinned to {provider.commit[:12]} but the lock "
            f"says {lock[provider.upstream]['commit'][:12]}")


# --- licence decisions -----------------------------------------------------


def test_every_locked_upstream_has_a_verified_licence(lock):
    missing = sorted(name for name, row in lock.items()
                     if not (row.get("license_verified") or row.get("license")))
    assert not missing, f"no licence recorded: {missing}"


def test_every_new_upstream_carries_a_decision_and_a_reason(new_set):
    for name, row in new_set.items():
        assert row["proposed_mode"], f"{name} has no proposed mode"
        assert len(row["reason"]) > 40, f"{name}: reason is not a reason"


def test_no_new_upstream_is_marked_implemented(new_set):
    """This phase is reconnaissance. Nothing here has an adapter yet."""
    for name, row in new_set.items():
        assert row["status"] == "AUDITED_NOT_INTEGRATED", name


def test_copyleft_is_never_proposed_in_an_importing_mode(new_set):
    """
    The same rule `fabric.Provider` enforces, applied one step earlier - at
    the proposal, before anyone writes the adapter that would raise.
    """
    importing = {"ADAPTER", "BUILTIN", "DIRECT_LIBRARY"}
    for name, row in new_set.items():
        if row["license"] in ("AGPL-3.0", "GPL-3.0"):
            assert row["proposed_mode"] not in importing, (
                f"{name} is {row['license']} and may not be "
                f"{row['proposed_mode']}")


def test_the_proposed_mode_is_one_the_fabric_understands(new_set):
    allowed = set(fabric.INTEGRATION_MODES) | {"REJECTED"}
    for name, row in new_set.items():
        assert row["proposed_mode"] in allowed, (
            f"{name}: {row['proposed_mode']!r} is not a fabric mode")


# --- the traps this audit exists to catch ----------------------------------


def test_restricted_subtrees_are_recorded_as_vendoring_blockers(new_set):
    """
    A repository-level licence does not cover every subtree. These two were
    found by reading the clones, and are the reason the scan exists.
    """
    scientific = new_set["scientific-agent-skills"]
    assert scientific["license"] == "MIT"
    blocked = {b.split("/")[1] for b in scientific["vendoring_blockers"]}
    assert {"docx", "pdf", "pptx", "xlsx"} <= blocked, (
        "Anthropic-licensed skills must be recorded as unvendorable")

    openwork = new_set["openwork"]
    assert any("ee/LICENSE" in b for b in openwork["vendoring_blockers"]), (
        "openwork ee/ is source-available and must be flagged")


def test_a_split_licence_root_is_not_reported_as_its_carve_out(new_set):
    """
    OpenWork's root grants MIT to everything except /ee. Labelling the whole
    repository 'enterprise' would be as wrong as ignoring the carve-out.
    """
    assert new_set["openwork"]["license"] == "SPLIT_MIT_PLUS_RESTRICTED_SUBTREE"


def test_the_anythingllm_copyleft_subtree_is_flagged_in_the_lock(lock):
    """
    The build pack's own licence policy lists AnythingLLM as MIT. The clone
    carries AGPL-3.0 under open-computer/. The generator must keep saying so.
    """
    assert "license_warning" in lock["anythingllm"]
    assert "open-computer" in lock["anythingllm"]["license_warning"]


# --- decisions that implementation revised ---------------------------------


def test_browser_use_is_reference_only_because_friday_already_has_a_browser(lock):
    """
    The template proposed ADAPTER. Reading Friday's own source contradicted it:
    toolsets/web.py already starts Playwright and browser_automate already
    drives it with a computer-use model behind sensitive_domains. Installing
    browser-use would be a second browser *and* a second reasoning loop.

    Asserted here rather than left in a commit message, because the next
    person to read the template will otherwise implement the template.
    """
    entry = lock["browser-use"]
    assert entry["integration_mode"] == "REFERENCE_ONLY"
    assert entry["integration_mode_proposed"] == "ADAPTER"
    assert "NON_NEGOTIABLE 11" in entry["revision_reason"]


def test_no_reference_only_upstream_has_a_registered_provider(lock):
    """
    REFERENCE_ONLY means read for patterns, never executed. A descriptor for
    one would make it routable, which is the opposite of the decision.
    """
    reference = {name for name, row in lock.items()
                 if row.get("integration_mode") == "REFERENCE_ONLY"}
    registered = {p.upstream for p in fabric.registry().values() if p.upstream}
    assert not (reference & registered), (
        f"REFERENCE_ONLY upstreams must not be registered: "
        f"{sorted(reference & registered)}")


def test_friday_declares_exactly_one_browser_stack():
    """
    NON_NEGOTIABLE 11. Friday's browser is Playwright, started in
    friday/toolsets/web.py. No second driver may appear in the dependency set.
    """
    import pathlib

    text = (pathlib.Path(__file__).resolve().parent.parent
            / "pyproject.toml").read_text(encoding="utf-8").lower()
    for driver in ("browser-use", "browser_use", "cdp-use", "selenium",
                   "puppeteer", "helium"):
        assert driver not in text, f"a second browser driver was declared: {driver}"


def test_an_implemented_upstream_reports_its_descriptors_mode(lock):
    """
    Where an adapter exists, the descriptor is the truth and the lock is
    derived from it. The template recorded an intent in its own vocabulary
    ("CORE_WEB_ADAPTER"); leaving that beside a descriptor that says ADAPTER
    is two records of one fact, drifting.
    """
    for provider in fabric.registry().values():
        if not provider.upstream:
            continue
        entry = lock[provider.upstream]
        assert entry["integration_mode"] == provider.integration_mode, (
            f"{provider.upstream}: lock says {entry['integration_mode']}, "
            f"descriptor says {provider.integration_mode}")
        assert entry.get("provider_id") == provider.id


#: The decision vocabulary. Wider than `fabric.INTEGRATION_MODES` on purpose:
#: the lock records every upstream, and not every integration is a fabric
#: provider. Cline is an entry in `executor_router.KNOWN`, which is a
#: different registry with a different lifecycle, so OPTIONAL_WORKER is a real
#: outcome that no fabric mode describes.
DECISIONS = set(fabric.INTEGRATION_MODES) | {
    "REJECTED", "OPTIONAL_WORKER", "DIRECT_LIBRARY"}


def test_every_revised_decision_states_its_evidence(lock):
    for name, row in lock.items():
        if "revision_reason" not in row:
            continue
        assert len(row["revision_reason"]) > 120, (
            f"{name}: a reversal of the build pack needs more than a sentence")
        assert row["integration_mode"] in DECISIONS, (
            f"{name}: {row['integration_mode']!r} is not a decision")


def test_a_non_fabric_decision_has_no_fabric_provider(lock):
    """
    OPTIONAL_WORKER means it lives in executor_router, not the fabric. If one
    ever gained a fabric descriptor too, it would be registered in two places
    with two lifecycles and two health stories.
    """
    registered = {p.upstream for p in fabric.registry().values() if p.upstream}
    for name, row in lock.items():
        if row.get("integration_mode") == "OPTIONAL_WORKER":
            assert name not in registered, (
                f"{name} is an executor and must not also be a fabric provider")


# --- Slice 3: coding backends ----------------------------------------------


def test_openhands_is_reference_only_because_it_is_a_control_layer(lock):
    """
    The brief describes an SDK, a CLI and an agent-server. At the pinned
    commit the repository is an Electron app that calls itself a developer
    control center and drives other agents. Friday is the control layer
    (NON_NEGOTIABLE 1), so this is not a worker to hand tasks to.
    """
    entry = lock["openhands"]
    assert entry["integration_mode"] == "REFERENCE_ONLY"
    assert "agent-canvas" in entry["revision_reason"]


def test_openhands_is_not_declared_as_an_executor():
    from friday import executor_router

    assert "openhands" not in executor_router.BY_ID


def test_cline_is_declared_for_discovery_but_has_no_builder():
    """
    The house rule this codebase already applies to opencode and codex:
    declare it so `discover()` can report it, and refuse to ship a builder
    for something nobody here can run.
    """
    from friday import executor_router

    cline = executor_router.BY_ID["cline"]
    assert cline.buildable is False
    assert "not installed" in cline.notes
    assert "cline" in executor_router.discover()["not_installed"]


def test_hermes_is_the_engine_not_an_option():
    """
    NON_NEGOTIABLE 2 - Hermes is mandatory for serious agentic execution.
    Revised 2026-09-02: the development pipeline used to reach ONLY Claude
    Code (DEFAULT was "claude"), which made the mandatory engine the one
    executor that never got the work. Hermes is now the router's DEFAULT and
    is reached through hermes_bridge (executors/hermes.py is a thin shim over
    HermesSupervisor.delegate - the same bridge, the same WorkRun ledger, the
    same memory bundle). Claude Code is the declared FALLBACK for a machine
    where Hermes is absent, and every other executor is optional.
    """
    from friday import executor_router
    from friday.executors import hermes as hx

    assert executor_router.DEFAULT == "hermes"
    assert executor_router.FALLBACK == "claude"
    assert executor_router.BY_ID["hermes"].locator == "friday.executors.hermes:_hermes_python"
    # The shim delegates through the bridge, never a second gateway.
    import inspect
    src = inspect.getsource(hx.HermesExecutor.execute)
    assert "sup.delegate(" in src and "session.create" not in src


def test_declaring_a_new_executor_does_not_change_who_gets_the_work():
    """
    Adding Cline to KNOWN must not make it chosen. Novelty is not evidence;
    choose() prefers a measured winner and otherwise the proven default.
    """
    from friday import executor_router

    choice = executor_router.choose("implement a feature")
    assert choice.executor != "cline"
