"""FAB-009: multi-model deliberation, run against deterministic stand-in models
(no provider call is made anywhere in this file)."""
import pytest

from friday import deliberation as d


class Stub:
    """A stand-in model. `ideas` are its round-1 proposals; `rejects` maps an
    idea it will reject to a reason; `revisions` maps a disputed idea to its
    replacement list (absent -> it insists on the same idea)."""

    def __init__(self, name, ideas, rejects=None, revisions=None):
        self.name = name
        self.ideas = list(ideas)
        self.rejects = dict(rejects or {})
        self.revisions = dict(revisions or {})
        self.seen_judging = []
        self.calls = 0

    def __call__(self, kind, question, items):
        self.calls += 1
        if kind == "propose":
            return list(self.ideas)
        if kind == "judge":
            self.seen_judging.extend(items)
            return [d.Judgment(i, i not in self.rejects,
                               self.rejects.get(i, "sound: " + i)) for i in items]
        if kind == "revise":
            out = []
            for idea, _reasons in items:
                out.extend(self.revisions.get(idea, [idea]))
            return out
        raise AssertionError(kind)


def test_both_endorse_ideas_and_nothing_is_left_open():
    a = Stub("a", ["cache the plan", "add a kill switch"])
    b = Stub("b", ["add a kill switch", "log every route"])
    r = d.deliberate("best ideas to add?", {"a": a, "b": b})
    assert r.status == d.CONVERGED and r.rounds == 1
    assert {i.idea for i in r.agreed} == {"cache the plan", "add a kill switch", "log every route"}
    assert r.disagreements == ()
    assert all(i.reasons for i in r.agreed if len(i.proposers) == 1)


def test_a_model_never_judges_its_own_idea():
    a = Stub("a", ["x"])
    b = Stub("b", ["y"])
    d.deliberate("q", {"a": a, "b": b})
    assert a.seen_judging == ["y"] and b.seen_judging == ["x"]


def test_a_rejected_idea_is_revised_and_then_endorsed():
    a = Stub("a", ["use a global lock"], revisions={"use a global lock": ["use a per-key lock"]})
    b = Stub("b", ["batch the writes"], rejects={"use a global lock": "serialises everything"})
    r = d.deliberate("q", {"a": a, "b": b})
    assert r.status == d.CONVERGED and r.rounds == 2
    assert {i.idea for i in r.agreed} == {"batch the writes", "use a per-key lock"}
    assert r.disagreements == ()


def test_no_convergence_within_the_round_limit_says_so():
    a = Stub("a", ["use a global lock", "batch the writes"])
    b = Stub("b", [], rejects={"use a global lock": "serialises everything"})
    r = d.deliberate("q", {"a": a, "b": b}, max_rounds=2)
    assert r.status == d.NOT_CONVERGED and r.rounds == 2
    assert [i.idea for i in r.agreed] == ["batch the writes"]
    (dis,) = r.disagreements
    assert dis.idea == "use a global lock" and dis.rejected_by == {"b": "serialises everything"}
    assert "not converge" in r.answer.lower() and "use a global lock" in r.answer


def test_total_rejection_never_invents_agreement():
    a = Stub("a", ["x"], rejects={"y": "no"})
    b = Stub("b", ["y"], rejects={"x": "no"})
    r = d.deliberate("q", {"a": a, "b": b}, max_rounds=2)
    assert r.status == d.NOT_CONVERGED and r.agreed == ()
    assert {i.idea for i in r.disagreements} == {"x", "y"}


def test_conceding_everything_is_not_agreement():
    a = Stub("a", ["x"], revisions={"x": []})
    b = Stub("b", [], rejects={"x": "no"})
    r = d.deliberate("q", {"a": a, "b": b})
    assert r.status == d.NOT_CONVERGED and r.agreed == ()


def test_with_three_models_one_rejection_blocks_agreement():
    a = Stub("a", ["x"])
    b = Stub("b", [])
    c = Stub("c", [], rejects={"x": "too risky"})
    r = d.deliberate("q", {"a": a, "b": b, "c": c}, max_rounds=1)
    assert r.status == d.NOT_CONVERGED and r.agreed == ()
    (dis,) = r.disagreements
    assert dis.endorsed_by == ("a", "b") and dis.rejected_by == {"c": "too risky"}


def test_the_same_model_twice_is_refused():
    s = Stub("a", ["x"])
    with pytest.raises(d.DeliberationError):
        d.deliberate("q", {"one": s, "two": s})
    with pytest.raises(d.DeliberationError):
        d.deliberate("q", {"A": Stub("a", []), "a": Stub("b", [])})


def test_fewer_than_two_or_too_many_models_is_refused():
    with pytest.raises(d.DeliberationError):
        d.deliberate("q", {"a": Stub("a", ["x"])})
    many = {str(n): Stub(str(n), []) for n in range(d.MAX_MODELS + 1)}
    with pytest.raises(d.DeliberationError):
        d.deliberate("q", many)


def test_round_limit_is_bounded():
    models = {"a": Stub("a", ["x"]), "b": Stub("b", ["y"])}
    for bad in (0, d.MAX_ROUNDS + 1):
        with pytest.raises(d.DeliberationError):
            d.deliberate("q", models, max_rounds=bad)


def test_a_judgment_without_a_reason_or_missing_an_idea_is_refused():
    class NoReason(Stub):
        def __call__(self, kind, question, items):
            if kind == "judge":
                return [d.Judgment(i, True, "  ") for i in items]
            return super().__call__(kind, question, items)

    class Skips(Stub):
        def __call__(self, kind, question, items):
            if kind == "judge":
                return []
            return super().__call__(kind, question, items)

    with pytest.raises(d.DeliberationError):
        d.deliberate("q", {"a": NoReason("a", ["x"]), "b": Stub("b", ["y"])})
    with pytest.raises(d.DeliberationError):
        d.deliberate("q", {"a": Skips("a", ["x"]), "b": Stub("b", ["y"])})


def test_the_call_budget_stops_the_run_without_inventing_agreement():
    a = Stub("a", ["x"])
    b = Stub("b", [], rejects={"x": "no"})
    r = d.deliberate("q", {"a": a, "b": b}, max_rounds=5, max_calls=4)
    assert r.status == d.NOT_CONVERGED and "budget" in r.reason
    assert r.calls <= 4 and a.calls + b.calls == r.calls
