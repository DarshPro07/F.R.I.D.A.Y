"""CTX-002: the staged tool router behind capability search, mode by mode."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from friday import capability_router as R
from friday import tool_router, tool_router_rollout as RO

IDS = ["music_search", "music_play_mood", "files_read", "files_info"]
TEXTS = ["find songs by name", "play a song for a mood", "read a text file", "size of a file"]
RANKING = ["music_search", "music_play_mood", "files_read"]
ALWAYS_STAGE_TWO = tool_router.Thresholds(agree=2.0, margin=2.0)     # what the shipped defaults amount to
EAGER = tool_router.Thresholds(agree=0.0, margin=0.0)


class FakeChooser:
    def __init__(self, answer=None, error: Exception | None = None):
        self.answer = answer or {"decision": "TOOL", "tool_id": "files_info", "confidence": 0.9}
        self.error, self.seen = error, []

    def choose(self, query, cards):
        self.seen.append(query)
        if self.error:
            raise self.error
        return self.answer


def make(mode, *, chooser=None, build=None, thresholds=ALWAYS_STAGE_TWO, **config):
    built = []

    def legacy(query, limit):
        return [{"capability": name} for name in RANKING[:limit]]

    def build_model_free(ids, texts, descriptions, *, shipped, chooser):
        built.append(1)
        return tool_router.StagedRouter(ids, texts, shipped=shipped, chooser=chooser, top_k=3,
                                        thresholds=thresholds, descriptions=descriptions)

    rollout = RO.Rollout(legacy=legacy, describe=lambda name: {"capability": name},
                         inputs=lambda: (IDS, TEXTS, {}), config=RO.Config(mode=mode, **config),
                         chooser=chooser, build=build or build_model_free)
    return rollout, built


def names(result):
    return [m["capability"] for m in result]


def routes(rollout):
    return [r for r in rollout.records if r["event"] == "route"]


# -- configuration ----------------------------------------------------------


def test_the_default_mode_is_legacy():
    assert RO.Config().mode == RO.LEGACY
    assert RO.Config.from_env({}).mode == RO.LEGACY


def test_a_bad_setting_never_stops_boot_and_falls_back_to_the_safe_value():
    config = RO.Config.from_env({
        "FRIDAY_TOOL_ROUTER_MODE": "turbo", "FRIDAY_TOOL_ROUTER_TIMEOUT_S": "soon",
        "FRIDAY_TOOL_ROUTER_SAMPLE_RATE": "7", "FRIDAY_TOOL_ROUTER_SAMPLE_BUDGET": "nan"})
    assert (config.mode, config.timeout_s, config.sample_rate, config.sample_budget) == ("legacy", 2.0, 1.0, 0)


def test_each_mode_is_read_from_the_environment():
    for mode in RO.MODES:
        assert RO.Config.from_env({"FRIDAY_TOOL_ROUTER_MODE": f" {mode.upper()} "}).mode == mode


# -- legacy -----------------------------------------------------------------


def test_legacy_mode_answers_from_legacy_and_builds_nothing():
    rollout, built = make(RO.LEGACY, chooser=FakeChooser())
    assert names(rollout.search("find songs", 2)) == RANKING[:2]
    rollout.drain()
    assert not built and not rollout.records and rollout.metrics.snapshot()["requests"] == 0


# -- shadow -----------------------------------------------------------------


def test_shadow_returns_exactly_what_legacy_returns_even_when_staged_disagrees():
    chooser = FakeChooser({"decision": "TOOL", "tool_id": "files_info", "confidence": 1})
    rollout, _ = make(RO.SHADOW, chooser=chooser, sample_rate=1.0, sample_budget=5)
    result = rollout.search("how big is this file", 3)
    rollout.drain()
    assert names(result) == RANKING                     # the executed choice is legacy's
    (record,) = routes(rollout)
    assert record["staged_choice"] == "files_info" and record["legacy_choice"] == "music_search"


def test_a_shadow_record_carries_what_promotion_needs():
    rollout, _ = make(RO.SHADOW)
    rollout.search("find songs", 3)
    rollout.drain()
    (record,) = routes(rollout)
    assert {"legacy_choice", "legacy_top", "staged_top", "staged_choice", "confidence", "latency_ms",
            "would_clarify", "decision", "stage", "uncertain", "chooser_called", "fallback",
            "query_sha"} <= set(record)
    assert record["mode"] == "shadow" and record["latency_ms"] >= 0 and record["staged_top"]
    assert "find songs" not in json.dumps(record)       # a hash of the request, never its text


def test_shadow_stage_two_is_off_the_model_by_default():
    chooser = FakeChooser()
    rollout, _ = make(RO.SHADOW, chooser=chooser)       # sample_rate 0, budget 0
    for text in ("a", "b", "c"):
        rollout.search(text, 3)
    rollout.drain()
    assert chooser.seen == [] and len(routes(rollout)) == 3
    assert all(r["uncertain"] and not r["chooser_called"] for r in routes(rollout))
    assert rollout.metrics.snapshot()["chooser_invocation_rate"] == 0.0


def test_shadow_calls_the_chooser_only_inside_the_sample_budget():
    chooser = FakeChooser()
    rollout, _ = make(RO.SHADOW, chooser=chooser, sample_rate=1.0, sample_budget=2)
    for text in ("one", "two", "three", "four"):
        rollout.search(text, 3)
    rollout.drain()
    assert len(chooser.seen) == 2
    snapshot = rollout.metrics.snapshot()
    assert (snapshot["requests"], snapshot["chooser_calls"], snapshot["chooser_invocation_rate"]) == (4, 2, 0.5)


def test_the_sample_is_a_stable_share_of_requests_not_every_request():
    chooser = FakeChooser()
    rollout, _ = make(RO.SHADOW, chooser=chooser, sample_rate=0.5, sample_budget=1000)
    texts = [f"request number {i}" for i in range(40)]
    for text in texts:
        rollout.search(text, 3)
        rollout.drain()                                   # a busy worker would drop some
    asked = set(chooser.seen)
    assert 0 < len(asked) < len(texts)
    assert {t for t in texts if RO._fraction(RO.query_id(t)) < 0.5} == asked


def test_a_confident_stage_one_route_does_not_spend_the_chooser():
    chooser = FakeChooser()
    rollout, _ = make(RO.SHADOW, chooser=chooser, thresholds=EAGER, sample_rate=1.0, sample_budget=9)
    rollout.search("find songs", 3)
    rollout.drain()
    assert chooser.seen == [] and rollout.metrics.snapshot()["chooser_invocation_rate"] == 0.0


def test_a_broken_staged_router_in_shadow_never_touches_the_answer():
    def broken(*_args, **_kwargs):
        raise RuntimeError("no index")

    rollout, _ = make(RO.SHADOW, build=broken)
    assert names(rollout.search("find songs", 2)) == RANKING[:2]
    rollout.drain()
    (record,) = routes(rollout)
    assert record["fallback"] == "error: RuntimeError" and record["staged_choice"] is None
    assert rollout.metrics.snapshot()["errors"] == 1


def test_shadow_drops_evaluations_when_the_worker_is_busy_instead_of_waiting():
    gate = threading.Event()
    rollout, _ = make(RO.SHADOW, chooser=FakeChooser(), sample_rate=1.0, sample_budget=99)

    class Slow(FakeChooser):
        def choose(self, query, cards):
            gate.wait(5)
            return super().choose(query, cards)

    rollout.chooser = Slow()                              # before the first search builds the index
    for i in range(RO.MAX_PENDING + 4):
        assert names(rollout.search(f"q{i}", 2)) == RANKING[:2]      # never blocked, never changed
    assert rollout.metrics.snapshot()["dropped"] >= 4
    gate.set()
    rollout.drain()


def test_the_tool_that_then_succeeded_is_logged_against_the_search():
    rollout, _ = make(RO.SHADOW)
    rollout.search("find songs", 3)
    rollout.drain()
    rollout.observe("music_search")
    observed = [r for r in rollout.records if r["event"] == "observed"]
    assert [(r["tool"], r["query_sha"]) for r in observed] == [("music_search", routes(rollout)[0]["query_sha"])]


def test_a_tool_after_a_new_turn_is_not_paired_with_the_earlier_turns_search():
    rollout, _ = make(RO.SHADOW)
    rollout.search("find songs", 3)
    rollout.drain()
    rollout.new_turn()
    rollout.observe("music_search")                      # no search yet this turn: nothing to pair with
    assert not [r for r in rollout.records if r["event"] == "observed"]
    rollout.search("read a file", 3)
    rollout.drain()
    rollout.observe("music_search")
    observed, = [r for r in rollout.records if r["event"] == "observed"]
    assert observed["query_sha"] == RO.query_id("read a file") and observed["turn"] == 1


def test_an_observed_tool_says_whether_the_search_offered_it():
    rollout, _ = make(RO.SHADOW)
    rollout.search("find songs", 2)
    rollout.drain()
    offered, elsewhere = RANKING[0], "not_in_the_shortlist"
    rollout.observe(offered)
    rollout.observe(elsewhere)
    flags = {r["tool"]: r["shortlisted"] for r in rollout.records if r["event"] == "observed"}
    assert flags == {offered: True, elsewhere: False}


def test_staged_shortlist_is_what_the_model_was_given_not_the_legacy_ranking():
    class Fixed:
        def route(self, query, use_chooser=True):
            return tool_router.Route("TOOL", "files_info", 2, 0.9, ("files_info", "files_read"),
                                     chooser_called=True)

    rollout, _ = make(RO.STAGED, build=lambda *args, **kwargs: Fixed())
    assert names(rollout.search("anything", 2)) == ["files_info", "files_read"]
    rollout.observe("files_info")
    rollout.observe(RANKING[0])                          # legacy's first choice, never offered here
    flags = {r["tool"]: r["shortlisted"] for r in rollout.records if r["event"] == "observed"}
    assert flags == {"files_info": True, RANKING[0]: False}


def test_routes_and_outcomes_carry_the_turn_they_belong_to():
    rollout, _ = make(RO.SHADOW)
    rollout.search("first", 2)
    rollout.drain()
    rollout.new_turn()
    rollout.search("second", 2)
    rollout.drain()
    assert sorted(r["turn"] for r in routes(rollout)) == [0, 1]


def test_legacy_mode_records_no_outcome():
    rollout, _ = make(RO.LEGACY)
    rollout.search("find songs", 3)
    rollout.observe("music_search")
    assert not rollout.records


def test_records_are_appended_to_the_configured_log(tmp_path):
    sink = tmp_path / "router.jsonl"
    rollout, _ = make(RO.SHADOW, log_path=str(sink))
    rollout.search("find songs", 3)
    rollout.drain()
    rollout.observe("music_search")
    lines = [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines()]
    assert [line["event"] for line in lines] == ["route", "observed"]


def test_an_unwritable_log_is_counted_and_does_not_fail_routing(tmp_path):
    rollout, _ = make(RO.SHADOW, log_path=str(tmp_path / "missing" / "router.jsonl"))
    assert names(rollout.search("find songs", 2)) == RANKING[:2]
    rollout.drain()
    assert rollout.metrics.snapshot()["log_errors"] == 1 and len(routes(rollout)) == 1


# -- staged -----------------------------------------------------------------


def test_staged_mode_lets_the_chooser_own_the_answer():
    chooser = FakeChooser({"decision": "TOOL", "tool_id": "files_info", "confidence": 0.8})
    rollout, _ = make(RO.STAGED, chooser=chooser)
    result = rollout.search("how big is this file", 3)
    assert names(result)[0] == "files_info" and len(result) == 3
    assert rollout.last_decision["decision"] == "TOOL" and not rollout.clarifying()
    (record,) = routes(rollout)
    assert record["mode"] == "staged" and record["chooser_called"] and record["fallback"] is None


def test_staged_mode_without_a_chooser_discloses_the_top_candidates_uncertainly():
    rollout, _ = make(RO.STAGED)
    result = rollout.search("find songs", 3)
    assert result and rollout.last_decision["uncertain"] is True


def test_a_clarify_decision_keeps_every_candidate_and_asks_the_caller_to_ask():
    chooser = FakeChooser({"decision": "CLARIFY", "tool_id": None, "confidence": 0.4})
    rollout, _ = make(RO.STAGED, chooser=chooser)
    result = rollout.search("close it", 3)
    assert len(result) == 3 and rollout.clarifying()
    assert routes(rollout)[0]["would_clarify"] is True
    assert rollout.metrics.snapshot()["clarify"] == 1


def test_an_abstain_decision_serves_the_legacy_answer_and_says_why():
    chooser = FakeChooser({"decision": "ABSTAIN", "tool_id": None, "confidence": 0.7})
    rollout, _ = make(RO.STAGED, chooser=chooser)
    result = rollout.search("read a text file", 3)
    (record,) = routes(rollout)
    assert record["staged_top"] != RANKING                # the staged shortlist is not what was served
    assert names(result) == RANKING and rollout.last_decision is None and not rollout.clarifying()
    assert (record["decision"], record["fallback"]) == ("ABSTAIN", "abstain")
    snapshot = rollout.metrics.snapshot()
    assert (snapshot["abstain"], snapshot["fallbacks"], snapshot["chooser_calls"]) == (1, 1, 1)
    assert (snapshot["errors"], snapshot["timeouts"]) == (0, 0)


def test_an_abstain_after_nothing_matched_also_serves_legacy():
    rollout, _ = make(RO.STAGED, build=lambda *a, **k: _NothingMatches())
    assert names(rollout.search("zzzz", 3)) == RANKING
    assert routes(rollout)[0]["fallback"] == "abstain" and rollout.metrics.snapshot()["abstain"] == 1


class _NothingMatches:
    def route(self, query, **_kwargs):
        return tool_router.Route(tool_router.ABSTAIN, None, 1, 0.0, (), reason="nothing matched")


def test_staged_falls_back_to_legacy_when_the_router_cannot_be_built():
    attempts = []

    def broken(*_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("no index")

    rollout, _ = make(RO.STAGED, build=broken)
    assert names(rollout.search("find songs", 2)) == RANKING[:2]
    assert names(rollout.search("find songs again", 2)) == RANKING[:2]
    snapshot = rollout.metrics.snapshot()
    assert (snapshot["fallbacks"], snapshot["errors"]) == (2, 2)
    assert len(attempts) == 1                            # a failed build is not retried per request
    assert rollout.last_decision is None
    assert routes(rollout)[0]["fallback"] == "error: RuntimeError"


def test_staged_falls_back_to_legacy_when_the_deadline_passes():
    release = threading.Event()

    class Slow(FakeChooser):
        def choose(self, query, cards):
            release.wait(5)
            return super().choose(query, cards)

    rollout, _ = make(RO.STAGED, chooser=Slow(), timeout_s=0.05)
    try:
        assert names(rollout.search("how big is this file", 2)) == RANKING[:2]
        snapshot = rollout.metrics.snapshot()
        assert (snapshot["timeouts"], snapshot["fallbacks"]) == (1, 1)
        assert routes(rollout)[0]["fallback"] == "timeout" and rollout.last_decision is None
    finally:
        release.set()


def _until(condition, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_the_deadline_covers_a_slow_first_index_build():
    release, started = threading.Event(), threading.Event()

    def slow_build(ids, texts, descriptions, *, shipped, chooser):
        started.set()
        release.wait(5)
        return tool_router.StagedRouter(ids, texts, shipped=shipped, chooser=chooser, top_k=3,
                                        thresholds=ALWAYS_STAGE_TWO, descriptions=descriptions)

    rollout, _ = make(RO.STAGED, build=slow_build, timeout_s=0.3)
    try:
        began = time.perf_counter()
        assert names(rollout.search("find songs", 2)) == RANKING[:2]
        assert time.perf_counter() - began < 2.0         # returned at the deadline, not after the build
        assert started.wait(2)                           # the build was running when the deadline passed
        snapshot = rollout.metrics.snapshot()
        assert (snapshot["timeouts"], snapshot["errors"], snapshot["fallbacks"]) == (1, 0, 1)
        assert routes(rollout)[0]["fallback"] == "timeout" and rollout.last_decision is None
    finally:
        release.set()
    assert _until(lambda: rollout._staged is not None)   # the build finishes in the background...
    assert rollout.search("find songs", 3) and rollout.last_decision is not None   # ...and serves the next request


def test_a_timeout_error_from_the_build_is_an_error_that_still_serves_legacy():
    # On Python 3.11 the futures TimeoutError is the builtin one (an OSError): it must not be
    # mistaken for a missed deadline, and must not escape search() as an AttributeError.
    def broken(*_args, **_kwargs):
        raise TimeoutError("index store timed out")

    rollout, _ = make(RO.STAGED, build=broken)
    assert names(rollout.search("find songs", 2)) == RANKING[:2]
    snapshot = rollout.metrics.snapshot()
    assert (snapshot["errors"], snapshot["timeouts"], snapshot["fallbacks"]) == (1, 0, 1)
    assert routes(rollout)[0]["fallback"] == "error: TimeoutError"


def test_a_timeout_error_from_the_route_is_an_error_not_a_missed_deadline():
    class RaisesTimeout:
        def route(self, query, **_kwargs):
            raise TimeoutError("chooser socket")

    rollout, _ = make(RO.STAGED, build=lambda *a, **k: RaisesTimeout())
    assert names(rollout.search("find songs", 2)) == RANKING[:2]
    snapshot = rollout.metrics.snapshot()
    assert (snapshot["errors"], snapshot["timeouts"]) == (1, 0)


def test_a_stopped_route_pool_falls_back_to_legacy():
    rollout, _ = make(RO.STAGED)
    rollout._route_pool.shutdown()
    assert names(rollout.search("find songs", 2)) == RANKING[:2]
    assert rollout.metrics.snapshot()["errors"] == 1


def test_a_tool_set_change_does_not_wait_for_an_index_build_in_flight():
    release, started = threading.Event(), threading.Event()

    def slow_build(ids, texts, descriptions, *, shipped, chooser):
        started.set()
        release.wait(5)
        return tool_router.StagedRouter(ids, texts, shipped=shipped, chooser=chooser, top_k=3,
                                        thresholds=ALWAYS_STAGE_TWO, descriptions=descriptions)

    rollout, _ = make(RO.STAGED, build=slow_build, timeout_s=0.3)
    try:
        rollout.search("find songs", 2)                  # times out; the build is still running
        assert started.wait(2)
        began = time.perf_counter()
        rollout.reset()                                  # Router.load() calls this on the voice path
        assert time.perf_counter() - began < 1.0
    finally:
        release.set()
    assert _until(lambda: not rollout._build_lock.locked())
    assert rollout._staged is None                       # the stale index was not kept


def test_a_failing_chooser_is_a_stage_two_call_that_still_answers():
    rollout, _ = make(RO.STAGED, chooser=FakeChooser(error=TimeoutError("slow")))
    assert rollout.search("find songs", 3)
    snapshot = rollout.metrics.snapshot()
    assert (snapshot["chooser_calls"], snapshot["fallbacks"]) == (1, 0)


def test_the_invocation_rate_counts_chooser_calls_over_routed_requests():
    chooser = FakeChooser()
    rollout, _ = make(RO.STAGED, chooser=chooser)
    for text in ("a", "b", "c", "d"):
        rollout.search(text, 3)
    assert rollout.metrics.snapshot()["chooser_invocation_rate"] == 1.0
    easy, _ = make(RO.STAGED, chooser=FakeChooser(), thresholds=EAGER)
    for text in ("a", "b"):
        easy.search(text, 3)
    assert easy.metrics.snapshot()["chooser_invocation_rate"] == 0.0


def test_p95_latency_is_the_nearest_rank_of_what_was_observed():
    metrics = RO.Metrics()
    for ms in range(1, 101):
        metrics.observe(float(ms))
    assert metrics.snapshot()["p95_latency_ms"] == 95.0


def test_a_new_tool_set_is_indexed_again():
    rollout, built = make(RO.STAGED)
    rollout.search("find songs", 3)
    rollout.search("find songs", 3)
    assert len(built) == 1
    rollout.reset()
    rollout.search("find songs", 3)
    assert len(built) == 2


# -- the Router wiring --------------------------------------------------------


class FakeInfo:
    def __init__(self, name):
        self.name = name


class FakeTool:
    def __init__(self, name):
        self.info = FakeInfo(name)


@pytest.fixture
def router(monkeypatch):
    monkeypatch.delenv("FRIDAY_TOOL_ROUTER_MODE", raising=False)

    def model_free(ids, texts, descriptions, *, shipped, chooser):
        return tool_router.StagedRouter(ids, texts, shipped=shipped, chooser=chooser,
                                        descriptions=descriptions)

    monkeypatch.setattr(RO, "_build_staged", model_free)
    r = R.Router()
    r.load([FakeTool(n) for n in list(R.CORE_TOOLS) + [n for g in R.GROUPS.values() for n in g]])
    return r


REQUESTS = ["play some music", "read the file on my desktop", "what is on my calendar", "turn the volume down",
            "send a message to my team"]


def test_search_is_the_legacy_scorer_unless_a_mode_is_set(router):
    for text in REQUESTS:
        assert router.search(text) == router.legacy_search(text)
    assert router.rollout.config.mode == RO.LEGACY and not router.rollout.records


def test_in_shadow_mode_the_router_still_returns_the_legacy_answer(router, monkeypatch):
    monkeypatch.setenv("FRIDAY_TOOL_ROUTER_MODE", "shadow")
    for text in REQUESTS:
        assert router.search(text) == router.legacy_search(text)
    router.rollout.drain()
    legacy_first = {RO.query_id(text): (router.legacy_search(text) or [{"capability": None}])[0]["capability"]
                    for text in REQUESTS}
    assert {r["query_sha"]: r["legacy_choice"] for r in routes(router.rollout)} == legacy_first


def test_in_staged_mode_the_router_answers_from_the_staged_ranking(router, monkeypatch):
    monkeypatch.setenv("FRIDAY_TOOL_ROUTER_MODE", "staged")
    found = router.search("play some music")
    assert found and all(set(m) == set(router.legacy_search("play some music")[0]) for m in found)
    assert router.rollout.last_decision is not None and not router.needs_clarification()


def _broken_build(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("no index")

    monkeypatch.setattr(RO, "_build_staged", broken)


def test_in_staged_mode_the_router_falls_back_to_the_legacy_answer_on_error(router, monkeypatch):
    _broken_build(monkeypatch)
    monkeypatch.setenv("FRIDAY_TOOL_ROUTER_MODE", "staged")
    for text in REQUESTS:
        assert router.search(text) == router.legacy_search(text)
    assert router.rollout.metrics.snapshot()["fallbacks"] == len(REQUESTS)
    assert router.rollout.last_decision is None and not router.needs_clarification()


def test_a_broken_staged_router_in_shadow_mode_leaves_the_router_answer_legacy(router, monkeypatch):
    _broken_build(monkeypatch)
    monkeypatch.setenv("FRIDAY_TOOL_ROUTER_MODE", "shadow")
    for text in REQUESTS:
        assert router.search(text) == router.legacy_search(text)
    router.rollout.drain()
    assert {r["fallback"] for r in routes(router.rollout)} == {"error: RuntimeError"}
    assert not router.needs_clarification()


def test_a_successful_capability_is_recorded_against_the_search_in_shadow_mode(router, monkeypatch):
    monkeypatch.setenv("FRIDAY_TOOL_ROUTER_MODE", "shadow")
    router.note_succeeded("music_play_mood")             # nothing searched yet: nothing to pair with
    top = router.search("play some music")[0]["capability"]
    router.rollout.drain()
    router.note_succeeded(top)
    observed = [r for r in router.rollout.records if r["event"] == "observed"]
    assert [r["tool"] for r in observed] == [top]


def test_note_turn_stops_the_router_pairing_an_earlier_search(router, monkeypatch):
    monkeypatch.setenv("FRIDAY_TOOL_ROUTER_MODE", "shadow")
    router.note_turn()                                   # before any search: nothing to reset, no error
    top = router.search("play some music")[0]["capability"]
    router.rollout.drain()
    router.note_turn()
    router.note_succeeded(top)
    assert not [r for r in router.rollout.records if r["event"] == "observed"]


def test_loading_tools_resets_the_staged_index(router, monkeypatch):
    monkeypatch.setenv("FRIDAY_TOOL_ROUTER_MODE", "staged")
    router.search("play some music")
    assert router.rollout._staged is not None
    router.load([FakeTool(n) for n in R.CORE_TOOLS])
    assert router.rollout._staged is None


# -- the agent: a CLARIFY decision is asked, not acted on ---------------------------


@pytest.fixture
def chooser():
    return FakeChooser({"decision": "CLARIFY", "tool_id": None, "confidence": 0.4})


@pytest.fixture
def staged_agent(router, chooser, monkeypatch):
    """The real FridayAgent.search_capabilities over a real Router in staged mode, with a
    scripted chooser standing in for the model (no paid call)."""
    import agent_friday

    def always_stage_two(ids, texts, descriptions, *, shipped, chooser):
        return tool_router.StagedRouter(ids, texts, shipped=shipped, chooser=chooser,
                                        descriptions=descriptions, thresholds=ALWAYS_STAGE_TWO)

    monkeypatch.setattr(RO, "_build_staged", always_stage_two)
    monkeypatch.setenv("FRIDAY_TOOL_ROUTER_MODE", "staged")
    router._rollout().chooser = chooser
    agent = object.__new__(agent_friday.FridayAgent)
    agent._router, agent._tools_suspended, agent._tools_offline = router, False, False
    return agent


def _search(agent, query):
    import agent_friday
    return json.loads(asyncio.run(agent_friday.FridayAgent.search_capabilities.__wrapped__(agent, query)))


def test_a_clarify_decision_makes_the_agent_ask_before_calling_any_tool(staged_agent, chooser):
    out = _search(staged_agent, "close it")
    assert chooser.seen == ["close it"]      # the chooser, not retrieval alone, decided
    assert out["found"] > 0 and out["capabilities"]       # the shortlist is still there to name
    assert "Ask the boss which one they mean before calling any" in out["clarify"]


def test_an_abstain_decision_makes_the_agent_serve_the_legacy_shortlist_and_ask_nothing(staged_agent, chooser):
    router, query = staged_agent._router, "play some music"
    chooser.answer = {"decision": "ABSTAIN", "tool_id": None, "confidence": 0.6}
    legacy = router.legacy_search(query, limit=6)
    staged = router.rollout._router().route(query, use_chooser=False).candidates[:6]
    assert names(legacy) != list(staged)                # the two shortlists can be told apart
    before = router.rollout.metrics.snapshot()
    out = _search(staged_agent, query)
    after = router.rollout.metrics.snapshot()
    assert chooser.seen == [query]                      # the chooser, not retrieval alone, abstained
    assert out["capabilities"] == legacy and out["found"] == len(legacy)
    assert "clarify" not in out
    assert {key: after[key] - before[key] for key in ("abstain", "fallbacks")} == {"abstain": 1, "fallbacks": 1}


class _RecordingTool(FakeTool):
    """A tool use_capability can run: it records that it was called."""

    def __init__(self, name, calls):
        super().__init__(name)
        self._calls = calls

    async def __call__(self, arguments):
        self._calls.append((self.info.name, dict(arguments)))
        return json.dumps({"status": "succeeded"})


def test_shadow_mode_at_the_agent_changes_neither_the_search_answer_nor_the_tool_that_runs(staged_agent, chooser):
    import agent_friday
    agent, query, calls = staged_agent, "play some music", []
    router = agent._router
    router.all_tools = {name: _RecordingTool(name, calls) for name in router.all_tools}
    legacy = router.legacy_search(query, limit=6)
    top, rival = legacy[0]["capability"], "music_search"       # a staged candidate legacy does not rank first
    assert rival != top and rival in router.all_tools
    chooser.answer = {"decision": "TOOL", "tool_id": rival, "confidence": 0.9}
    router.rollout.config = RO.Config(mode=RO.SHADOW, sample_rate=1.0, sample_budget=5)
    agent._turn_owned_by, agent._owner_words, agent._spoke_this_turn = "", "", True
    agent._keep_group_open = lambda name: None

    out = _search(agent, query)
    ran = json.loads(asyncio.run(agent_friday.FridayAgent.use_capability.__wrapped__(agent, top, "{}")))
    router.rollout.drain()

    assert out["capabilities"] == legacy and "clarify" not in out        # the model was shown legacy's answer
    assert ran == {"status": "succeeded"} and calls == [(top, {})]       # and only legacy's tool ran
    (record,) = routes(router.rollout)
    assert record["mode"] == "shadow" and record["chooser_called"]       # staged did run, off to the side
    assert (record["legacy_choice"], record["staged_choice"]) == (top, rival)
    (observed,) = [r for r in router.rollout.records if r["event"] == "observed"]
    assert observed["tool"] == top and observed["shortlisted"]


def test_a_clarify_does_not_outlive_the_search_that_asked_it(staged_agent, chooser):
    assert "clarify" in _search(staged_agent, "close it")
    chooser.answer = {"decision": "ABSTAIN", "tool_id": None, "confidence": 0.6}
    assert "clarify" not in _search(staged_agent, "play some music")


def test_legacy_mode_never_asks(staged_agent, chooser):
    staged_agent._router.rollout.config = RO.Config(mode=RO.LEGACY)
    assert "clarify" not in _search(staged_agent, "close it") and chooser.seen == []
