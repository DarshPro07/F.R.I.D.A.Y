"""ULTRA-005: ultracode verification templates over adversarial.py. A stub
gateway answers every actor, so no model is called and the call order is
asserted from the record."""
from __future__ import annotations

import pytest

from friday import adversarial as A
from friday.ultracode import templates as T

REFUTES = "VERDICT: DISPUTED\nFINDING: the retry path in queue.py is never reached\nCONFIDENCE: 80"
HOLDS = "VERDICT: CONFIRMED\nFINDING: NONE\nCONFIDENCE: 70"
COMPLETE = "VERDICT: COMPLETE\nMISSING: NONE\nCONFIDENCE: 75"

CLAIM = "The queue drains on shutdown."
EVIDENCE = ["queue.py:40 calls drain() in close()", "no test covers close()"]


class Gateway:
    """One call log shared by every actor, so ordering across actors is visible."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []     # (actor, worker, user)
        self.systems: list[str] = []                    # the system prompt of each call

    def actor(self, name: str, model: str, answer, *, session: str | None = None) -> T.Actor:
        def infer(system, user, *, worker, objective_id):
            self.calls.append((name, worker, user))
            self.systems.append(system)
            reply = answer(worker, user) if callable(answer) else answer
            if isinstance(reply, Exception):
                raise reply
            return reply
        return T.Actor(name=name, model=model, session=session or f"sess-{name}", infer=infer)

    @property
    def order(self) -> list[str]:
        return [name for name, _, _ in self.calls]


@pytest.fixture
def gw() -> Gateway:
    return Gateway()


@pytest.fixture
def producer(gw) -> T.Actor:
    return gw.actor("producer", "sonnet", "unused")


def _skeptics(gw, *answers):
    return [gw.actor(f"skeptic{i}", "haiku", a) for i, a in enumerate(answers, 1)]


# -- skeptics: a majority refuting kills the claim ---------------------------


def test_two_of_three_refuting_skeptics_drop_the_claim(gw, producer):
    report = T.skeptic_vote(CLAIM, EVIDENCE, producer=producer,
                            skeptics=_skeptics(gw, REFUTES, REFUTES, HOLDS))
    assert report.status == T.KILLED
    assert (report.refuted, report.held, report.unavailable) == (2, 1, 0)
    # The reasons survive on the record; the dissent is kept too.
    assert report.checks[0].findings == ("the retry path in queue.py is never reached",)
    assert report.checks[2].verdict == A.CONFIRMED


def test_one_refuting_skeptic_does_not_drop_the_claim(gw, producer):
    report = T.skeptic_vote(CLAIM, EVIDENCE, producer=producer,
                            skeptics=_skeptics(gw, REFUTES, HOLDS, HOLDS))
    assert report.status == T.SURVIVED
    assert report.refuted == 1


def test_a_claim_nobody_could_check_is_unverified_not_passed(gw, producer):
    report = T.skeptic_vote(CLAIM, EVIDENCE, producer=producer, skeptics=_skeptics(
        gw, HOLDS, RuntimeError("gateway down"), TimeoutError("slow")))
    assert report.status == T.UNVERIFIED
    assert report.unavailable == 2 and report.held == 1
    assert "gateway down" in report.checks[1].error


def test_a_skeptic_that_confirms_with_findings_is_counted_as_refuting(gw, producer):
    both = "VERDICT: CONFIRMED\nFINDING: close() is never called on SIGTERM\nCONFIDENCE: 60"
    report = T.skeptic_vote(CLAIM, EVIDENCE, producer=producer,
                            skeptics=_skeptics(gw, both, both, HOLDS))
    assert report.status == T.KILLED


# -- isolation: a verifier never shares the producer's model or session -------


def test_a_verifier_on_the_producers_model_is_rejected_before_any_call(gw, producer):
    same_model = gw.actor("skeptic2", "Sonnet ", HOLDS)       # case/space-insensitive
    with pytest.raises(T.IsolationError, match="producer's model"):
        T.skeptic_vote(CLAIM, EVIDENCE, producer=producer,
                       skeptics=[*_skeptics(gw, HOLDS), same_model])
    assert gw.calls == []


def test_a_verifier_in_the_producers_session_is_rejected(gw, producer):
    same_session = gw.actor("skeptic1", "haiku", HOLDS, session="sess-producer")
    with pytest.raises(T.IsolationError, match="producer's session"):
        T.skeptic_vote(CLAIM, EVIDENCE, producer=producer, skeptics=[same_session])
    assert gw.calls == []


def test_an_unstated_model_cannot_prove_independence(gw, producer):
    unknown = gw.actor("skeptic1", "", HOLDS)
    with pytest.raises(T.IsolationError, match="must be stated"):
        T.skeptic_vote(CLAIM, EVIDENCE, producer=producer, skeptics=[unknown])


# -- perspective-diverse verifiers -------------------------------------------


def test_any_disputing_perspective_fails_the_claim(gw, producer):
    report = T.perspective_check(CLAIM, EVIDENCE, producer=producer, perspectives={
        "correctness": gw.actor("v1", "haiku", HOLDS),
        "security": gw.actor("v2", "opus-old", REFUTES),
        "regression": gw.actor("v3", "haiku", HOLDS)})
    assert report.status == A.DISPUTED
    assert [c.perspective for c in report.checks] == ["correctness", "security", "regression"]
    # Same claim and evidence for all; each verifier is told only its own angle.
    assert len({user for _, _, user in gw.calls}) == 1
    assert "security" in gw.systems[1] and "security" not in gw.systems[0]


def test_perspectives_must_differ(gw, producer):
    with pytest.raises(ValueError, match="perspectives must differ"):
        T.perspective_check(CLAIM, EVIDENCE, producer=producer, perspectives={
            "Security": gw.actor("v1", "haiku", HOLDS),
            "security": gw.actor("v2", "haiku", HOLDS)})


# -- the loop ends after K dry rounds ----------------------------------------


def _finder(rounds):
    seen_rounds: list[int] = []

    def find(round_no, found):
        seen_rounds.append(round_no)
        return rounds[round_no - 1] if round_no <= len(rounds) else ["never-reached"]
    find.rounds = seen_rounds
    return find


def test_k_equals_two_stops_after_exactly_two_dry_rounds():
    find = _finder([["a", "b"], ["c"], ["A "], ["b"], ["z"]])
    result = T.loop_until_dry(find, k=2, max_rounds=10)
    # Rounds 3 and 4 add nothing new (case/space-insensitive), so round 5 never runs.
    assert result.items == ("a", "b", "c")
    assert (result.rounds, result.stopped) == (4, "dry")
    assert find.rounds == [1, 2, 3, 4]


def test_a_loop_with_nothing_new_from_the_start_stops_after_two_rounds():
    find = _finder([[], [], ["late"]])
    result = T.loop_until_dry(find, k=2, max_rounds=10)
    assert result.items == () and result.rounds == 2 and result.stopped == "dry"


def test_a_productive_round_resets_the_dry_count():
    find = _finder([["a"], [], ["b"], [], []])
    result = T.loop_until_dry(find, k=2, max_rounds=10)
    assert result.items == ("a", "b") and result.rounds == 5


def test_a_loop_that_never_dries_ends_at_its_cap_and_says_so():
    result = T.loop_until_dry(lambda n, found: [f"item{n}"], k=2, max_rounds=3)
    assert (result.rounds, result.stopped) == (3, "cap")
    assert result.items == ("item1", "item2", "item3")


def test_a_loop_that_can_never_dry_is_refused():
    with pytest.raises(ValueError):
        T.loop_until_dry(lambda n, found: [], k=3, max_rounds=2)
    with pytest.raises(ValueError):
        T.loop_until_dry(lambda n, found: [], k=0, max_rounds=2)


# -- the completeness critic runs last ---------------------------------------


def test_the_critic_runs_last_and_sees_what_the_others_found(gw, producer):
    report = T.verify(
        CLAIM, EVIDENCE, producer=producer,
        skeptics=_skeptics(gw, HOLDS, HOLDS, HOLDS),
        perspectives={"correctness": gw.actor("v1", "haiku", HOLDS),
                      "security": gw.actor("v2", "haiku", HOLDS)},
        critic=gw.actor("critic", "opus-old", COMPLETE))
    assert gw.order == ["skeptic1", "skeptic2", "skeptic3", "v1", "v2", "critic"]
    assert gw.calls[-1][1] == "critic:critic"
    assert "skeptics: SURVIVED" in gw.calls[-1][2]
    assert "security: CONFIRMED" in gw.calls[-1][2]
    assert report.passed and report.skipped == ()


def test_an_incomplete_verdict_fails_a_claim_every_verifier_confirmed(gw, producer):
    report = T.verify(
        CLAIM, EVIDENCE, producer=producer, skeptics=_skeptics(gw, HOLDS, HOLDS),
        critic=gw.actor("critic", "opus-old",
                        "VERDICT: COMPLETE\nMISSING: no check of the SIGTERM path\nCONFIDENCE: 50"))
    assert report.critic.verdict == T.INCOMPLETE          # the missing item wins
    assert report.critic.missing == ("no check of the SIGTERM path",)
    assert not report.passed


def test_a_killed_claim_skips_the_rest_and_names_what_it_skipped(gw, producer):
    report = T.verify(
        CLAIM, EVIDENCE, producer=producer, skeptics=_skeptics(gw, REFUTES, REFUTES, HOLDS),
        perspectives={"correctness": gw.actor("v1", "haiku", HOLDS)},
        critic=gw.actor("critic", "opus-old", COMPLETE))
    assert report.skeptics.status == T.KILLED
    assert report.skipped == ("perspectives", "critic")
    assert "v1" not in gw.order and "critic" not in gw.order
    assert not report.passed


def test_a_critic_on_the_producers_model_rejects_the_run_before_any_call(gw, producer):
    with pytest.raises(T.IsolationError):
        T.verify(CLAIM, EVIDENCE, producer=producer, skeptics=_skeptics(gw, HOLDS),
                 critic=gw.actor("critic", "sonnet", COMPLETE))
    assert gw.calls == []


def test_verify_refuses_a_run_with_nothing_to_check_it(gw, producer):
    with pytest.raises(ValueError, match="nothing"):
        T.verify(CLAIM, EVIDENCE, producer=producer,
                 critic=gw.actor("critic", "haiku", COMPLETE))


# -- judge panel and FAB-009 deliberation ------------------------------------


def _panel_answers(*, unresolved="NONE", objection="NONE"):
    def answer(worker, user):
        role = worker.split(":", 1)[1]
        return {
            A.PROPOSER: "POSITION: adopt\nARGUMENT: simple\nEVIDENCE: [E1]\nCONFIDENCE: 80",
            A.CONTRARIAN: f"POSITION: no\nOBJECTION: {objection}\nCONFIDENCE: 50",
            A.FAILURE_ANALYST: "FAILURE: NONE\nEARLY_SIGNAL: NONE\nCONFIDENCE: 50",
            A.EVIDENCE_CHECKER: "SUPPORTED: [E1]\nMISSING: NONE\nCONFIDENCE: 70",
            A.JUDGE: ("VERDICT: ADOPT\nREASON: the attempts agree on a drain in close().\n"
                      f"UNRESOLVED: {unresolved}\nUNCERTAINTY: NONE\nCONFIDENCE: 72"),
        }[role]
    return answer


def _attempt(text):
    return f"ANSWER: {text}\nCONFIDENCE: 60"


def _synthesis(worker, user):
    # The judge actor answers both the panel roles and the synthesis.
    if worker.startswith("synthesis:"):
        return "SYNTHESIS: drain in close(), and test SIGTERM\nCONFIDENCE: 70"
    return _panel_answers(unresolved="whether SIGTERM reaches close()")(worker, user)


def test_a_judge_panel_runs_independent_attempts_then_synthesises(gw):
    a = gw.actor("alpha", "gemini", _attempt("drain in close()"))
    b = gw.actor("beta", "gpt", _attempt("drain in an atexit hook"))
    judge = gw.actor("judge", "opus-old", _synthesis)
    result = T.judge_panel("How should the queue drain?", EVIDENCE, producers=[a, b], judge=judge)

    # Attempts are independent: neither saw the other's answer.
    attempt_calls = [(n, u) for n, w, u in gw.calls if w.startswith("attempt:")]
    assert [n for n, _ in attempt_calls] == ["alpha", "beta"]
    assert all("atexit" not in u and "drain in close()" not in u for _, u in attempt_calls)
    # The panel then judged both attempts, and the judge wrote one synthesis last.
    assert gw.order == ["alpha", "beta"] + ["judge"] * 6
    assert gw.calls[-1][1] == "synthesis:judge"
    assert "drain in an atexit hook" in gw.calls[-2][2]
    assert isinstance(result.decision, A.Decision)
    assert result.synthesis == "drain in close(), and test SIGTERM"
    assert [x.model for x in result.attempts] == ["gemini", "gpt"]


def test_deliberation_returns_the_verdict_and_the_remaining_disagreements(gw):
    providers = [gw.actor("alpha", "gemini", _attempt("drain in close()")),
                 gw.actor("beta", "gpt", _attempt("drain in an atexit hook"))]
    judge = gw.actor("judge", "opus-old", _synthesis)
    result = T.deliberation("How should the queue drain?", EVIDENCE,
                            providers=providers, judge=judge)
    assert result.verdict == "ADOPT"
    assert result.disagreements == ("whether SIGTERM reaches close()",)
    assert not result.converged          # it says so instead of inventing agreement


def test_a_deliberation_with_nothing_left_open_has_converged(gw):
    providers = [gw.actor("alpha", "gemini", _attempt("drain in close()")),
                 gw.actor("beta", "gpt", _attempt("drain in close()"))]
    judge = gw.actor("judge", "opus-old", lambda w, u: (
        "SYNTHESIS: drain in close()" if w.startswith("synthesis:")
        else _panel_answers()(w, u)))
    result = T.deliberation("How should the queue drain?", EVIDENCE,
                            providers=providers, judge=judge)
    assert result.verdict == "ADOPT" and result.disagreements == () and result.converged


def test_a_deliberation_needs_distinct_providers(gw):
    judge = gw.actor("judge", "opus-old", _synthesis)
    same = [gw.actor("alpha", "gemini", _attempt("x")), gw.actor("beta", "Gemini", _attempt("y"))]
    with pytest.raises(T.IsolationError, match="distinct providers"):
        T.deliberation("q", EVIDENCE, providers=same, judge=judge)
    assert gw.calls == []
    # A plain judge panel may sample one model twice, but never in one session.
    T.judge_panel("q", EVIDENCE, producers=same, judge=judge)
    one_session = [gw.actor("a", "gemini", _attempt("x"), session="s"),
                   gw.actor("b", "gemini", _attempt("y"), session="s")]
    with pytest.raises(T.IsolationError, match="independent sessions"):
        T.judge_panel("q", EVIDENCE, producers=one_session, judge=judge)


def test_a_judge_on_an_attempts_model_is_rejected(gw):
    producers = [gw.actor("alpha", "gemini", _attempt("x")), gw.actor("beta", "gpt", _attempt("y"))]
    with pytest.raises(T.IsolationError, match="producer's model"):
        T.judge_panel("q", EVIDENCE, producers=producers,
                      judge=gw.actor("judge", "gpt", _synthesis))
    assert gw.calls == []


def test_fewer_than_two_available_attempts_is_unavailable_and_no_panel_runs(gw):
    producers = [gw.actor("alpha", "gemini", _attempt("x")),
                 gw.actor("beta", "gpt", RuntimeError("provider unavailable"))]
    result = T.judge_panel("q", EVIDENCE, producers=producers,
                           judge=gw.actor("judge", "opus-old", _synthesis))
    assert result.verdict == "UNAVAILABLE" and result.decision is None
    assert "1 of 2" in result.reason
    assert gw.order == ["alpha", "beta"]
