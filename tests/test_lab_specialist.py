"""LAB-004: a specialist micro-model is trained offline from LAB-002's approved
dataset, judged against the current routing on the frozen set, and promoted
through the AdaptorLadder only on a strict win with no forgetting. It never
decides an approval, and a promoted one rolls back in one call.

Every test builds its own export directory, governor, ladder and stores, so
nothing depends on this machine."""
from __future__ import annotations

import ast
import hashlib
import json
import time
from pathlib import Path

import pytest

from friday import adaptor_promotion as AP
from friday import arena
from friday import confirmation as CF
from friday import experimental_local as EL
from friday import governor as G
from friday import lab_benchmark as LB
from friday import lab_dataset as LD
from friday import lab_specialist as LS
from friday import neural_fabric as NF

PROV = {"commit": "abc1234", "date": "2026-10-10T00:00:00+00:00"}
TOTAL_GB = 16.0
IDLE = G.Baseline(ram_available_gb=TOTAL_GB * 0.7, cpu_percent=10.0,
                  readings=12, provisional=False)

# The deterministic router calls files_delete CRITICAL and everything else
# SIMPLE, so web_search / shell_run (STANDARD) are what a specialist can win on.
BASE_TRAIN = [("web_search", "STANDARD", 8), ("shell_run", "STANDARD", 8),
              ("files_list", "SIMPLE", 8), ("files_delete", "CRITICAL", 8)]
BASE_EVAL = [("web_search", "STANDARD", 5), ("shell_run", "STANDARD", 6),
             ("files_list", "SIMPLE", 3), ("files_delete", "CRITICAL", 5)]
# routing: 8/19; BASE_TRAIN specialist: 19/19
SHELL_WRONG_TRAIN = [("web_search", "STANDARD", 8), ("shell_run", "CRITICAL", 8),
                     ("files_list", "SIMPLE", 8), ("files_delete", "CRITICAL", 8)]
# 13/19: strictly better than routing, wrong on every shell_run case
LIST_WRONG_TRAIN = [("web_search", "STANDARD", 8), ("shell_run", "STANDARD", 8),
                    ("files_list", "STANDARD", 8), ("files_delete", "CRITICAL", 8)]
# 16/19: beats both, but fails the 3 files_list cases routing and v1 passed
TIE_TRAIN = [("files_list", "SIMPLE", 8), ("files_delete", "CRITICAL", 8)]
TIE_EVAL = [("files_list", "SIMPLE", 5), ("files_delete", "CRITICAL", 5)]
WORSE_TRAIN = [("files_list", "STANDARD", 8), ("files_delete", "CRITICAL", 8)]


def rows(prefix, spec):
    out, i = [], 0
    for cap, label, count in spec:
        for _ in range(count):
            out.append({"objective_id": f"{prefix}{i:03d}",
                        "request_fingerprint": f"{prefix}f{i:03d}",
                        "steps": [{"capability": cap}], "evidence_method": "return_value",
                        "task_class": label, "data_class": "PUBLIC"})
            i += 1
    return out


def export(tmp_path, dirname, train_spec, eval_spec=BASE_EVAL):
    """A directory shaped exactly as lab_dataset.build writes it."""
    out = tmp_path / dirname
    out.mkdir()
    files = {}
    for fname, data in ((LD.TRAIN, rows("t", train_spec)), (LD.EVAL, rows("e", eval_spec))):
        blob = LD._dump(data)
        (out / fname).write_bytes(blob)
        files[fname] = {"sha256": hashlib.sha256(blob).hexdigest(), "rows": len(data)}
    (out / LD.MANIFEST).write_text(json.dumps({"version": 1, "files": files}),
                                   encoding="utf-8")
    return out


def sample(cpu=10.0, ram=30.0):
    return G.Sample(at=time.time(), cpu_percent=cpu, ram_percent=ram,
                    ram_available_gb=TOTAL_GB * (1 - ram / 100), disk_free_gb=100.0,
                    browser_processes=0, friday_rss_mb=200.0, ram_total_gb=TOTAL_GB)


class Dial:
    def __init__(self, s):
        self.s = s

    def __call__(self):
        return self.s


def make_lab(tmp_path, *, load=None):
    dial = Dial(load or sample())
    gov = G.Governor(thresholds=G.Thresholds(), sampler=dial, sample_ttl_s=0.0,
                     baseline=IDLE)
    ladder = AP.AdaptorLadder(tmp_path / "adaptors.sqlite3", floor=0.0, strict=True)
    store = arena.ArenaStore(tmp_path / "arena.json")
    lab = LS.SpecialistLab(tmp_path / "state", ladder=ladder, arena_store=store,
                           governor=gov, hardware="test-box", provenance=PROV)
    return lab, gov, ladder, store, dial


def eval_cases(rows_):
    return LB.cases_for(EL.TASK_CATEGORY, rows_)


def serving(lab, name, spec=BASE_EVAL):
    return [lab.classify(name, c) for c in eval_cases(rows("e", spec))]


def routing(spec=BASE_EVAL):
    return [LB.current_routing(c) for c in eval_cases(rows("e", spec))]


# -- the trainer ------------------------------------------------------------


def test_a_trainer_called_with_one_interaction_refuses():
    one = rows("t", [("web_search", "STANDARD", 1)])
    with pytest.raises(LS.TrainingRefused):
        LS.train_linear(one)
    with pytest.raises(LS.TrainingRefused):
        LS.train_linear(one[0])  # a bare interaction, not a dataset


def test_a_job_on_a_too_small_export_refuses_before_anything_is_registered(tmp_path):
    lab, gov, ladder, _, _ = make_lab(tmp_path)
    tiny = export(tmp_path, "tiny", [("web_search", "STANDARD", 1)])
    with pytest.raises(LS.TrainingRefused):
        lab.run_job("task-category", tiny, trigger=LS.CONTROLLER)
    assert ladder.listing() == []
    assert not (tmp_path / "state" / NF.STATE_FILE).exists()
    assert gov.active() == 0


def test_the_trained_artifact_is_plain_json_and_reproduces_the_classifier():
    clf = LB.LinearClassifier().fit(eval_cases(rows("t", BASE_TRAIN)))
    again = LB.LinearClassifier.from_dict(json.loads(json.dumps(clf.to_dict())))
    cases = eval_cases(rows("e", BASE_EVAL))
    assert [again.predict(c) for c in cases] == [clf.predict(c) for c in cases]


# -- promotion --------------------------------------------------------------


def test_a_candidate_equal_to_routing_stays_candidate_and_routing_is_unchanged(tmp_path):
    lab, _, ladder, _, _ = make_lab(tmp_path)
    out = lab.run_job("task-category", export(tmp_path, "tie", TIE_TRAIN, TIE_EVAL),
                      trigger=LS.CONTROLLER)
    assert out["status"] == "candidate" and "strict_win" in out["failed_gates"]
    assert out["accuracy"] == {"candidate": 1.0, "serving": 1.0}
    assert ladder.active("task-category") is None
    assert serving(lab, "task-category", TIE_EVAL) == routing(TIE_EVAL)
    with pytest.raises(NF.NotProduction):
        NF.NeuralFabric(tmp_path / "state").use(out["network"])


def test_a_candidate_worse_than_routing_stays_candidate_and_routing_is_unchanged(tmp_path):
    lab, _, ladder, _, _ = make_lab(tmp_path)
    out = lab.run_job("task-category", export(tmp_path, "worse", WORSE_TRAIN, TIE_EVAL),
                      trigger=LS.CONTROLLER)
    assert out["status"] == "candidate" and "strict_win" in out["failed_gates"]
    assert out["accuracy"]["candidate"] < out["accuracy"]["serving"]
    assert ladder.active("task-category") is None
    assert serving(lab, "task-category", TIE_EVAL) == routing(TIE_EVAL)


def test_a_strictly_better_candidate_promotes_with_the_stored_arena_report_id(tmp_path):
    lab, _, ladder, store, _ = make_lab(tmp_path)
    before = serving(lab, "task-category")
    out = lab.run_job("task-category", export(tmp_path, "v1", BASE_TRAIN),
                      trigger=LS.CONTROLLER)
    assert out["status"] == "promoted" and out["version"] == 1
    assert out["accuracy"]["candidate"] > out["accuracy"]["serving"]
    stored = store.report(out["report_id"])
    assert stored is not None and stored.result(AP.arena_id("task-category", 1)).passed
    assert ladder.active("task-category")["report_id"] == out["report_id"]
    assert NF.NeuralFabric(tmp_path / "state").use(out["network"])["kind"] == "linear"
    truth = [c.label for c in eval_cases(rows("e", BASE_EVAL))]
    assert serving(lab, "task-category") == truth != before


def test_a_candidate_that_fails_a_previously_passing_case_is_blocked(tmp_path):
    lab, _, ladder, _, _ = make_lab(tmp_path)
    out = lab.run_job("task-category", export(tmp_path, "forget", LIST_WRONG_TRAIN),
                      trigger=LS.CONTROLLER)
    # 16/19 against routing's 8/19, yet the 3 files_list cases routing passed are lost
    assert out["accuracy"]["candidate"] > out["accuracy"]["serving"]
    assert out["status"] == "candidate" and out["failed_gates"] == ["no_forgetting"]
    assert len(out["forgotten"]) == 3
    assert ladder.active("task-category") is None
    assert serving(lab, "task-category") == routing()


def test_forgetting_is_judged_against_the_serving_specialist_too(tmp_path):
    lab, _, ladder, _, _ = make_lab(tmp_path)
    first = lab.run_job("task-category", export(tmp_path, "v1", SHELL_WRONG_TRAIN),
                        trigger=LS.CONTROLLER)
    assert first["status"] == "promoted"
    held = serving(lab, "task-category")
    second = lab.run_job("task-category", export(tmp_path, "v2", LIST_WRONG_TRAIN),
                         trigger=LS.CONTROLLER)
    assert second["status"] == "candidate" and second["version"] == 2
    assert second["failed_gates"] == ["no_forgetting"]
    assert ladder.active("task-category")["version"] == 1
    assert serving(lab, "task-category") == held


def test_a_promoted_specialist_rolls_back_in_one_call(tmp_path):
    lab, _, ladder, _, _ = make_lab(tmp_path)
    assert lab.run_job("task-category", export(tmp_path, "v1", SHELL_WRONG_TRAIN),
                       trigger=LS.CONTROLLER)["status"] == "promoted"
    v1 = serving(lab, "task-category")
    out = lab.run_job("task-category", export(tmp_path, "v2", BASE_TRAIN),
                      trigger=LS.CONTROLLER)
    assert out["status"] == "promoted" and out["version"] == 2
    assert serving(lab, "task-category") != v1

    back = lab.rollback("task-category")
    assert back == {"status": "rolled_back", "from": 2, "to": 1}
    assert serving(lab, "task-category") == v1
    with pytest.raises(NF.NotProduction):
        NF.NeuralFabric(tmp_path / "state").use(out["network"])

    last = lab.rollback("task-category")
    assert last["to"] is None
    assert ladder.active("task-category") is None
    assert serving(lab, "task-category") == routing()
    assert lab.rollback("task-category")["status"] == "nothing_to_roll_back"


# -- training is a controller job ---------------------------------------------


def test_a_conversational_turn_cannot_start_training(tmp_path):
    lab, gov, ladder, _, _ = make_lab(tmp_path)
    src = export(tmp_path, "v1", BASE_TRAIN)
    for trigger in ("conversation", "voice_turn", "", "Controller"):
        with pytest.raises(LS.JobRefused):
            lab.run_job("task-category", src, trigger=trigger)
    assert ladder.listing() == [] and gov.active() == 0
    assert not (tmp_path / "state" / NF.STATE_FILE).exists()


def test_training_is_refused_while_the_governor_reports_pressure(tmp_path):
    lab, gov, ladder, _, _ = make_lab(tmp_path, load=sample(cpu=97))
    assert gov.pressure().level != G.NORMAL
    with pytest.raises(LS.JobRefused) as refused:
        lab.run_job("task-category", export(tmp_path, "v1", BASE_TRAIN),
                    trigger=LS.CONTROLLER)
    assert "pressure" in str(refused.value)
    assert ladder.listing() == []
    assert not (tmp_path / "state" / NF.STATE_FILE).exists()
    assert [j["status"] for j in lab.jobs()] == ["REFUSED"]


def test_a_job_is_recorded_with_its_hardware_and_releases_its_lease(tmp_path):
    lab, gov, _, _, _ = make_lab(tmp_path)
    out = lab.run_job("task-category", export(tmp_path, "v1", BASE_TRAIN),
                      trigger=LS.CONTROLLER)
    (job,) = lab.jobs()
    assert job["status"] == "COMPLETED" and job["trigger"] == LS.CONTROLLER
    assert job["hardware"] == "test-box" and job["version"] == out["version"]
    assert job["figure"]["hardware"] == "test-box" and job["figure"]["model"] == "linear"
    assert job["figure"]["seconds"] >= 0 and job["report_id"] == out["report_id"]
    assert gov.active() == 0


def test_a_failing_job_is_recorded_failed_and_releases_its_lease(tmp_path):
    lab, gov, _, _, _ = make_lab(tmp_path)
    src = export(tmp_path, "v1", BASE_TRAIN)
    (src / LD.TRAIN).write_bytes(b"{}\n")           # no longer what the manifest recorded
    with pytest.raises(LD.NotAnExport):
        lab.run_job("task-category", src, trigger=LS.CONTROLLER)
    assert gov.active() == 0
    assert lab.jobs()[-1]["status"] == "FAILED"


def test_a_job_after_a_failed_one_still_completes_with_the_next_ladder_version(tmp_path):
    # A refusal after the job is admitted (too small an export, a changed
    # manifest) must not leave anything that wedges the next job for that name.
    lab, gov, ladder, _, _ = make_lab(tmp_path)
    tiny = export(tmp_path, "tiny", [("files_list", "SIMPLE", 1)], [("files_list", "SIMPLE", 1)])
    with pytest.raises(Exception):
        lab.run_job("task-category", tiny, trigger=LS.CONTROLLER)
    assert lab.jobs()[-1]["status"] == "FAILED" and gov.active() == 0

    first = lab.run_job("task-category", export(tmp_path, "v1", BASE_TRAIN),
                        trigger=LS.CONTROLLER)
    assert first["status"] == "promoted" and first["version"] == 1
    second = lab.run_job("task-category", export(tmp_path, "v2", BASE_TRAIN),
                         trigger=LS.CONTROLLER)
    assert second["version"] == 2
    assert [j["status"] for j in lab.jobs()] == ["FAILED", "COMPLETED", "COMPLETED"]
    assert [r["version"] for r in ladder.listing()] == [1, 2]
    assert gov.active() == 0


def _train_file_changes_after_the_ladder_promotes(monkeypatch, ladder, src):
    """The ladder promotes, then the export's train file is edited before the
    fabric records the win, so the fabric refuses (DatasetChanged)."""
    promote = ladder.promote

    def promote_then_edit(*args, **kwargs):
        out = promote(*args, **kwargs)
        (src / LD.TRAIN).write_bytes(b"{}\n")
        return out

    monkeypatch.setattr(ladder, "promote", promote_then_edit)


def test_a_job_failing_after_the_ladder_promoted_leaves_routing_serving(tmp_path, monkeypatch):
    lab, gov, ladder, _, _ = make_lab(tmp_path)
    src = export(tmp_path, "v1", BASE_TRAIN)
    _train_file_changes_after_the_ladder_promotes(monkeypatch, ladder, src)
    with pytest.raises(NF.DatasetChanged):
        lab.run_job("task-category", src, trigger=LS.CONTROLLER)
    assert lab.jobs()[-1]["status"] == "FAILED" and gov.active() == 0
    assert ladder.active("task-category") is None
    assert [r["state"] for r in ladder.listing()] == ["ROLLED_BACK"]
    assert serving(lab, "task-category") == routing()

    monkeypatch.undo()
    out = lab.run_job("task-category", export(tmp_path, "v2", BASE_TRAIN),
                      trigger=LS.CONTROLLER)
    assert out["status"] == "promoted" and out["version"] == 2


def test_a_job_failing_after_the_ladder_promoted_restores_the_previous_specialist(
        tmp_path, monkeypatch):
    lab, _, ladder, _, _ = make_lab(tmp_path)
    assert lab.run_job("task-category", export(tmp_path, "v1", SHELL_WRONG_TRAIN),
                       trigger=LS.CONTROLLER)["status"] == "promoted"
    v1 = serving(lab, "task-category")
    src = export(tmp_path, "v2", BASE_TRAIN)
    _train_file_changes_after_the_ladder_promotes(monkeypatch, ladder, src)
    with pytest.raises(NF.DatasetChanged):
        lab.run_job("task-category", src, trigger=LS.CONTROLLER)
    assert ladder.active("task-category")["version"] == 1
    assert serving(lab, "task-category") == v1


# -- approvals are not the specialist's ---------------------------------------


def test_a_specialist_output_on_an_approval_is_ignored_and_the_confirmation_still_binds(tmp_path):
    lab, _, _, _, _ = make_lab(tmp_path)
    assert lab.run_job("task-category", export(tmp_path, "v1", BASE_TRAIN),
                       trigger=LS.CONTROLLER)["status"] == "promoted"
    case = eval_cases(rows("e", BASE_EVAL))[0]
    assert lab.advise("task-category", case, LS.PURPOSE_TASK_CATEGORY).used is True
    for purpose in ("approval", "confirmation", "approve_action", ""):
        advice = lab.advise("task-category", case, purpose)
        assert advice.used is False and advice.label is None

    book = CF.Book()
    pending = book.ask("run-1", "files.delete", "C:/x", "delete it?")
    lab.advise("task-category", case, "approval")           # nothing it says approves
    unapproved = book.consume(pending.nonce, run_id="run-1", action="files.delete",
                              target="C:/x")
    assert unapproved.ok is False
    assert book.approve(pending.nonce, answered_by="person").ok
    other = book.consume(pending.nonce, run_id="run-1", action="files.delete", target="C:/y")
    assert other.ok is False                                 # bound to the exact action
    assert book.consume(pending.nonce, run_id="run-1", action="files.delete",
                        target="C:/x").ok


def test_the_specialist_module_has_no_way_to_reach_the_confirmation_book():
    tree = ast.parse(Path(LS.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {f"{node.module}.{a.name}" for a in node.names} | {node.module}
    assert not {m for m in imported if "confirmation" in m}
