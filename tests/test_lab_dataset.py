"""
LAB-002: the privacy-safe training dataset built from verified task history.

One test per acceptance clause. The history is a real Store with real
objective rows; the owner's approval is a real StoredBook confirmation.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from friday import confirmation as CF
from friday import lab_dataset as LD
from friday.store import Store

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8"      # token shape for the observability redactor


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "lab.db")


@pytest.fixture
def book(store):
    return CF.StoredBook(store=lambda: store, instance="test-instance")


def seed(store, run_id, *, request="tidy the downloads folder", status="COMPLETED",
         passed=True, evidence=True, approvals=(), arguments="{}", task_class="",
         method="return_value", summary=None):
    store.open_objective_run(run_id, request=request,
                             objective_summary=summary or request)
    if task_class:
        store.touch_objective_run(run_id, task_class=task_class)
    store.save_objective_task(task_id=f"{run_id}-t1", run_id=run_id,
                              capability="files_list", arguments=arguments,
                              status="SUCCEEDED" if status == "COMPLETED" else "FAILED")
    if evidence:
        store.append_objective_evidence(run_id, expected="listed", actual="listed",
                                        method=method, passed=passed,
                                        task_id=f"{run_id}-t1")
    for decision in approvals:
        store.append_objective_approval(run_id, operation="files_delete", target="x",
                                        parameters={}, decision=decision,
                                        decided_by="owner")
    if status == "COMPLETED" and (not evidence or not passed):
        store.touch_objective_run(run_id, status="COMPLETED")   # said, not proven
    else:
        store.finish_objective_run(run_id, status=status, summary={})


def approve(book, out_dir, *, answer=True):
    c = book.ask(LD.APPROVAL_RUN, LD.APPROVAL_ACTION, LD.approval_target(out_dir),
                 "Export the training dataset?")
    if answer:
        assert book.approve(c.nonce, answered_by="owner").ok
    return c.nonce


def build(store, book, out_dir):
    return LD.build(store, out_dir, approval=approve(book, out_dir), book=book)


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def all_rows(out):
    return rows(out / "train.jsonl") + rows(out / "eval.jsonl")


def id_where(want_eval, prefix="r"):
    for i in range(500):
        rid = f"{prefix}{i}"
        if LD.is_eval_id(rid) == want_eval:
            return rid
    raise AssertionError("no id found")


# -- admission -------------------------------------------------------------


def test_unverified_failed_or_unapproved_records_never_enter(store, book, tmp_path):
    seed(store, "good")
    seed(store, "nowitness", evidence=False)                 # COMPLETED, no evidence
    seed(store, "failedev", passed=False)                    # evidence says no
    seed(store, "failed", status="FAILED")
    seed(store, "denied", approvals=("APPROVED", "DENIED"))
    seed(store, "pending", approvals=("PENDING",))
    out = tmp_path / "out"
    manifest = build(store, book, out)
    assert [r["objective_id"] for r in all_rows(out)] == ["good"]
    assert manifest["counts"]["admitted"] == 1
    assert manifest["counts"]["excluded"] == {"failed": 1, "unapproved": 2,
                                              "unverified": 2}


def test_an_approved_run_is_admitted(store, book, tmp_path):
    seed(store, "ok", approvals=("APPROVED", "APPROVED_ONCE"))
    out = tmp_path / "out"
    build(store, book, out)
    assert [r["objective_id"] for r in all_rows(out)] == ["ok"]


@pytest.mark.parametrize("text", [
    "call me at 555-234-6789",                       # PERSONAL
    "card 4111 1111 1111 1111",                      # CUSTOMER_PII
    "CONFIDENTIAL roadmap",                          # CONFIDENTIAL
    "password: hunter2hunter2hunter2",               # SECRET
    "keep this LOCAL_ONLY",                          # LOCAL_ONLY
])
def test_sensitive_classes_are_dropped_and_counted(store, book, tmp_path, text):
    seed(store, "clean")
    seed(store, "dirty", arguments=json.dumps({"note": text}))
    out = tmp_path / "out"
    manifest = build(store, book, out)
    assert [r["objective_id"] for r in all_rows(out)] == ["clean"]
    dropped = manifest["counts"]["dropped_by_class"]
    assert sum(dropped.values()) == 1
    assert next(iter(dropped)) in ("PERSONAL", "CUSTOMER_PII", "CONFIDENTIAL",
                                   "SECRET", "LOCAL_ONLY")


def test_the_request_text_is_classified_too(store, book, tmp_path):
    seed(store, "dirty", request="CONFIDENTIAL: rename the board deck")
    manifest = build(store, book, tmp_path / "out")
    assert manifest["counts"]["dropped_by_class"] == {"CONFIDENTIAL": 1}


def test_internal_records_are_admitted_with_their_class(store, book, tmp_path):
    seed(store, "int", arguments=json.dumps({"note": "INTERNAL tooling"}))
    out = tmp_path / "out"
    build(store, book, out)
    assert [r["data_class"] for r in all_rows(out)] == ["INTERNAL"]


# -- provenance -------------------------------------------------------------


def test_every_row_carries_provenance(store, book, tmp_path):
    for i in range(6):
        seed(store, f"run{i}", request=f"request number {i}")
    out = tmp_path / "out"
    build(store, book, out)
    got = all_rows(out)
    assert len(got) == 6
    for row in got:
        assert row["objective_id"].startswith("run")
        assert row["outcome_evidence_id"] == f"{row['objective_id']}:evidence:1"
        assert row["data_class"] in ("PUBLIC", "INTERNAL")


# -- approval ---------------------------------------------------------------


def test_an_export_without_a_recorded_owner_approval_is_refused(store, book, tmp_path):
    seed(store, "good")
    out = tmp_path / "out"
    with pytest.raises(LD.ExportRefused):
        LD.build(store, out, approval="", book=book)
    with pytest.raises(LD.ExportRefused):
        LD.build(store, out, approval="not-a-nonce", book=book)
    with pytest.raises(LD.ExportRefused):                     # asked, never answered
        LD.build(store, out, approval=approve(book, out, answer=False), book=book)
    assert not out.exists()


def test_an_approval_for_another_directory_does_not_carry_over(store, book, tmp_path):
    seed(store, "good")
    nonce = approve(book, tmp_path / "elsewhere")
    with pytest.raises(LD.ExportRefused):
        LD.build(store, tmp_path / "out", approval=nonce, book=book)


def test_each_export_needs_its_own_approval(store, book, tmp_path):
    seed(store, "good")
    out = tmp_path / "out"
    nonce = approve(book, out)
    LD.build(store, out, approval=nonce, book=book)
    with pytest.raises(LD.ExportRefused):
        LD.build(store, out, approval=nonce, book=book)


# -- the frozen evaluation set ---------------------------------------------


def seed_with_eval(store, count=8):
    for i in range(count):
        seed(store, f"run{i}", request=f"unique request {i}")


def test_the_evaluation_set_is_written_once_with_its_sha256(store, book, tmp_path):
    seed_with_eval(store, 12)
    out = tmp_path / "out"
    manifest = build(store, book, out)
    eval_file = out / "eval.jsonl"
    assert eval_file.exists() and rows(eval_file)
    assert manifest["files"]["eval.jsonl"]["sha256"] == \
        hashlib.sha256(eval_file.read_bytes()).hexdigest()
    assert {r["objective_id"] for r in rows(eval_file)} == \
        {f"run{i}" for i in range(12) if LD.is_eval_id(f"run{i}")}


def test_a_second_build_that_would_change_the_evaluation_set_fails(store, book, tmp_path):
    seed(store, id_where(True), request="held out")
    seed(store, id_where(False), request="trained on")
    out = tmp_path / "out"
    build(store, book, out)
    before = (out / "eval.jsonl").read_bytes()
    store.touch_objective_run(id_where(True), task_class="changed-after-freeze")
    nonce = approve(book, out)
    with pytest.raises(LD.EvalSetChanged):
        LD.build(store, out, approval=nonce, book=book)
    assert (out / "eval.jsonl").read_bytes() == before
    # a refused build does not spend the owner's yes
    assert book.pending[nonce].state == CF.APPROVED


def test_a_tampered_evaluation_file_fails_the_next_build(store, book, tmp_path):
    seed(store, id_where(True))
    out = tmp_path / "out"
    build(store, book, out)
    with open(out / "eval.jsonl", "ab") as fh:
        fh.write(b'{"objective_id": "smuggled"}\n')
    with pytest.raises(LD.EvalSetChanged):
        LD.build(store, out, approval=approve(book, out), book=book)


def test_later_history_never_enters_the_frozen_set(store, book, tmp_path):
    seed(store, id_where(True, "a"))
    out = tmp_path / "out"
    build(store, book, out)
    frozen = (out / "eval.jsonl").read_bytes()
    seed(store, id_where(True, "b"), request="a brand new request")
    manifest = build(store, book, out)
    assert (out / "eval.jsonl").read_bytes() == frozen
    assert id_where(True, "b") in {r["objective_id"] for r in rows(out / "train.jsonl")}
    assert manifest["files"]["eval.jsonl"]["sha256"] == hashlib.sha256(frozen).hexdigest()


def test_a_training_example_in_the_evaluation_set_is_rejected(store, book, tmp_path):
    held = id_where(True, "a")
    seed(store, held, request="Archive the Q3 invoices")
    out = tmp_path / "out"
    build(store, book, out)
    leak = id_where(False, "b")
    seed(store, leak, request="  archive the q3 invoices ")    # same fingerprint
    seed(store, id_where(False, "c"), request="something else entirely")
    manifest = build(store, book, out)
    train_ids = {r["objective_id"] for r in rows(out / "train.jsonl")}
    assert leak not in train_ids
    assert manifest["counts"]["rejected_eval_leak"] == 1
    assert not train_ids & {r["objective_id"] for r in rows(out / "eval.jsonl")}


# -- determinism, privacy, deletion ----------------------------------------


def test_the_same_history_builds_byte_identical_output(store, book, tmp_path):
    seed_with_eval(store, 10)
    first, second = tmp_path / "one", tmp_path / "two"
    build(store, book, first)
    build(store, book, second)
    again = build(store, book, first)                     # rebuild in place too
    for name in ("train.jsonl", "eval.jsonl", "manifest.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert again["files"] == json.loads((second / "manifest.json").read_text())["files"]


def test_no_secret_or_raw_conversation_text_is_stored(store, book, tmp_path):
    seed(store, "talk", request="please email the quarterly zebra-plan to the team",
         summary="zebra-plan summary", method=f"checked with {TOKEN}")
    out = tmp_path / "out"
    build(store, book, out)
    blob = b"".join(p.read_bytes() for p in out.iterdir()).decode()
    assert "zebra" not in blob
    assert TOKEN not in blob
    assert "[REDACTED]" in blob


def test_an_approved_export_can_be_deleted_and_the_deletion_is_verified(store, book, tmp_path):
    seed_with_eval(store, 8)
    out = tmp_path / "out"
    build(store, book, out)
    stranger = out / "keep.txt"
    stranger.write_text("not ours")
    result = LD.delete_export(out)
    assert result["verified"] is True
    assert sorted(result["deleted"]) == ["eval.jsonl", "manifest.json", "train.jsonl"]
    assert [p.name for p in out.iterdir()] == ["keep.txt"]


def test_deleting_something_that_is_not_an_export_is_refused(tmp_path):
    (tmp_path / "train.jsonl").write_text("{}")
    with pytest.raises(LD.NotAnExport):
        LD.delete_export(tmp_path)
    assert (tmp_path / "train.jsonl").exists()


def test_a_manifest_cannot_point_deletion_outside_the_export(store, book, tmp_path):
    seed(store, "good")
    out = tmp_path / "out"
    build(store, book, out)
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["files"]["../victim.txt"] = {"sha256": "0", "rows": 0}
    (out / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(LD.NotAnExport):
        LD.delete_export(out)
    assert victim.read_text() == "precious"
