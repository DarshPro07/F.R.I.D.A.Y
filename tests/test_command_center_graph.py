"""ACC-004 slice 1: the Command Center graph model (ui/command_center_model.js).

The truth rules live in a pure ES module; these tests drive it through node
with frames shaped exactly like ui_server._event_frame emits.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from friday import command_center as cc

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

MODEL = Path(__file__).resolve().parents[1] / "ui" / "command_center_model.js"
STATES = ("queued", "running", "verified", "blocked", "failed", "cancelled")


def _js(tmp_path, body, **inputs):
    """Run ``body`` (an async function body with ``m`` = the module and each
    input bound by name) under node and return its JSON result."""
    mod = tmp_path / "m.mjs"
    shutil.copyfile(MODEL, mod)
    binds = "".join("const %s = %s;\n" % (k, json.dumps(json.dumps(v)))
                    for k, v in inputs.items())
    binds += "".join("const %s_ = JSON.parse(%s);\n" % (k, k) for k in inputs)
    code = ("const m = await import(%s);\n%s"
            "const out = await (async () => {%s})();\n"
            "process.stdout.write(JSON.stringify(out));\n"
            % (json.dumps(mod.as_uri()), binds, body))
    done = subprocess.run([NODE, "--input-type=module", "-e", code],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _agent(run_id, state, parent="", role="worker", reason="", position=None):
    return {"run_id": run_id, "parent_run_id": parent, "role": role,
            "model": "m", "state": state, "reason": reason,
            "position": position}


def _frame(kind, payload, run_id=""):
    return {"event": kind, "data": json.dumps(
        {"v": 1, "type": kind, "run_id": run_id, "at": "t",
         "payload": payload})}


def _snap(*agents):
    return _frame("STATE_SNAPSHOT", {"agents": list(agents)})


def _delta(*ops):
    return _frame("STATE_DELTA", {"delta": list(ops)})


def _replace(a):
    return {"op": "replace", "path": "/agents/" + a["run_id"], "value": a}


# Feeds `frames_` into a fresh Map and returns graph + runningIds + map.
_FEED = """
let a = new Map();
for (const f of frames_) a = m.applyFrame(a, f);
return {graph: m.toGraph(a, !!reduced_), running: m.runningIds(a),
        agents: Object.fromEntries(a)};
"""


def _feed(tmp_path, frames, reduced=False):
    return _js(tmp_path, _FEED, frames=frames, reduced=reduced)


def _node(graph, nid):
    return next(n for n in graph["nodes"] if n["id"] == nid)


def _edge_to(graph, nid):
    return [e for e in graph["edges"] if e["target"] == nid]


def test_a_queued_agent_is_not_running_and_not_animated(tmp_path):
    out = _feed(tmp_path, [_snap(_agent("w1", "queued"))])
    assert out["running"] == []
    assert _node(out["graph"], "w1")["data"]["visual"]["animation"] == "none"
    assert [e["animated"] for e in _edge_to(out["graph"], "w1")] == [False]


def test_a_delta_to_running_pulses_and_animates_its_edge(tmp_path):
    out = _feed(tmp_path, [_snap(_agent("w1", "queued")),
                           _delta(_replace(_agent("w1", "running")))])
    assert out["running"] == ["w1"]
    assert _node(out["graph"], "w1")["data"]["visual"]["animation"] == "pulse"
    edge = _edge_to(out["graph"], "w1")
    assert [(e["source"], e["animated"]) for e in edge] == [("friday", True)]


def test_reduced_motion_stops_every_animation_but_states_stay_distinct(
        tmp_path):
    agents = [_agent("w%d" % i, s) for i, s in enumerate(STATES)]
    out = _feed(tmp_path, [_snap(*agents)], reduced=True)
    vis = [_node(out["graph"], a["run_id"])["data"]["visual"] for a in agents]
    assert {v["animation"] for v in vis} == {"none"}
    assert not any(e["animated"] for e in out["graph"]["edges"])
    assert len({v["icon"] for v in vis}) == len(STATES)
    assert [v["label"] for v in vis] == list(STATES)


def test_verified_plays_once_and_a_repeated_frame_changes_nothing(tmp_path):
    done = _delta(_replace(_agent("w1", "verified")))
    once = _feed(tmp_path, [_snap(_agent("w1", "running")), done])
    twice = _feed(tmp_path, [_snap(_agent("w1", "running")), done, done])
    assert _node(once["graph"], "w1")["data"]["visual"]["animation"] == "once"
    assert once["graph"] == twice["graph"]


def test_blocked_shows_its_reason_and_queued_its_position(tmp_path):
    out = _feed(tmp_path, [_snap(
        _agent("b", "blocked", reason="waiting for approval"),
        _agent("q", "queued", position=2))])
    b = _node(out["graph"], "b")["data"]["visual"]
    q = _node(out["graph"], "q")["data"]["visual"]
    assert (b["detail"], b["animation"]) == ("waiting for approval", "none")
    assert q["detail"] == "position 2"


def test_unknown_state_renders_as_blocked_and_never_animates(tmp_path):
    out = _feed(tmp_path, [_snap(_agent("u", "weird"))])
    v = _node(out["graph"], "u")["data"]["visual"]
    assert (v["icon"], v["animation"], v["detail"], v["label"]) == (
        "stop", "none", "unknown state", "weird")


def test_remove_bad_frames_and_odd_ids(tmp_path):
    odd = "run:1 with spaces"
    frames = [_snap(_agent("w1", "queued"), _agent(odd, "running")),
              {"event": "STATE_DELTA", "data": "{not json"},
              _frame("RUN_STARTED", {"runId": "w1"}, "w1"),
              _frame("SOMETHING_NEW", {}),
              _delta({"op": "remove", "path": "/agents/w1"})]
    out = _feed(tmp_path, frames)
    assert list(out["agents"]) == [odd]
    assert out["running"] == [odd]
    assert [n["id"] for n in out["graph"]["nodes"]] == [
        "friday", "team:worker", odd]


def test_reload_rebuilds_the_same_graph_and_parent_edges_need_the_parent(
        tmp_path):
    child = _agent("c", "running", parent="p", role="coder")
    frames = [_snap(_agent("p", "running", role="lead")),
              _delta({"op": "add", "path": "/agents/c", "value": child})]
    first = _feed(tmp_path, frames)
    again = _feed(tmp_path, frames)
    assert first["graph"] == again["graph"]
    assert [e["source"] for e in _edge_to(first["graph"], "c")] == ["p"]
    assert _node(first["graph"], "c")["parentId"] == "team:coder"
    orphan = _feed(tmp_path, [_snap(child)])
    assert [e["source"] for e in _edge_to(orphan["graph"], "c")] == ["friday"]


def test_python_frames_rebuild_python_states_in_js(tmp_path):
    from friday import ui_server as u

    def st(run_id, state, position=None):
        return cc.AgentState(run_id=run_id, parent_run_id="", role="r",
                             model="m", state=state, reason="",
                             position=position)

    t0 = [st("a", "queued", 1), st("gone", "running")]
    t1 = [st("a", "running"), st("b", "queued", 1)]
    frames = [u._event_frame(f.type, f.payload, f.run_id)
              for f in cc.to_agui(None, t0) + cc.to_agui(t0, t1)]
    out = _feed(tmp_path, frames)
    assert {k: v["state"] for k, v in out["agents"].items()} == {
        a.run_id: a.state for a in t1}
    assert out["running"] == ["a"]
