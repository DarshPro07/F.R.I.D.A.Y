"""
Watching the router work, without letting it work.

Every routing number Friday has comes from a corpus this project wrote. The
store holds **four** real utterances, all long compound dictations from one
test - so the distribution that decides whether local execution is safe does
not exist yet, and the only place it exists is the boss talking to Friday.

What is proved here:

    it cannot act        by construction, and the counter that proves it
    it cannot be waited  on - the row is queued, and the queue overflows
                         rather than holding a turn up
    it keeps no          transcript, only a one-way fingerprint and routing
                         metadata
    production is not    ground truth. Comparison and correctness are separate
                         columns, and a router taught to match Gemini would
                         learn its mistakes
"""

from __future__ import annotations

import pytest

from friday import shadow as SH
from friday.store import Store


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "shadow.sqlite3")


@pytest.fixture(autouse=True)
def _shadow_mode(monkeypatch):
    monkeypatch.delenv("FRIDAY_REFLEX", raising=False)
    monkeypatch.delenv("FRIDAY_SHADOW", raising=False)
    monkeypatch.setenv(SH.ENV_MODE, SH.SHADOW)
    SH.reset()
    yield
    SH.reset()


def watch(text, store, **rest):
    return SH.observe(text, store=store, blocking=True, **rest)


# ---------------------------------------------------------------------------
# Three modes, not a boolean
# ---------------------------------------------------------------------------


def test_off_shadow_and_direct_are_different_states(monkeypatch):
    """
    Collapsing SHADOW into "on" is how a telemetry deployment becomes a
    production one by accident.
    """
    for value, observes, acts in ((SH.OFF, False, False),
                                  (SH.SHADOW, True, False),
                                  (SH.DIRECT, True, True)):
        monkeypatch.setenv(SH.ENV_MODE, value)
        assert SH.enabled() is observes, value
        assert SH.may_act() is acts, value


def test_shadow_never_grants_the_reflex_permission_to_act(monkeypatch):
    from friday import reflex as X

    monkeypatch.setenv(SH.ENV_MODE, SH.SHADOW)
    assert not X.enabled(), "shadow mode let the reflex path execute"


def test_nothing_happens_when_it_is_off(monkeypatch, store):
    monkeypatch.setenv(SH.ENV_MODE, SH.OFF)
    assert watch("pause the music", store) is None
    assert store.shadow_rows() == []


def test_the_old_flags_still_mean_something(monkeypatch):
    monkeypatch.delenv(SH.ENV_MODE, raising=False)
    monkeypatch.setenv("FRIDAY_SHADOW", "1")
    assert SH.mode() == SH.SHADOW
    assert not SH.may_act()


# ---------------------------------------------------------------------------
# It cannot act
# ---------------------------------------------------------------------------


def test_a_prediction_has_no_way_to_execute():
    """
    §3, and the reason it is a dataclass rather than an object with a runtime:
    there is no method that refuses, there is an absence. A method that
    refuses is a method somebody can be tempted to change.
    """
    prediction = SH.Prediction(predicted_capability="power_shutdown")

    for name in ("execute", "run", "call", "act", "invoke", "dispatch"):
        assert not hasattr(prediction, name), name
    with pytest.raises(Exception):
        prediction.predicted_capability = "apps_open"      # frozen


def test_a_prediction_carries_no_runtime_or_principal():
    prediction = SH.Prediction()
    values = list(vars(prediction).values())
    for value in values:
        assert isinstance(value, (str, int, float, bool)), value


def test_execution_attempts_stay_at_zero(store):
    for text in ("pause the music", "shut down the computer", "delete it"):
        watch(text, store)
    assert SH.attempted_to_act() == 0
    assert SH.status(store=store)["execution_attempts"] == 0


def test_reaching_for_execution_is_counted_and_refused():
    """The guard exists so the test above proves something."""
    before = SH.attempted_to_act()
    with pytest.raises(PermissionError):
        SH._no_execution("apps_open", {})
    assert SH.attempted_to_act() == before + 1


# ---------------------------------------------------------------------------
# It keeps no transcript
# ---------------------------------------------------------------------------


def test_the_sentence_is_never_stored(store):
    watch("pause the music by daft punk and open my tax return", store)
    row = store.shadow_rows()[0]

    for value in row.values():
        text = str(value).lower()
        for secret in ("daft punk", "tax return", "pause the music"):
            assert secret not in text, (secret, value)


def test_only_argument_names_are_kept_never_values(store):
    watch("open Paint", store)
    row = store.shadow_rows()[0]

    assert "paint" not in str(row.get("predicted_argument_shape") or "").lower()
    assert SH.argument_shape({"path": "C:/secret.txt", "name": "Paint"}) \
        == "name,path"


def test_the_fingerprint_is_stable_and_one_way():
    assert SH.fingerprint("Pause The Music") == SH.fingerprint("pause the music")
    assert SH.fingerprint("pause the music") != SH.fingerprint("stop the music")
    assert len(SH.fingerprint("anything")) == 16


def test_a_column_nobody_reviewed_cannot_be_written(store):
    """
    Telemetry does not grow a field by accident. A new column is a privacy
    decision and has to be made deliberately.
    """
    with pytest.raises(ValueError):
        store.record_shadow(fingerprint="x", decision="LOCAL",
                            raw_transcript="the boss said something")


# ---------------------------------------------------------------------------
# Production is not ground truth
# ---------------------------------------------------------------------------


def test_comparison_says_what_happened_not_who_was_right(store):
    watch("pause the music", store)
    assert SH.compare("pause the music", production_capability="music_stop",
                      store=store) == SH.DISAGREED

    row = store.shadow_rows()[0]
    assert row["comparison_status"] == SH.DISAGREED
    assert row["intent_correct"] is None, \
        "a disagreement was treated as proof the shadow was wrong"
    assert row["execution_correct"] is None
    assert row["label_source"] == SH.PRODUCTION_ROUTE_ONLY
    assert row["label_grounding"] == SH.WEAK_LABEL


def test_the_production_route_alone_is_the_weakest_label():
    assert SH.LABEL_STRENGTH[SH.PRODUCTION_ROUTE_ONLY] == (
        SH.WEAK_LABEL, SH.WEAK)
    assert SH.PRODUCTION_ROUTE_ONLY not in SH.GROUNDED


def test_strength_is_ordinal_not_a_guessed_probability():
    """
    This was a table of numbers - 0.95, 0.90, 0.75 - and they were guesses
    wearing the clothes of probabilities. Nobody had measured how often a
    verified ActionResult is right; 0.95 was a feeling, and a number like that
    gets multiplied into a promotion calculation by somebody who assumes it
    was derived.
    """
    for grounding, strength in SH.LABEL_STRENGTH.values():
        assert grounding in (SH.GROUNDED_LABEL, SH.WEAK_LABEL,
                             SH.UNGROUNDED_LABEL)
        assert strength in (SH.STRONG, SH.MEDIUM, SH.WEAK)
        assert not isinstance(strength, float)


def test_human_review_does_not_outrank_the_thing_actually_happening():
    """
    Which looks wrong and is not. A person adjudicating weeks later, from a
    fingerprint and a capability name, is working from less than the machine
    had at the time - and people mislabel.
    """
    assert SH.LABEL_STRENGTH[SH.HUMAN_REVIEW][1] == SH.MEDIUM
    assert SH.LABEL_STRENGTH[SH.VERIFIED_ACTION_RESULT][1] == SH.STRONG


def test_a_verified_action_settles_execution_and_not_intent(store):
    """
        "stop it"  ->  Friday resolves "it" to Chrome  ->  browser_close
                   ->  VERIFIED: Chrome really did close

    The verification is correct. The route was wrong. One column would score
    that row a success, and a router trained on it would learn to close
    browsers when told to stop things.
    """
    watch("pause the music", store)
    SH.compare("pause the music", production_capability="browser_close",
               action_status="succeeded", verified=True, store=store)

    row = store.shadow_rows()[0]
    assert row["execution_correct"] == 0, \
        "the verified route was browser_close and the shadow said otherwise"
    assert row["intent_correct"] is None, "a side effect was read as intent"
    assert row["label_source"] == SH.VERIFIED_ACTION_RESULT


def test_a_machine_cannot_settle_what_the_boss_meant(store):
    watch("pause the music", store)
    with pytest.raises(ValueError):
        SH.judge("pause the music", correct=True,
                 source=SH.VERIFIED_ACTION_RESULT, truth=SH.INTENT_TRUTH,
                 store=store)


def test_a_correction_can_settle_intent():
    assert SH.settles(SH.EXPLICIT_USER_CORRECTION, SH.INTENT_TRUTH)
    assert not SH.settles(SH.EXPLICIT_USER_CORRECTION, SH.EXECUTION_TRUTH)
    assert not SH.settles(SH.PRODUCTION_ROUTE_ONLY, SH.INTENT_TRUTH)


def test_reliability_is_measured_not_asserted(store):
    """
    What the guessed numbers were pretending to be. A rate computed from four
    rows is visibly a rate computed from four rows.
    """
    watch("pause the music", store)
    SH.compare("pause the music", production_capability="music_pause",
               store=store)
    SH.judge("pause the music", correct=True,
             source=SH.EXPLICIT_USER_CORRECTION, store=store)

    found = SH.observed_reliability(store=store)
    assert found[SH.EXPLICIT_USER_CORRECTION]["n"] == 1
    assert found[SH.EXPLICIT_USER_CORRECTION]["rate"] == "TOO_FEW"


def test_a_verified_outcome_can_settle_correctness(store):
    watch("pause the music", store)
    SH.compare("pause the music", production_capability="music_pause",
               action_status="succeeded", verified=True, store=store)

    row = store.shadow_rows()[0]
    assert row["label_source"] == SH.VERIFIED_ACTION_RESULT
    assert row["execution_correct"] == 1


def test_a_route_that_failed_proves_nothing_about_the_route(store):
    watch("pause the music", store)
    SH.compare("pause the music", production_capability="music_stop",
               action_status="failed", verified=False, store=store)

    settled = store.shadow_rows()[0]
    assert settled["execution_correct"] is None
    assert settled["intent_correct"] is None


@pytest.mark.parametrize("predicted,production,expected", [
    ("music_pause", "music_pause", SH.AGREED),
    ("music_pause", "music_stop", SH.DISAGREED),
    ("music_pause", "", SH.PRODUCTION_ABSTAINED),
    ("", "music_pause", SH.SHADOW_ABSTAINED),
    ("", "", SH.BOTH_ABSTAINED),
])
def test_every_pairing_has_a_neutral_name(predicted, production, expected):
    assert SH._comparison(predicted, production) == expected


def test_a_label_with_no_provenance_is_refused(store):
    with pytest.raises(ValueError):
        SH.judge("pause the music", correct=True, source="because I said so",
                 store=store)


# ---------------------------------------------------------------------------
# Corrections, the most valuable thing here
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "no, I meant the music",
    "I said pause the song",
    "wrong app",
    "not that one",
    "that's not what I wanted",
])
def test_a_correction_is_recognised(text):
    assert SH.looks_like_a_correction(text), text


@pytest.mark.parametrize("text", [
    "pause the music", "open Paint", "what is playing",
])
def test_an_ordinary_command_is_not_a_correction(text):
    assert not SH.looks_like_a_correction(text), text


def test_a_correction_keeps_the_shape_of_the_mistake_not_the_words(store):
    entry = SH.record_correction(
        "no, I meant the music",
        previous={"predicted_operation": "CONTROL",
                  "predicted_target": "SYSTEM",
                  "predicted_capability": "system_get_info"},
        store=store)

    assert entry["previous_capability"] == "system_get_info"
    assert entry["evidence"] == SH.EXPLICIT_USER_CORRECTION
    stored = store.routing_corrections()[0]
    for value in stored.values():
        assert "i meant" not in str(value).lower()


def test_one_correction_changes_nothing_by_itself(store):
    """
    §14. Shadow mode is data collection. It does not edit regexes, adjust
    thresholds or promote anything.
    """
    from friday import selective as SEL

    before = SEL.MARGIN
    SH.record_correction("no, I meant the music",
                         previous={"predicted_capability": "system_get_info"},
                         store=store)
    assert SEL.MARGIN == before


# ---------------------------------------------------------------------------
# It cannot slow Friday down
# ---------------------------------------------------------------------------


def test_the_queue_is_bounded():
    assert SH.QUEUE_DEPTH <= 1024


def test_an_overflowing_queue_drops_rather_than_blocks(monkeypatch, store):
    """
    A missing observation costs a data point. A stalled turn costs the boss
    his assistant.
    """
    import queue as queue_module

    class Full:
        def put_nowait(self, _row):
            raise queue_module.Full()

        def qsize(self):
            return SH.QUEUE_DEPTH

    monkeypatch.setattr(SH, "_QUEUE", Full())
    # The writer is "running": the queue is the only thing in the way.
    monkeypatch.setattr(SH, "_ensure_writer", lambda _store: True)

    prediction = SH.predict("pause the music")
    assert SH.record(prediction, store=store) is False
    assert SH.dropped() == 1


def test_a_broken_store_never_reaches_the_turn(store):
    class Broken:
        def record_shadow(self, **_):
            raise RuntimeError("disk on fire")

    assert SH.observe("pause the music", store=Broken(), blocking=True) \
        is not None      # the prediction still happened; only the write failed


def test_a_broken_router_never_reaches_the_turn(monkeypatch, store):
    from friday import selective as SEL

    monkeypatch.setattr(SEL, "decide",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    assert SH.predict("pause the music") is None


def test_the_live_hook_swallows_everything(monkeypatch):
    """`watch_from_the_side` is wrapped whole. A telemetry path may not cost
    the boss a reply under any circumstances at all."""
    import agent_friday

    monkeypatch.setattr(SH, "enabled",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    agent_friday.watch_from_the_side("pause the music")     # must not raise


# ---------------------------------------------------------------------------
# Versioning and retention
# ---------------------------------------------------------------------------


def test_every_row_is_stamped_with_what_produced_it(store):
    """
    Rows collected before and after a routing change are not comparable, and
    without this nobody can tell which is which six weeks later.
    """
    watch("pause the music", store)
    row = store.shadow_rows()[0]

    assert row["router_version"] == SH.ROUTER_VERSION
    assert row["taxonomy_version"] == SH.TAXONOMY_VERSION
    assert row["threshold_version"] == SH.THRESHOLD_VERSION


def test_telemetry_can_be_purged(store):
    for text in ("pause the music", "open Paint"):
        watch(text, store)
    assert SH.purge(store=store) == 2
    assert store.shadow_rows() == []


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_it_refuses_to_compute_precision_without_a_denominator(store):
    """
    Three correct out of three is not 100%, it is three. §15: do not report a
    misleading accuracy.
    """
    watch("pause the music", store)
    SH.compare("pause the music", production_capability="music_pause",
               action_status="succeeded", verified=True, store=store)

    assert SH.status(store=store)["real_precision"] == "UNMEASURED"


def test_the_spoken_report_is_one_sentence(store):
    watch("pause the music", store)
    said = SH.spoken(store=store)

    assert len(said.split(".")) <= 4
    assert "1" in said


def test_the_report_counts_coverage_on_real_turns(store):
    for text in ("pause the music", "why is my computer slow",
                 "should I restart my PC"):
        watch(text, store)

    found = SH.status(store=store)
    assert found["observed"] == 3
    assert found["local"] == 1
    assert found["abstained"] == 2
    assert found["real_coverage"] == pytest.approx(1 / 3, abs=0.01)
    assert found["mode"] == SH.SHADOW


def test_the_report_says_where_coverage_is_being_lost(store):
    for text in ("why is my computer slow", "should I restart my PC",
                 "open Chrome and find the news"):
        watch(text, store)

    blame = SH.status(store=store)["blame"]
    assert blame, "abstentions with no blame are a dead end for whoever "\
                  "tries to improve coverage later"
