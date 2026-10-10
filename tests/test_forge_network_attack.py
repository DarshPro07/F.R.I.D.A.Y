"""
BIN-004: a network-attack candidate (scanner, exploit, command-and-control
style) is EXCLUDED by the forge on submission. It leaves that state only with an
unexpired authorization and the owner's confirmation, and then enters the
ordinary lifecycle as a fresh candidate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from friday import forge as F
from friday import trust as T
from friday.store import Store

GOOD = "def run(ctx, text):\n    return {'words': len(text.split())}\n"
FUTURE = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
PAST = "2000-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def store(tmp_path):
    fresh = Store(tmp_path / "t.db")
    F.reset_store(fresh)
    yield fresh
    F.reset_store(None)
    fresh.close()


@pytest.fixture
def registry(tmp_path):
    return F.Registry(tmp_path / "skills")


def spec(name="word_count", goal="count words", **over):
    return F.CapabilitySpec.from_dict({
        "name": name, "goal": goal, "inputs": {"text": "string"},
        "outputs": {"words": "integer"},
        "verification": [{"inputs": {"text": "a b"}, "expect": {"words": 2}}], **over})


def auth(expires=FUTURE, scope=("port_scanner",), actions=(F.RELEASE_ACTION,)):
    return T.SecurityAuthorization(
        owner_identity="owner", target_scope=scope,
        ownership_or_permission_basis="my own lab", allowed_actions=actions,
        expires_at=expires, approval_id="appr-9")


@pytest.mark.parametrize("name,goal", [
    ("port_scanner", "scan the open ports of a host range"),
    ("c2_client", "talk to a command-and-control server and run tasks"),
    ("shell_pop", "open a reverse shell back to the operator"),
    ("exploit_runner", "send an exploit payload to a vulnerable service"),
])
def test_attack_style_candidates_are_excluded_on_submission(registry, name, goal):
    record = registry.submit(spec(name, goal), GOOD, provenance="reverse-engineering repo")
    assert record["state"] == F.EXCLUDED
    assert F.network_attack_signals(spec(name, goal), GOOD)


def test_the_source_is_read_as_well_as_the_spec(registry):
    src = "# nmap-style syn scan helper\n" + GOOD
    assert registry.submit(spec(), src, provenance="t")["state"] == F.EXCLUDED


NEUTRAL_SCANNER = (
    "import socket\n\n"
    "def run(ctx, host):\n"
    "    open_ = []\n"
    "    for p in range(1, 1025):\n"
    "        s = socket.socket()\n"
    "        if s.connect_ex((host, p)) == 0:\n"
    "            open_.append(p)\n"
    "    return {'words': len(open_)}\n")
SCAPY_PROBE = (
    "from scapy.all import IP, TCP, sr1\n\n"
    "def run(ctx, host):\n"
    "    r = sr1(IP(dst=host) / TCP(dport=80, flags='S'), timeout=1)\n"
    "    return {'words': 1 if r else 0}\n")
RAW_SOCKET = ("import socket\n\ndef run(ctx, host):\n"
              "    s = socket.socket(socket.AF_INET, socket.SOCK_RAW, 6)\n"
              "    return {'words': 1}\n")
CONNECT_PER_HOST = (
    "import socket\n\ndef run(ctx, hosts):\n"
    "    ok = [h for h in hosts if socket.create_connection((h, 22), timeout=1)]\n"
    "    return {'words': len(ok)}\n")


@pytest.mark.parametrize("name,goal,source", [
    ("net_probe", "check which services answer", NEUTRAL_SCANNER),
    ("net_probe", "send a probe packet", SCAPY_PROBE),
    ("net_probe", "open a raw socket", RAW_SOCKET),
    ("host_check", "see which hosts answer", CONNECT_PER_HOST),
    ("beacon_client", "beacon to a C&C server", GOOD),
    ("beacon_client", "beacon to a c2 server for tasks", GOOD),
    ("exploit_helper", "exploits vulnerabilities in a service", GOOD),
])
def test_attack_tools_are_excluded_by_their_structure_not_only_their_words(
        registry, name, goal, source):
    record = registry.submit(spec(name, goal), source, provenance="t")
    assert record["state"] == F.EXCLUDED, F.network_attack_signals(spec(name, goal), source)


def test_a_wide_scoped_network_spec_is_excluded(registry):
    wide = spec("net_probe", "check things", network="scoped",
                allowed_hosts=tuple(f"h{i}.example" for i in range(20)))
    assert registry.submit(wide, GOOD, provenance="t")["state"] == F.EXCLUDED
    cidr = spec("net_probe2", "check things", network="scoped", allowed_hosts=("10.0.0.0/8",))
    assert registry.submit(cidr, GOOD, provenance="t")["state"] == F.EXCLUDED


def test_a_narrow_scoped_network_skill_with_one_fixed_connection_is_not_excluded(registry):
    one = ("import socket\n\ndef run(ctx, text):\n"
           "    s = socket.create_connection(('api.example', 443), timeout=2)\n"
           "    return {'words': len(text.split())}\n")
    narrow = spec("fetcher", "fetch a document", network="scoped",
                  allowed_hosts=("api.example",))
    assert registry.submit(narrow, one, provenance="t")["state"] == F.CANDIDATE


@pytest.mark.parametrize("goal", [
    "mix the c2 and c3 colour channels",
    "the cache exploits locality to count words",
    "exploits the locality of reference",
])
def test_ordinary_text_that_shares_a_word_stays_a_candidate(registry, goal):
    assert registry.submit(spec("word_count", goal), GOOD, provenance="t")["state"] == F.CANDIDATE


def test_an_ordinary_skill_is_still_a_candidate(registry):
    assert registry.submit(spec(), GOOD, provenance="t")["state"] == F.CANDIDATE


def test_excluded_skills_are_left_out_of_the_default_listing(registry):
    registry.submit(spec(), GOOD, provenance="t")
    registry.submit(spec("port_scanner", "port scan a host"), GOOD, provenance="t")
    assert [r["name"] for r in registry.list()] == ["word_count"]
    assert [r["name"] for r in registry.list(F.EXCLUDED)] == ["port_scanner"]


def test_an_excluded_skill_cannot_be_scanned_verified_or_enabled(registry):
    registry.submit(spec("port_scanner", "port scan a host"), GOOD, provenance="t")
    for step in (lambda: registry.scan("port_scanner"),
                 lambda: registry.verify("port_scanner"),
                 lambda: registry.register("port_scanner", actor="owner"),
                 lambda: registry.enable("port_scanner", ("scope",), actor="owner")):
        with pytest.raises(F.ForgeError):
            step()
    assert registry.get("port_scanner")["state"] == F.EXCLUDED


@pytest.mark.parametrize("which,confirmed,fragment", [
    (None, True, "authorized scope"),
    ("expired", True, "authorized scope"),
    ("valid", False, "confirmation"),
])
def test_release_needs_an_unexpired_authorization_and_owner_confirmation(
        registry, which, confirmed, fragment):
    registry.submit(spec("port_scanner", "port scan a host"), GOOD, provenance="t")
    given = {"expired": auth(PAST), "valid": auth()}.get(which)
    with pytest.raises(F.ForgeError, match=fragment):
        registry.release("port_scanner", actor="owner", authorization=given,
                         owner_confirmed=confirmed)
    assert registry.get("port_scanner")["state"] == F.EXCLUDED


@pytest.mark.parametrize("given", [
    lambda: auth(scope=("lab.example",)),             # names a different target
    lambda: auth(actions=("scan",)),                  # does not allow releasing
    lambda: auth(scope=("other_skill",)),
])
def test_an_authorization_that_does_not_name_the_skill_cannot_release_it(registry, given):
    registry.submit(spec("port_scanner", "port scan a host"), GOOD, provenance="t")
    with pytest.raises(F.ForgeError, match="authorized scope"):
        registry.release("port_scanner", actor="owner", authorization=given(),
                         owner_confirmed=True)
    assert registry.get("port_scanner")["state"] == F.EXCLUDED


def test_forge_cannot_release_what_it_excluded(registry):
    registry.submit(spec("port_scanner", "port scan a host"), GOOD, provenance="t")
    with pytest.raises(F.ForgeError, match="forge"):
        registry.release("port_scanner", actor="forge", authorization=auth(),
                         owner_confirmed=True)


def test_a_released_skill_is_a_candidate_and_the_release_is_recorded(registry):
    registry.submit(spec("port_scanner", "port scan a host"), GOOD, provenance="t")
    record = registry.release("port_scanner", actor="owner", authorization=auth(),
                              owner_confirmed=True)
    assert record["state"] == F.CANDIDATE
    last = registry.history("port_scanner")[-1]
    assert (last["from_state"], last["to_state"], last["actor"]) == (
        F.EXCLUDED, F.CANDIDATE, "owner")
    assert "appr-9" in last["reason"]


def test_releasing_a_skill_that_is_not_excluded_is_refused(registry):
    registry.submit(spec(), GOOD, provenance="t")
    with pytest.raises(F.ForgeError):
        registry.release("word_count", actor="owner", authorization=auth(),
                         owner_confirmed=True)
