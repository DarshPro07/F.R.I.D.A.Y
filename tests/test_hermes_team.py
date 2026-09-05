"""
`friday/hermes_team.py` against a fake `hermes` CLI (`fake_hermes_cli.py`):
records argv, answers canned JSON. No real Hermes process involved.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from friday import hermes_team as ht


@pytest.fixture(autouse=True)
def _fake_cli(tmp_path, monkeypatch):
    log_path = tmp_path / "log.jsonl"
    answers_path = tmp_path / "answers.json"
    answers_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HERMES_EXE", sys.executable)
    fake_cli = os.path.join(os.path.dirname(__file__), "fake_hermes_cli.py")
    monkeypatch.setenv("HERMES_EXE_PREFIX", fake_cli)
    monkeypatch.setenv("FAKE_HERMES_LOG", str(log_path))
    monkeypatch.setenv("FAKE_HERMES_ANSWERS", str(answers_path))
    ht.reset_state()

    def set_answers(mapping):
        answers_path.write_text(json.dumps(mapping), encoding="utf-8")

    def calls():
        if not log_path.exists():
            return []
        return [json.loads(line) for line in
                log_path.read_text(encoding="utf-8").splitlines() if line]

    yield type("Ctx", (), {"set_answers": staticmethod(set_answers),
                           "calls": staticmethod(calls)})
    ht.reset_state()


def test_plan_team_small_goal_is_empty():
    assert ht.plan_team("fix a typo in the readme") == ()


def test_plan_team_orders_by_profile_not_role_order():
    team = ht.plan_team(
        "design the architecture, implement it, write tests and review it",
        files=6)
    assert team == tuple(p for p in ht.PROFILES if p in team)
    assert len(team) >= 2


def test_ensure_profile_idempotent(_fake_cli):
    _fake_cli.set_answers({"profile list":
                           {"profiles": [{"name": "friday"},
                                         {"name": "friday-engineering"}]}})
    assert ht.ensure_profile("friday-engineering") is True
    calls = _fake_cli.calls()
    assert all(c[:2] != ["profile", "create"] for c in calls)


def test_ensure_profile_creates_when_missing(_fake_cli):
    _fake_cli.set_answers({"profile list": {"profiles": [{"name": "friday"}]},
                           "profile create": {}})
    assert ht.ensure_profile("friday-qa") is True
    creates = [c for c in _fake_cli.calls() if c[:2] == ["profile", "create"]]
    assert creates and creates[0][2] == "friday-qa"
    assert "--clone-from" in creates[0] and "friday" in creates[0]


def test_submit_creates_linked_tasks_with_idempotency_and_model(_fake_cli):
    _fake_cli.set_answers({
        "profile list": {"profiles": [{"name": "friday"},
                                      {"name": "friday-engineering"},
                                      {"name": "friday-qa"}]},
        "kanban init": {},
        "kanban create": {"id": "T-VARIES"},
        "kanban link": {},
    })
    team = ("friday-engineering", "friday-qa")
    ref = ht.submit("OBJ-1", "build and test the thing", "BUNDLE TEXT",
                    team, {"friday-engineering": ("claude-opus", "anthropic"),
                           "friday-qa": ("", "")})
    assert "error" not in ref
    assert set(ref["tasks"]) == set(team)
    assert ref["order"] == list(team)

    creates = [c for c in _fake_cli.calls() if c[:2] == ["kanban", "create"]]
    assert len(creates) == 2
    eng = next(c for c in creates if "friday-engineering" in c)
    assert "OBJ-1:friday-engineering" in eng
    assert "claude-opus" in eng and "anthropic" in eng
    links = [c for c in _fake_cli.calls() if c[:2] == ["kanban", "link"]]
    assert len(links) == 1


def test_submit_refuses_kanban_worker_origin(_fake_cli):
    ref = ht.submit("OBJ-2", "goal", f"body {ht.CYCLE_MARKER}",
                    ("friday-engineering", "friday-qa"), {})
    assert "error" in ref
    assert _fake_cli.calls() == []


def test_submit_falls_back_on_kanban_error(_fake_cli):
    _fake_cli.set_answers({
        "profile list": {"profiles": [{"name": "friday"},
                                      {"name": "friday-engineering"},
                                      {"name": "friday-qa"}]},
        "kanban init": {"__exit__": 1, "__stderr__": "kanban unavailable"},
    })
    ref = ht.submit("OBJ-3", "goal", "body",
                    ("friday-engineering", "friday-qa"), {})
    assert "error" in ref


def test_poll_parses_show_json(_fake_cli):
    _fake_cli.set_answers({"kanban show": {"status": "done",
                                           "result": "wrote the file"}})
    board_ref = {"tasks": {"friday-engineering": "T-1", "friday-qa": "T-2"}}
    status = ht.poll(board_ref)
    assert status["friday-engineering"] == {"status": "done",
                                            "result": "wrote the file"}
    assert status["friday-qa"] == status["friday-engineering"]


def test_gateway_cap_stops_lru(_fake_cli, monkeypatch):
    started, stopped = [], []

    class FakeSup:
        def __init__(self, *, profile, cwd):
            self.profile = profile
        def start(self):
            started.append(self.profile)
        def stop(self):
            stopped.append(self.profile)

    monkeypatch.setattr(ht.hb, "HermesSupervisor", FakeSup)
    monkeypatch.setattr(ht.hb, "profile_home", lambda p: "")
    ht.gateway_for("friday-research")
    ht.gateway_for("friday-engineering")
    ht.gateway_for("friday-qa")
    assert started == ["friday-research", "friday-engineering", "friday-qa"]
    assert stopped == ["friday-research"]
