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


# ---------------------------------------------------------------------------
# FAIL-001 extra scope: capture from the handoff path, matching beyond the
# exact token, applicability conditions that are evaluated.
# ---------------------------------------------------------------------------

FAILED_REPORT = (
    "Status: FAILED\n"
    "Blockers: voice turn times out after the Hermes broker restarts\n"
    "Root cause: the session kept the dead broker's MCP handle\n"
    "Repair: re-resolve the broker handle on reconnect\n"
    "Tests: tests/test_hermes_engine.py::test_reconnect\n"
)


def _failed_handoff(**over):
    from friday.handoff import Handoff
    fields = dict(
        task_id="wr-1", agent="worker", status="FAILED",
        summary="delegated task failed",
        blockers=("voice turn times out after the Hermes broker restarts",),
        root_cause="the session kept the dead broker's MCP handle",
        repair="re-resolve the broker handle on reconnect",
        tests_run=("tests/test_hermes_engine.py::test_reconnect",))
    fields.update(over)
    return Handoff(**fields)


def test_a_failed_handoff_with_cause_and_repair_becomes_a_pattern(tmp_path):
    from friday.handoff import capture_failure
    g = genome(tmp_path)

    out = capture_failure(_failed_handoff(), genome=g)

    assert out["status"] == "captured"
    found = g.retrieve("the voice turn times out again after a broker restart")
    assert found is not None
    assert "re-resolve the broker handle" in found["repair"]


def test_a_failed_handoff_nobody_root_caused_is_not_stored(tmp_path):
    from friday.handoff import capture_failure
    g = genome(tmp_path)

    out = capture_failure(_failed_handoff(root_cause="", repair=""), genome=g)

    assert out["status"] == "skipped"
    assert sorted(out["missing"]) == ["repair", "root_cause"]
    assert g.listing() == []


def test_a_handoff_that_did_not_fail_is_not_captured(tmp_path):
    from friday.handoff import capture_failure
    g = genome(tmp_path)

    out = capture_failure(_failed_handoff(status="COMPLETE"), genome=g)

    assert out["status"] == "skipped"
    assert g.listing() == []


def test_the_worker_report_labels_fill_root_cause_and_repair():
    from friday.handoff import Handoff
    h = Handoff.from_work_run({"work_run_id": "wr-9", "status": "FAILED",
                               "result": FAILED_REPORT})
    assert h.root_cause == "the session kept the dead broker's MCP handle"
    assert h.repair == "re-resolve the broker handle on reconnect"
    assert Handoff.from_json(h.to_json()).repair == h.repair


def test_a_failed_work_run_is_captured_when_it_lands(tmp_path, monkeypatch):
    """The wire: a terminal FAILED run goes work-run -> Handoff -> promotion
    seam -> genome, with nobody calling failure_capture."""
    from friday import hermes_bridge as hb
    from friday.toolsets import memory as M
    path = tmp_path / "wire.sqlite3"
    monkeypatch.setenv("ADA_DB", str(path))
    M.reset_store(None)
    try:
        log = hb.WorkRunLog(path)
        run_id = log.create(task="fix the voice turn")
        log.update(run_id, status=hb.FAILED, result=FAILED_REPORT)
        found = fg.FailureGenome(path).retrieve(
            "the voice turn times out again after a broker restart")
    finally:
        M.reset_store(None)
    assert found is not None
    assert "re-resolve the broker handle" in found["repair"]


def test_a_differently_inflected_symptom_still_retrieves_the_repair(tmp_path):
    """Matching is not exact-token: timed/times/timing, restarted/restarts
    and turns/turn are one concept each."""
    g = genome(tmp_path)
    g.capture(broker_restart_failure())

    assert g.retrieve("voice turns timed out following broker restarted",
                      task_class="voice-turn") is not None
    assert g.retrieve("voice turn timing out, broker restarting",
                      task_class="voice-turn") is not None
    assert g.retrieve("the invoice pdf renders with the wrong font",
                      task_class="voice-turn") is None


def _conditioned(*conditions):
    return fg.FailureRecord(
        symptom="build step crashes while packaging the installer",
        root_cause="the packager reads the registry on install",
        repair="run the packager with the registry shim",
        applicability=list(conditions))


def test_a_repair_whose_condition_does_not_hold_is_not_offered(tmp_path):
    g = genome(tmp_path)
    g.capture(_conditioned("os == windows"))
    symptom = "build step crashes while packaging the installer"

    assert g.retrieve(symptom, facts={"os": "linux"}) is None
    found = g.retrieve(symptom, facts={"os": "windows"})
    assert found["conditions"]["met"] == ["os == windows"]
    assert found["conditions"]["unmet"] == []


def test_version_conditions_compare_as_versions_not_strings(tmp_path):
    g = genome(tmp_path)
    g.capture(_conditioned("python >= 3.10"))
    symptom = "build step crashes while packaging the installer"

    assert g.retrieve(symptom, facts={"python": "3.9.18"}) is None
    assert g.retrieve(symptom, facts={"python": "3.12.1"}) is not None
    # "3.9" > "3.10" as strings; as versions it is below 3.10.
    assert g.retrieve(symptom, facts={"python": "3.9"}) is None


def test_a_condition_that_cannot_be_checked_is_reported_not_assumed(tmp_path):
    g = genome(tmp_path)
    g.capture(_conditioned("the broker runs out of process", "gpu >= 2",
                           "os != darwin"))

    found = g.retrieve("build step crashes while packaging the installer",
                       facts={"os": "windows"})

    assert found["conditions"]["unverified"] == [
        "the broker runs out of process", "gpu >= 2"]
    assert found["conditions"]["met"] == ["os != darwin"]
    assert found["applicability"][0] == "the broker runs out of process"


# -- review fixes: fuzzy matching must not merge opposites ------------------

OPPOSITES = [
    ("login returns unauthorized", "login returns authorized"),
    ("service unavailable after deploy", "service available after deploy"),
    ("token invalid on refresh", "token valid on refresh"),
    ("listener bound to port 8000", "listener bound to port 8001"),
]


def _rec(symptom, repair="fix"):
    return fg.FailureRecord(symptom=symptom, root_cause="cause of " + symptom,
                            repair=repair + " for " + symptom)


def test_a_word_and_its_opposite_are_not_the_same_word():
    for a, b in OPPOSITES:
        exact = len(set(fg._tokens_of(a)) & set(fg._tokens_of(b))) / len(
            set(fg._tokens_of(a)) | set(fg._tokens_of(b)))
        assert fg._similarity(a, b) <= exact + 1e-9, (a, b)
        assert fg._similarity(a, b, fuzzy=False) < fg.DUPLICATE_AT


def test_opposite_failures_are_two_records_and_keep_their_own_repair(tmp_path):
    for a, b in OPPOSITES:
        g = fg.FailureGenome(tmp_path / (a.split()[0] + b[-4:] + ".sqlite3"))
        assert g.capture(_rec(a, "repair-A"))["status"] == "captured"
        assert g.capture(_rec(b, "repair-B"))["status"] == "captured", (a, b)
        assert len(g.listing()) == 2
        assert "repair-A" in g.retrieve(a)["repair"]
        assert "repair-B" in g.retrieve(b)["repair"]


# -- review fixes: "== 3.11" means any 3.11.x ---------------------------------

def test_an_equality_condition_holds_at_the_precision_written(tmp_path):
    g = genome(tmp_path)
    g.capture(_conditioned("python == 3.11"))
    symptom = "build step crashes while packaging the installer"

    assert g.retrieve(symptom, facts={"python": "3.11.15"}) is not None
    assert g.retrieve(symptom, facts={"python": "3.11"}) is not None
    assert g.retrieve(symptom, facts={"python": "3.12.1"}) is None
    assert fg.evaluate_conditions(["python != 3.11"],
                                  {"python": "3.11.15"})["unmet"]
    assert fg.evaluate_conditions(["python >= 3.11"],
                                  {"python": "3.11.0"})["met"]


# -- review fixes: auto-capture is insert-only, marked, and evidenced --------

def test_an_auto_capture_never_replaces_a_record_already_on_file(tmp_path):
    from friday.handoff import capture_failure
    g = genome(tmp_path)
    g.capture(fg.FailureRecord(
        symptom="voice turn times out after the Hermes broker restarts",
        root_cause="owner-verified cause", repair="owner-verified repair"))

    out = capture_failure(_failed_handoff(root_cause="worker guess",
                                          repair="worker repair"), genome=g)

    assert out["status"] == "skipped"
    assert "duplicate" in out["note"]
    rows = g.listing()
    assert len(rows) == 1
    assert rows[0]["repair"] == "owner-verified repair"


def test_an_auto_captured_row_carries_the_worker_reported_marker(tmp_path):
    from friday.handoff import capture_failure
    from friday import memory_stack
    g = genome(tmp_path)
    assert capture_failure(_failed_handoff(), genome=g)["status"] == "captured"

    row = g.retrieve("voice turn times out after the Hermes broker restarts")
    assert row["source"] == fg.SOURCE_WORKER
    manual = fg.FailureGenome(tmp_path / "manual.sqlite3")
    manual.capture(broker_restart_failure())
    assert manual.listing()[0]["source"] == ""
    assert fg.SOURCE_WORKER in memory_stack.render_failure(row)


def test_an_auto_capture_with_no_test_or_evidence_is_not_stored(tmp_path):
    from friday.handoff import capture_failure
    g = genome(tmp_path)

    out = capture_failure(_failed_handoff(tests_run=(), evidence_refs=()),
                          genome=g)
    assert out["status"] == "skipped"
    assert out["missing"] == ["tests_run or evidence_refs"]
    assert g.listing() == []
    assert capture_failure(_failed_handoff(tests_run=(), evidence_refs=("log:1",)),
                           genome=g)["status"] == "captured"


def test_a_skipped_failed_handoff_is_logged_without_its_contents(
        tmp_path, caplog):
    import logging
    from friday import memory_promotion
    g = genome(tmp_path)
    secretless = _failed_handoff(root_cause="", repair="")
    with caplog.at_level(logging.INFO, logger="friday.memory_promotion"):
        memory_promotion.promote_handoff(secretless, genome=g)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "wr-1" in text and "root_cause" in text
    assert "voice turn times out" not in text
