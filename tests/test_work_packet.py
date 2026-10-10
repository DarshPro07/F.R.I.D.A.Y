"""
FAB-002: the bounded work packet and the receipt every runtime returns.

A packet is the existing `TaskBundle` (goal, allowed paths, constraints,
acceptance) plus what it lacked: a budget and the acceptance tests the manager
itself runs. A receipt is what comes back whichever runtime did the work.
"""
from __future__ import annotations

import sys

import pytest

from friday.executors import claude_code as CC
from friday.executors import work_packet as wp

CHECK = (sys.executable, "-c", "print('ok')")


def _bundle(**over):
    fields = dict(goal="add a TTL cache to the uploader", workspace="ws",
                  allowed_paths=("friday/uploader.py", "tests/"),
                  constraints=("keep the public API",),
                  acceptance=("the cache expires entries",), isolate=False)
    fields.update(over)
    return CC.TaskBundle(**fields)


def _packet(**over):
    fields = dict(bundle=_bundle(), budget=wp.Budget(usd=0.0, seconds=60.0),
                  checks=(CHECK,))
    fields.update(over)
    return wp.WorkPacket(**fields)


def test_a_packet_states_its_files_constraints_acceptance_and_budget():
    packet = _packet()
    text = packet.prompt()
    assert "add a TTL cache to the uploader" in text
    for line in ("friday/uploader.py", "tests/", "keep the public API",
                 "the cache expires entries", "-c print('ok')"):
        assert line in text, f"{line!r} is missing from the prompt a runtime gets"
    assert packet.budget.usd == 0.0 and packet.budget.seconds == 60.0


@pytest.mark.parametrize("paths", [
    (), ("/etc/passwd",), ("C:\\Windows\\system32",), ("..\\secrets.txt",),
    ("friday/../../outside.py",)])
def test_a_packet_without_a_bounded_scope_is_refused(paths):
    with pytest.raises(ValueError, match="allowed_paths"):
        _packet(bundle=_bundle(allowed_paths=paths))


def test_a_packet_without_a_goal_or_a_check_is_refused():
    with pytest.raises(ValueError, match="goal"):
        _packet(bundle=_bundle(goal="  "))
    with pytest.raises(ValueError, match="check"):
        _packet(checks=())
    with pytest.raises(ValueError, match="check"):
        _packet(checks=((),))


@pytest.mark.parametrize("usd,seconds", [(-1.0, 60.0), (0.0, 0.0), (0.0, float("inf")),
                                         (float("nan"), 60.0)])
def test_a_budget_must_be_finite_and_non_negative(usd, seconds):
    with pytest.raises(ValueError, match="budget"):
        wp.Budget(usd=usd, seconds=seconds)


def test_scope_accepts_a_file_or_anything_under_a_listed_directory():
    packet = _packet()
    assert packet.in_scope("friday/uploader.py")
    assert packet.in_scope("tests/test_uploader.py")
    assert packet.in_scope("tests\\sub\\test_x.py")
    assert not packet.in_scope("friday/other.py")
    assert not packet.in_scope("tests_extra/x.py")


def test_a_receipt_serialises_every_field_the_manager_reads():
    receipt = wp.Receipt(runtime="codex", status=wp.DONE, model="gpt-5.5",
                         result="done", cost_usd=0.0,
                         usage={"input_tokens": 3}, warnings=["w"])
    assert set(receipt.to_dict()) == {
        "runtime", "status", "model", "result", "cost_usd", "usage", "warnings",
        "files_changed", "acceptance", "workspace", "commit"}
