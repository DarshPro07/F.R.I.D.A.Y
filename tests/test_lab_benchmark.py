"""
LAB-003: the narrow-task benchmark. Routing, a stdlib linear classifier and
MiniMind (a stub here, through LAB-001's fence) are measured on LAB-002's
frozen evaluation set; MiniMind is selected only by beating both.
"""
from __future__ import annotations

import json

import pytest

from friday import confirmation as CF
from friday import experimental_local as EL
from friday import lab_benchmark as LB
from friday import lab_dataset as LD
from friday.store import Store

PROV = {"commit": "abc1234", "date": "2026-10-10T00:00:00+00:00"}
LABELS = ("TRIVIAL", "STANDARD", "CRITICAL")


def case(i, features, label):
    return LB.Case(case_id=f"c{i:03d}", features=tuple(features), label=label,
                   data_class="PUBLIC")


def wrong_for(label):
    return next(x for x in LABELS if x != label)


def make_sets(n_eval=20):
    """Eval cases whose features are all alike but whose labels alternate, so
    no feature-based candidate can beat 50% on them."""
    train = [case(100 + i, ("files_list",), "STANDARD" if i % 2 else "TRIVIAL")
             for i in range(30)]
    ev = [case(i, ("files_list",), "STANDARD" if i % 2 else "TRIVIAL")
          for i in range(n_eval)]
    return train, ev


class Stub:
    """A MiniMind stand-in answering from a script, in eval order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return json.dumps({"label": self.answers.pop(0)})


def variant(name, answers, size_gb=0.2):
    return LB.MiniMind(name=name, size_gb=size_gb, generate=Stub(answers))


def run(variants, *, n_eval=20, routing=None, training=()):
    train, ev = make_sets(n_eval)
    return LB.run_task(EL.TASK_CATEGORY, train, ev, "e" * 64,
                       minimind=variants, labels=LABELS, routing=routing,
                       provenance=PROV, minimum_cases=10, training=training,
                       ram_probe=lambda: 1.5)


def truth(ev_count=20):
    return ["STANDARD" if i % 2 else "TRIVIAL" for i in range(ev_count)]


def test_report_lists_all_three_candidates_with_identical_case_ids_and_set_hash():
    rep = run([variant("minimind-3-64m", truth())], routing=lambda c: "STANDARD")
    names = {r.candidate_id for r in rep.arena.results}
    assert {"routing", "linear", "minimind-3-64m"} <= names
    assert rep.eval_sha256 == "e" * 64
    ids = [f"c{i:03d}" for i in range(20)]
    assert list(rep.case_ids) == ids
    for name in names:
        assert list(rep.candidates[name]["case_ids"]) == ids


def test_minimind_that_only_ties_the_linear_baseline_is_not_selected():
    train, ev = make_sets()
    linear = LB.LinearClassifier().fit(train)
    tie = [linear.predict(c) for c in ev]
    rep = run([variant("minimind-3-64m", tie)], routing=lambda c: wrong_for(c.label))
    mini = rep.arena.result("minimind-3-64m")
    assert mini.score == rep.arena.result("linear").score
    assert not mini.passed and "beats_linear" in mini.failed_gates
    assert rep.arena.winner == "linear"


def test_a_deliberately_worse_neural_stub_is_rejected():
    rep = run([variant("minimind-3-64m", [wrong_for(x) for x in truth()])],
              routing=lambda c: "STANDARD")
    mini = rep.arena.result("minimind-3-64m")
    assert not mini.passed and rep.arena.winner != "minimind-3-64m"


def test_minimind_that_beats_both_is_selected_from_the_stored_report(tmp_path):
    rep = run([variant("minimind-3-64m", truth())], routing=lambda c: "STANDARD")
    assert rep.arena.winner == "minimind-3-64m"
    LB.ReportStore(tmp_path / "reports.json").add(rep)
    sel = LB.select(EL.TASK_CATEGORY, LB.ReportStore(tmp_path / "reports.json"),
                    eval_sha256="e" * 64)
    assert sel.candidate == "minimind-3-64m" and sel.report_id == rep.report_id


def test_selection_refuses_a_stale_set_and_a_tampered_report(tmp_path):
    rep = run([variant("minimind-3-64m", truth())], routing=lambda c: "STANDARD")
    path = tmp_path / "reports.json"
    LB.ReportStore(path).add(rep)
    with pytest.raises(LB.SelectionRefused):
        LB.select(EL.TASK_CATEGORY, LB.ReportStore(path), eval_sha256="f" * 64)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["reports"][0]["arena"]["winner"] = "routing"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(LB.BenchmarkStoreError):
        LB.select(EL.TASK_CATEGORY, LB.ReportStore(path), eval_sha256="e" * 64)


def test_selection_without_a_report_is_refused(tmp_path):
    with pytest.raises(LB.SelectionRefused):
        LB.select(EL.TASK_CATEGORY, LB.ReportStore(tmp_path / "none.json"),
                  eval_sha256="e" * 64)


def test_selection_requires_the_set_hash_it_is_asked_about(tmp_path):
    rep = run([variant("minimind-3-64m", truth())], routing=lambda c: "STANDARD")
    store = LB.ReportStore(tmp_path / "reports.json")
    store.add(rep)
    with pytest.raises(TypeError):
        LB.select(EL.TASK_CATEGORY, store)
    with pytest.raises(LB.SelectionRefused):
        LB.select(EL.TASK_CATEGORY, store, eval_sha256="")

def test_dense_and_moe_variants_are_measured_separately():
    dense = variant("minimind-3-64m", truth(), size_gb=0.13)
    moe = variant("minimind-3-moe-198m", [wrong_for(x) for x in truth()], size_gb=0.4)
    ticks = iter(range(0, 10_000, 3))
    train, ev = make_sets()
    rep = LB.run_task(EL.TASK_CATEGORY, train, ev, "e" * 64, minimind=[dense, moe],
                      labels=LABELS, routing=lambda c: "STANDARD", provenance=PROV,
                      minimum_cases=10, ram_probe=lambda: 2.0,
                      clock=lambda: next(ticks) / 1000)
    for name, size in (("minimind-3-64m", 0.13), ("minimind-3-moe-198m", 0.4)):
        m = rep.arena.result(name).metrics
        assert m["size_gb"] == size and m["ram_gb"] == 2.0
        assert m["latency_ms"] == pytest.approx(3.0) and "accuracy" in m
    assert (rep.arena.result("minimind-3-64m").score
            != rep.arena.result("minimind-3-moe-198m").score)


def test_training_figures_carry_their_hardware():
    with pytest.raises(ValueError):
        LB.TrainingFigure(stage="pretrain", seconds=3600.0, hardware="", model="minimind-3-64m")
    with pytest.raises(ValueError):
        LB.TrainingFigure(stage="pretrain", seconds=3600.0, hardware="RTX 3090 24GB", model="")
    fig3090 = LB.TrainingFigure("pretrain", 3600.0, "RTX 3090 24GB", "minimind-3-64m")
    figlap = LB.TrainingFigure("sft", 7200.0, "laptop i7 16GB", "minimind-3-64m")
    rep = run([variant("minimind-3-64m", truth())], routing=lambda c: "STANDARD",
              training=[fig3090, figlap])
    assert {f.hardware for f in rep.training} == {"RTX 3090 24GB", "laptop i7 16GB"}
    assert [f.stage for f in LB.figures_for(rep, "laptop i7 16GB")] == ["sft"]
    back = LB.BenchmarkReport.from_dict(rep.to_dict())
    assert back.training == rep.training and back.report_id == rep.report_id


def test_a_training_figure_is_tied_to_the_variant_it_trained():
    dense = variant("minimind-3-64m", truth())
    moe = variant("minimind-3-moe-198m", truth())
    rep = run([dense, moe], routing=lambda c: "STANDARD", training=[
        LB.TrainingFigure("pretrain", 10.0, "gpu-a", "minimind-3-64m"),
        LB.TrainingFigure("pretrain", 30.0, "gpu-a", "minimind-3-moe-198m")])
    assert [f.seconds for f in LB.figures_for(rep, "gpu-a", model="minimind-3-moe-198m")] == [30.0]
    assert len(LB.figures_for(rep, "gpu-a")) == 2
    assert LB.BenchmarkReport.from_dict(rep.to_dict()).training == rep.training
    with pytest.raises(ValueError):  # a figure for a model that was not measured
        run([dense], routing=lambda c: "STANDARD", training=[
            LB.TrainingFigure("pretrain", 10.0, "gpu-a", "someone-else")])


@pytest.mark.parametrize("names", [["routing"], ["linear"], ["mm", "mm"], [""], ["  "]])
def test_variant_names_cannot_shadow_a_baseline_or_each_other(names):
    with pytest.raises(ValueError):
        run([variant(n, truth()) for n in names], routing=lambda c: "STANDARD")


def test_a_failing_ram_probe_propagates_instead_of_failing_the_model():
    def no_psutil():
        raise ImportError("psutil")
    train, ev = make_sets()
    with pytest.raises(ImportError):
        LB.run_task(EL.TASK_CATEGORY, train, ev, "e" * 64,
                    minimind=[variant("mm", truth())], labels=LABELS, provenance=PROV,
                    minimum_cases=10, ram_probe=no_psutil)


def test_a_refused_data_class_propagates_instead_of_failing_the_model():
    train, ev = make_sets()
    ev = [LB.Case(c.case_id, c.features, c.label, "WEIRD") for c in ev]
    with pytest.raises(EL.TaskRefused):
        LB.run_task(EL.TASK_CATEGORY, train, ev, "e" * 64,
                    minimind=[variant("mm", truth())], labels=LABELS, provenance=PROV,
                    minimum_cases=10, ram_probe=lambda: 1.0)


def test_rerun_on_an_unchanged_set_reproduces_the_winner():
    a = run([variant("minimind-3-64m", truth())], routing=lambda c: "STANDARD")
    b = run([variant("minimind-3-64m", truth())], routing=lambda c: "STANDARD")
    assert a.arena.winner == b.arena.winner and a.case_ids == b.case_ids


def test_a_model_output_outside_the_labels_counts_as_wrong_not_as_a_crash():
    rep = run([variant("minimind-3-64m", ["NONSENSE"] * 20)], routing=lambda c: "STANDARD")
    mini = rep.arena.result("minimind-3-64m")
    assert mini.metrics["rejected"] == 20 and mini.score == 0.0


def test_a_model_that_raises_fails_its_own_candidate_only():
    def boom(prompt):
        raise RuntimeError("model gone")
    rep = run([LB.MiniMind("minimind-3-64m", 0.1, boom)], routing=lambda c: "STANDARD")
    assert rep.arena.result("minimind-3-64m").failed_gates == ("scored",)
    assert rep.candidates["minimind-3-64m"]["failure"] == "RuntimeError"
    assert rep.arena.result("routing").passed


def test_the_default_routing_is_the_deterministic_task_classifier():
    c = case(1, ("files_delete", "method:return_value"), "CRITICAL")
    assert LB.current_routing(c) == "CRITICAL"


def test_linear_classifier_is_deterministic_and_learns_separable_data():
    train = ([case(i, ("a",), "X") for i in range(5)]
             + [case(9 + i, ("b",), "Y") for i in range(5)])
    one = LB.LinearClassifier().fit(train)
    two = LB.LinearClassifier().fit(list(train))
    probe = [case(50, ("a",), "X"), case(51, ("b",), "Y")]
    assert [one.predict(c) for c in probe] == ["X", "Y"] == [two.predict(c) for c in probe]


def test_unsupported_narrow_task_is_named_not_faked():
    with pytest.raises(LB.UnsupportedTask):
        LB.cases_for(EL.TEAM_ACTIVATION, [])


# -- the real frozen export ---------------------------------------------------


def seed(store, run_id, capability, task_class):
    store.open_objective_run(run_id, request=f"do {run_id}", objective_summary=run_id)
    store.touch_objective_run(run_id, task_class=task_class)
    store.save_objective_task(task_id=f"{run_id}-t1", run_id=run_id,
                              capability=capability, arguments="{}", status="SUCCEEDED")
    store.append_objective_evidence(run_id, expected="ok", actual="ok",
                                    method="return_value", passed=True,
                                    task_id=f"{run_id}-t1")
    store.finish_objective_run(run_id, status="COMPLETED", summary={})


def test_cases_come_from_the_frozen_export_and_its_hash(tmp_path):
    store = Store(tmp_path / "lab.db")
    book = CF.StoredBook(store=lambda: store, instance="t")
    for i in range(60):
        cap, cls = (("files_delete", "CRITICAL") if i % 2 else ("files_list", "STANDARD"))
        seed(store, f"run-{i:03d}", cap, cls)
    out = tmp_path / "export"
    c = book.ask(LD.APPROVAL_RUN, LD.APPROVAL_ACTION, LD.approval_target(out), "export?")
    assert book.approve(c.nonce, answered_by="owner").ok
    manifest = LD.build(store, out, approval=c.nonce, book=book)
    export = LB.load_export(out)
    assert export.eval_sha256 == manifest["files"][LD.EVAL]["sha256"]
    assert len(export.eval_rows) == manifest["counts"]["eval"] > 0
    eval_cases = LB.cases_for(EL.TASK_CATEGORY, export.eval_rows)
    rep = LB.run_task(EL.TASK_CATEGORY, LB.cases_for(EL.TASK_CATEGORY, export.train_rows),
                      eval_cases, export.eval_sha256,
                      minimind=[variant("minimind-3-64m", [c.label for c in eval_cases])],
                      labels=("CRITICAL", "STANDARD"), provenance=PROV,
                      minimum_cases=1, ram_probe=lambda: 1.0)
    assert rep.eval_sha256 == manifest["files"][LD.EVAL]["sha256"]
    assert rep.arena.result("linear").score == 1.0
    # the MiniMind path succeeds on the real exported rows (real data classes pass the fence)
    assert "scored" not in rep.arena.result("minimind-3-64m").failed_gates
    assert rep.arena.result("minimind-3-64m").metrics["rejected"] == 0.0
    ids = rep.candidates["routing"]["case_ids"]
    assert ids == rep.candidates["linear"]["case_ids"] == rep.candidates["minimind-3-64m"]["case_ids"]
    assert list(rep.case_ids) == ids == [c.case_id for c in eval_cases]
    # a changed evaluation file is refused
    (out / LD.EVAL).write_bytes(b"")
    with pytest.raises(LD.EvalSetChanged):
        LB.load_export(out)
