"""NEURAL-001: the PRD 24.11 training lifecycle skeleton. A candidate network
is registered against a lab_dataset export, trained offline by an injected
trainer, scored on the frozen holdout, and promoted only past the baseline by
a margin. Nothing here trains or downloads anything."""
from __future__ import annotations

import hashlib
import json

import pytest

from friday import lab_dataset as LD
from friday import neural_fabric as NF


def _rows(prefix: str, n: int) -> list[dict]:
    return [{"objective_id": f"{prefix}{i}", "request_fingerprint": f"{prefix}f{i}",
             "capabilities": ["a"]} for i in range(n)]


def _export(tmp_path, train=6, held=3):
    """A directory shaped exactly as lab_dataset.build writes it."""
    out = tmp_path / "export"
    out.mkdir()
    files = {}
    for name, rows in ((LD.TRAIN, _rows("t", train)), (LD.EVAL, _rows("e", held))):
        data = LD._dump(rows)
        (out / name).write_bytes(data)
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "rows": len(rows)}
    (out / LD.MANIFEST).write_text(json.dumps({"version": 1, "files": files}),
                                   encoding="utf-8")
    return out


def _trainer(rows):
    return {"weights": len(rows)}


def _fabric(tmp_path):
    return NF.NeuralFabric(tmp_path / "state")


def _candidate(tmp_path, fab, name="router-1"):
    fab.register(name, "model_router", _export(tmp_path))
    fab.train(name, _trainer)
    return name


def test_register_records_the_dataset_reference_from_the_manifest(tmp_path):
    fab = _fabric(tmp_path)
    export = _export(tmp_path)
    rec = fab.register("router-1", "model_router", export)
    manifest = json.loads((export / LD.MANIFEST).read_text(encoding="utf-8"))["files"]
    assert rec["status"] == NF.CANDIDATE
    assert rec["dataset"]["train_sha256"] == manifest[LD.TRAIN]["sha256"]
    assert rec["dataset"]["eval_sha256"] == manifest[LD.EVAL]["sha256"]


def test_a_directory_that_is_not_an_export_is_refused(tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(LD.NotAnExport):
        _fabric(tmp_path).register("x", "model_router", tmp_path / "plain")


def test_a_duplicate_name_is_refused(tmp_path):
    fab = _fabric(tmp_path)
    export = _export(tmp_path)
    fab.register("router-1", "model_router", export)
    with pytest.raises(NF.AlreadyRegistered):
        fab.register("router-1", "model_router", export)


def test_train_hands_the_trainer_only_the_train_rows(tmp_path):
    fab = _fabric(tmp_path)
    fab.register("router-1", "model_router", _export(tmp_path))
    seen = []
    rec = fab.train("router-1", lambda rows: seen.extend(rows) or {"n": len(rows)})
    assert {r["objective_id"][0] for r in seen} == {"t"} and len(seen) == 6
    assert rec["trained"] is True and rec["artifact"] == {"n": 6}


def test_evaluate_before_train_is_refused(tmp_path):
    fab = _fabric(tmp_path)
    fab.register("router-1", "model_router", _export(tmp_path))
    with pytest.raises(NF.NotTrained):
        fab.evaluate("router-1", lambda a, rows: 1.0, lambda rows: 0.0)


def test_a_tampered_holdout_blocks_evaluation(tmp_path):
    fab = _fabric(tmp_path)
    name = _candidate(tmp_path, fab)
    with (tmp_path / "export" / LD.EVAL).open("ab") as fh:
        fh.write(b'{"objective_id":"sneaky"}\n')
    with pytest.raises(LD.EvalSetChanged):
        fab.evaluate(name, lambda a, rows: 1.0, lambda rows: 0.0)


def test_a_winner_past_the_margin_is_promoted_and_usable(tmp_path):
    fab = _fabric(tmp_path)
    name = _candidate(tmp_path, fab)
    held = []
    fab.evaluate(name, lambda a, rows: held.extend(rows) or 0.90, lambda rows: 0.80)
    assert {r["objective_id"][0] for r in held} == {"e"}
    rec = fab.promote(name, margin=0.05)
    assert rec["status"] == NF.PRODUCTION
    assert fab.use(name) == {"weights": 6}


def test_a_candidate_inside_the_margin_stays_candidate_with_its_numbers(tmp_path):
    fab = _fabric(tmp_path)
    name = _candidate(tmp_path, fab)
    fab.evaluate(name, lambda a, rows: 0.82, lambda rows: 0.80)
    rec = fab.promote(name, margin=0.05)
    assert rec["status"] == NF.CANDIDATE
    assert rec["holdout"]["candidate"] == 0.82 and rec["holdout"]["baseline"] == 0.80
    assert rec["decision"]["promoted"] is False and rec["decision"]["margin"] == 0.05


def test_promote_without_evaluation_is_refused(tmp_path):
    fab = _fabric(tmp_path)
    name = _candidate(tmp_path, fab)
    with pytest.raises(NF.NotEvaluated):
        fab.promote(name, margin=0.05)


def test_a_non_production_network_is_refused_by_use(tmp_path):
    fab = _fabric(tmp_path)
    name = _candidate(tmp_path, fab)
    with pytest.raises(NF.NotProduction):
        fab.use(name)
    fab.evaluate(name, lambda a, rows: 0.82, lambda rows: 0.80)
    fab.promote(name, margin=0.05)
    with pytest.raises(NF.NotProduction):
        fab.use(name)
    with pytest.raises(NF.NotProduction):
        fab.use("never-registered")


def test_state_survives_a_new_instance(tmp_path):
    fab = _fabric(tmp_path)
    name = _candidate(tmp_path, fab)
    fab.evaluate(name, lambda a, rows: 0.9, lambda rows: 0.5)
    fab.promote(name, margin=0.1)
    assert _fabric(tmp_path).use(name) == {"weights": 6}


def test_a_stale_evaluation_cannot_promote_after_the_holdout_changes(tmp_path):
    fab = _fabric(tmp_path)
    name = _candidate(tmp_path, fab)
    fab.evaluate(name, lambda a, rows: 0.9, lambda rows: 0.5)
    with (tmp_path / "export" / LD.EVAL).open("ab") as fh:
        fh.write(b"{}\n")
    with pytest.raises(LD.EvalSetChanged):
        fab.promote(name, margin=0.1)


# -- LAB-004 hooks: a strict win, and a way back to CANDIDATE ------------------


def _evaluated(tmp_path, fab, candidate, baseline):
    fab.register("router-1", "model_router", _export(tmp_path))
    fab.train("router-1", _trainer)
    fab.evaluate("router-1", lambda artifact, rows: candidate, lambda rows: baseline)
    return "router-1"


def test_a_tie_promotes_by_default_but_not_when_a_strict_win_is_required(tmp_path):
    fab = _fabric(tmp_path)
    name = _evaluated(tmp_path, fab, 0.8, 0.8)
    rec = fab.promote(name, margin=0.0, strict=True)
    assert rec["status"] == NF.CANDIDATE and rec["decision"]["promoted"] is False
    assert fab.promote(name, margin=0.0)["status"] == NF.PRODUCTION


def test_a_strict_win_promotes(tmp_path):
    fab = _fabric(tmp_path)
    name = _evaluated(tmp_path, fab, 0.85, 0.8)
    assert fab.promote(name, margin=0.0, strict=True)["status"] == NF.PRODUCTION


def test_demote_returns_a_production_network_to_candidate(tmp_path):
    fab = _fabric(tmp_path)
    name = _evaluated(tmp_path, fab, 0.9, 0.5)
    fab.promote(name)
    rec = fab.demote(name, "rolled back")
    assert rec["status"] == NF.CANDIDATE and rec["decision"]["demoted"] == "rolled back"
    with pytest.raises(NF.NotProduction):
        fab.use(name)
