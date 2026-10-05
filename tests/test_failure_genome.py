"""FAIL-001 / PRD §36: a root-caused failure is retrieved before it repeats.

The acceptance criterion is one sentence -- "similar failure retrieves prior
repair" -- so the load-bearing test is `test_a_similar_failure_retrieves_the
_prior_repair`. The rest guard the two ways this goes wrong in practice: a
genome full of symptoms nobody root caused, and a repair offered for a
failure it does not apply to.
"""
from friday import failure_genome as fg


def genome(tmp_path):
    return fg.FailureGenome(tmp_path / "genome.sqlite3")


def broker_restart_failure():
    return fg.FailureRecord(
        symptom="voice turn times out after the Hermes broker restarts",
        root_cause="the agent session kept the dead broker's MCP handle; "
                   "every tool call waited the full 30s timeout",
        repair="re-resolve the broker handle on reconnect instead of "
               "caching it for the session",
        task_class="voice-turn",
        environment="windows, livekit agents 0.12",
        failed_approach="raised the tool timeout to 90s",
        wrong_assumption="assumed the handle survived a broker restart",
        prevention="handles to out-of-process things are resolved per call",
        regression_test="tests/test_hermes_engine.py::test_reconnect",
        skill_candidate="reconnect-broker-and-reresolve",
        applicability=["the broker runs out of process"],
    )


def test_a_similar_failure_retrieves_the_prior_repair(tmp_path):
    """The acceptance criterion. Worded differently, same failure: the stored
    repair comes back rather than being re-derived."""
    g = genome(tmp_path)
    assert g.capture(broker_restart_failure())["status"] == "captured"

    found = g.retrieve("the voice turn times out again after a broker restart",
                       task_class="voice-turn")

    assert found is not None
    assert "re-resolve the broker handle" in found["repair"]
    assert found["wrong_assumption"].startswith("assumed the handle")
    assert found["regression_test"] == "tests/test_hermes_engine.py::test_reconnect"
    assert found["applicability"] == ["the broker runs out of process"]
    assert found["score"] >= fg.MATCH_AT


def test_an_unrelated_failure_is_not_offered(tmp_path):
    g = genome(tmp_path)
    g.capture(broker_restart_failure())
    assert g.retrieve("the invoice pdf renders with the wrong font",
                      task_class="document-render") is None


def test_a_repair_from_another_task_class_is_not_offered(tmp_path):
    """Same words, different kind of work. Handing over this repair would
    send the caller down a wrong path with Friday's authority behind it."""
    g = genome(tmp_path)
    g.capture(broker_restart_failure())
    assert g.retrieve("the voice turn times out again after a broker restart",
                      task_class="db-migration") is None


class _Tools:
    """Collects what `register(mcp)` decorates, so the MCP wrappers can be
    called as plain functions."""

    def __init__(self):
        self.fns = {}

    def tool(self):
        def deco(fn):
            self.fns[fn.__name__] = fn
            return fn
        return deco


def _registered(monkeypatch, tmp_path):
    from friday.tools import vnext_control
    monkeypatch.setenv("ADA_DB", str(tmp_path / "genome.sqlite3"))
    tools = _Tools()
    vnext_control.register(tools)
    return tools.fns


def test_friday_can_record_a_root_caused_failure_and_it_comes_back(
        monkeypatch, tmp_path):
    """The producer. Retrieval was wired, capture had no caller, so the genome
    stayed empty in production. The MCP tool is the call site: Friday records
    a failure once it has the root cause, and the next request that looks like
    it gets the repair in its memory bundle before a model plans."""
    from friday import memory_stack
    fns = _registered(monkeypatch, tmp_path)

    out = fns["failure_capture"](
        symptom="voice turn times out after the Hermes broker restarts",
        root_cause="the session kept the dead broker's MCP handle",
        repair="re-resolve the broker handle on reconnect",
        task_class="voice-turn",
        applicability=["the broker runs out of process"])
    assert out["status"] == "captured"

    looked_up = fns["failure_lookup"](
        "the voice turn times out again after a broker restart",
        task_class="voice-turn")
    assert looked_up["status"] == "succeeded"
    assert "re-resolve the broker handle" in looked_up["matches"][0]["repair"]
    assert looked_up["matches"][0]["applicability"] == [
        "the broker runs out of process"]

    tier = memory_stack.failures("the voice turn times out after a broker restart")
    assert [i["repair"] for i in tier["items"]] == [
        "re-resolve the broker handle on reconnect"]


def test_the_tool_refuses_a_failure_nobody_root_caused(monkeypatch, tmp_path):
    fns = _registered(monkeypatch, tmp_path)
    out = fns["failure_capture"](symptom="it broke again", root_cause="",
                                 repair="")
    assert out["status"] == "refused"
    assert fns["failure_lookup"]("it broke again")["matches"] == []


def test_a_secret_in_a_failure_is_never_stored(tmp_path):
    """Retrieved records are placed in the model's context, so a token pasted
    into a repair note would be handed to every later task that matches."""
    g = genome(tmp_path)
    out = g.capture(fg.FailureRecord(
        symptom="gateway returns 401 on every call",
        root_cause="the key was rotated",
        repair="set the key to sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4"))
    assert out["status"] == "refused"
    assert out["missing"] == []
    assert g.listing() == []


def test_a_symptom_with_no_root_cause_or_repair_is_refused(tmp_path):
    out = genome(tmp_path).capture(fg.FailureRecord(
        symptom="it broke again", root_cause="  ", repair=""))
    assert out["status"] == "refused"
    assert out["missing"] == ["root_cause", "repair"]
    assert genome(tmp_path).listing() == []


def test_recording_the_same_failure_twice_updates_one_row(tmp_path):
    """Otherwise the fifth recurrence buries the best repair under four
    near-identical rows."""
    g = genome(tmp_path)
    first = g.capture(broker_restart_failure())
    again = broker_restart_failure()
    again.repair = "resolve the broker handle per call; the reconnect hook " \
                   "only clears the cache"
    second = g.capture(again)
    assert second["status"] == "updated" and second["id"] == first["id"]
    rows = g.listing()
    assert len(rows) == 1 and "per call" in rows[0]["repair"]


def test_retrieval_counts_its_hits(tmp_path):
    """A genome no query ever matches is dead weight, and only the count
    makes that visible."""
    g = genome(tmp_path)
    g.capture(broker_restart_failure())
    g.retrieve("voice turn times out after broker restart", task_class="voice-turn")
    g.retrieve("voice turn times out after broker restart", task_class="voice-turn")
    assert g.listing()[0]["hits"] == 2


def test_the_bundle_a_model_plans_from_carries_the_prior_repair(tmp_path):
    """The wiring, at the seam that matters: PRD §36 asks for retrieval
    *before* repeating a known failure, and `aggregate` is what every brain
    and every Hermes bundle reads. No call site has to remember to ask."""
    from friday import memory_stack

    fg.FailureGenome().capture(broker_restart_failure())   # ADA_DB, per test
    bundle = memory_stack.aggregate(
        "fix the voice turn that times out after the broker restarts",
        budget_tokens=600, include_episodes=False)

    assert bundle["injected"]["failures"] == 1
    assert "re-resolve the broker handle" in bundle["prompt"]
    assert [e["tier"] for e in bundle["layout"] if e["tier"] == "failures"]
