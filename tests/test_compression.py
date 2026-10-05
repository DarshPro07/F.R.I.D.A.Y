"""
The compression coordinator (PRD v7 CTX-007, §16 payload classes, §18).

Lossless literals pass raw; a lossy action on LOSSLESS_REQUIRED is refused; a
stack of transformations keeps every critical fact and every transformed
artifact is recoverable by its hash; a backend that raises, a vault that cannot
write and a vault entry that no longer matches its hash all fail closed.
"""
from __future__ import annotations

import pytest

from friday import compaction, config, contracts
from friday import compression as C

RULE = "MUST: never touch data/ada.sqlite3 and never push to main"
LITERAL = "EXACT: Q3 report.md"
FACTS = compaction.CriticalFactSet(constraints=(RULE,), literals=(LITERAL,))
NOISE = "the build log said many things about nothing in particular " * 8


@pytest.fixture()
def vault(tmp_path):
    return C.ArtifactVault(tmp_path / "vault")


@pytest.fixture()
def coordinator(vault):
    return C.CompressionCoordinator(vault, {"head": lambda t: t[:40], "same": lambda t: t})


def blobs(vault):
    return list((vault.root / "blobs").rglob("*")) if (vault.root / "blobs").exists() else []


# --- classification -------------------------------------------------------

def test_a_payload_that_carries_a_critical_line_is_lossless_whatever_it_is_told():
    carrying = "build said:\n  MUST:   never touch data/ada.sqlite3 and never push to main\n"
    assert C.classify(carrying, facts=FACTS, hint=C.DROP_AFTER_EXTRACTION) == C.LOSSLESS_REQUIRED
    assert C.classify(NOISE, facts=FACTS, hint=C.DROP_AFTER_EXTRACTION) == C.DROP_AFTER_EXTRACTION


@pytest.mark.parametrize("tag", sorted(C.LOSSLESS_TAGS) + ["Security Evidence", "exact-literal"])
def test_each_lossless_tag_makes_a_payload_lossless(tag):
    assert C.classify(NOISE, tags=(tag,), hint=C.SUMMARIZABLE) == C.LOSSLESS_REQUIRED


@pytest.mark.parametrize("hint", ["", "SUMMARISE_ME", "summarizable"])
def test_a_payload_nobody_can_classify_is_lossless(hint):
    assert C.classify(NOISE, hint=hint) == C.LOSSLESS_REQUIRED


def test_a_payload_that_is_not_text_is_lossless():
    assert C.classify(b"bytes", hint=C.SUMMARIZABLE) == C.LOSSLESS_REQUIRED
    assert C.classify(None, hint=C.SUMMARIZABLE) == C.LOSSLESS_REQUIRED


@pytest.mark.parametrize("cls", [C.RECOVERABLE_LOSSY_ALLOWED, C.SUMMARIZABLE,
                                 C.DROP_AFTER_EXTRACTION])
def test_a_payload_that_is_told_what_it_is_keeps_that_class(cls):
    assert C.classify(NOISE, facts=FACTS, hint=cls) == cls


# --- lossless payloads pass raw -------------------------------------------

def test_a_lossless_literal_passes_byte_identical_and_touches_no_vault(coordinator, vault):
    text = "EXACT: café → “Q3  report”.md\t\n"
    out, record = coordinator.transform(text, C.PASS_RAW, facts=FACTS, tags=("exact_literal",))
    assert out == text and out.encode() == text.encode()
    assert (record.action, record.payload_class, record.refusal) == (
        C.PASS_RAW, C.LOSSLESS_REQUIRED, "")
    assert record.source_sha256 == record.transformed_sha256 == C.sha256(text)
    assert record.recovery == "" and not vault.root.exists()


@pytest.mark.parametrize("action", [C.SUMMARIZE, C.FOLD_TO_ARTIFACT, C.DROP])
def test_a_lossy_action_on_a_lossless_payload_is_refused(coordinator, vault, action):
    with pytest.raises(compaction.LossyCompaction) as refused:
        coordinator.transform(LITERAL, action, backend="head", facts=FACTS,
                              hint=C.DROP_AFTER_EXTRACTION)
    assert LITERAL not in str(refused.value), "the refusal names the class, not the payload"
    assert not vault.root.exists()


def test_a_payload_carrying_a_fact_inside_noise_is_refused_a_lossy_action(coordinator):
    with pytest.raises(compaction.LossyCompaction):
        coordinator.transform(NOISE + RULE, C.DROP, facts=FACTS, hint=C.DROP_AFTER_EXTRACTION)


@pytest.mark.parametrize("cls,action", [
    (C.RECOVERABLE_LOSSY_ALLOWED, C.SUMMARIZE), (C.RECOVERABLE_LOSSY_ALLOWED, C.DROP),
    (C.SUMMARIZABLE, C.DROP)])
def test_an_action_the_class_does_not_allow_is_refused(coordinator, cls, action):
    with pytest.raises(contracts.ContractError):
        coordinator.transform(NOISE, action, backend="head", hint=cls)


def test_an_unknown_action_and_a_payload_that_is_not_text_are_refused(coordinator):
    with pytest.raises(contracts.ContractError):
        coordinator.transform(NOISE, "SQUASH", hint=C.SUMMARIZABLE)
    with pytest.raises(contracts.ContractError):
        coordinator.transform(b"bytes", C.PASS_RAW, hint=C.SUMMARIZABLE)


# --- provenance and recovery ----------------------------------------------

def test_a_stack_of_summarize_fold_drop_is_recorded_step_by_step_and_recovers(coordinator):
    out, records = coordinator.stack(
        NOISE, [(C.SUMMARIZE, "head"), (C.FOLD_TO_ARTIFACT, ""), (C.DROP, "")],
        facts=FACTS, hint=C.DROP_AFTER_EXTRACTION)
    assert out.startswith("[dropped") and C.POINTER + C.sha256(NOISE) in out
    assert [r.action for r in records] == [C.SUMMARIZE, C.FOLD_TO_ARTIFACT, C.DROP]
    summarized, folded, dropped = records
    assert summarized.source_sha256 == C.sha256(NOISE) and summarized.parent_sha256 == ""
    assert summarized.transformed_sha256 == C.sha256(NOISE[:40])
    assert folded.source_sha256 == summarized.transformed_sha256 == folded.parent_sha256
    assert dropped.source_sha256 == folded.transformed_sha256 == dropped.parent_sha256
    assert {r.origin_sha256 for r in records} == {C.sha256(NOISE)}
    assert {r.recovery for r in records} == {C.POINTER + C.sha256(NOISE)}
    assert all(r.payload_class == C.DROP_AFTER_EXTRACTION and r.refusal == "" for r in records)
    for record in records:
        assert coordinator.recover(record.transformed_sha256) == NOISE.encode()
        assert coordinator.recover(record.recovery) == NOISE.encode()


def test_a_fold_leaves_a_stub_that_names_the_original(coordinator):
    out, record = coordinator.transform(NOISE, C.FOLD_TO_ARTIFACT, hint=C.RECOVERABLE_LOSSY_ALLOWED)
    assert record.origin_sha256 in out and str(len(NOISE)) in out and len(out) < len(NOISE)
    assert coordinator.recover(C.sha256(out)) == NOISE.encode()


def test_a_stack_keeps_every_critical_line_in_the_context_it_shrinks(coordinator):
    lines = [RULE, LITERAL] + [NOISE + str(i) for i in range(6)]
    packet = []
    for text in lines:
        if C.classify(text, facts=FACTS, hint=C.DROP_AFTER_EXTRACTION) == C.LOSSLESS_REQUIRED:
            out, _ = coordinator.transform(text, C.PASS_RAW, facts=FACTS)
        else:
            out, _ = coordinator.stack(text, [(C.SUMMARIZE, "head"), (C.FOLD_TO_ARTIFACT, "")],
                                       facts=FACTS, hint=C.DROP_AFTER_EXTRACTION)
        packet.append(out)
    compaction.validate("\n".join(packet), FACTS)
    assert sum(map(len, packet)) < sum(map(len, lines))


def test_a_stage_that_drops_a_fact_its_input_carried_is_refused(coordinator, monkeypatch):
    monkeypatch.setattr(C, "classify", lambda *a, **k: C.SUMMARIZABLE)
    carrying = RULE + " " + NOISE
    out, record = coordinator.transform(carrying, C.SUMMARIZE, backend="head", facts=FACTS)
    assert out == carrying
    assert record.action == C.PASS_RAW and record.requested == C.SUMMARIZE
    assert "critical fact" in record.refusal
    assert blobs(coordinator.vault) == []
    coordinator.register("tag", lambda t: "(shortened) " + t.replace(NOISE, "..."))
    out, record = coordinator.transform(carrying, C.SUMMARIZE, backend="tag", facts=FACTS)
    assert RULE in out and record.action == C.SUMMARIZE and record.refusal == ""


def test_a_backend_that_changes_nothing_is_a_pass_not_a_transformation(coordinator, vault):
    out, record = coordinator.transform(NOISE, C.SUMMARIZE, backend="same", hint=C.SUMMARIZABLE)
    assert out == NOISE and record.action == C.PASS_RAW and record.refusal == ""
    assert record.recovery == "" and not vault.root.exists()


def test_a_dropped_payload_leaves_a_marker_that_recovers_it(coordinator):
    out, record = coordinator.transform(NOISE, C.DROP, hint=C.DROP_AFTER_EXTRACTION)
    assert out.startswith("[dropped") and record.transformed_sha256 == C.sha256(out)
    other, another = coordinator.transform(NOISE + "!", C.DROP, hint=C.DROP_AFTER_EXTRACTION)
    assert another.transformed_sha256 != record.transformed_sha256
    assert coordinator.recover(record.recovery) == NOISE.encode()
    assert coordinator.recover(record.transformed_sha256) == NOISE.encode()
    assert coordinator.recover(another.transformed_sha256) == (NOISE + "!").encode()


def test_two_originals_behind_one_hash_are_recovered_by_pointer_never_guessed(coordinator):
    first, a = coordinator.transform("alpha " + NOISE, C.SUMMARIZE, backend="head",
                                     hint=C.SUMMARIZABLE)
    second, b = coordinator.transform("alpha " + NOISE + " and more", C.SUMMARIZE,
                                      backend="head", hint=C.SUMMARIZABLE)
    assert a.transformed_sha256 == b.transformed_sha256 and a.recovery != b.recovery
    with pytest.raises(C.ArtifactError):
        coordinator.recover(a.transformed_sha256)
    assert coordinator.recover(a.recovery) == ("alpha " + NOISE).encode()
    assert coordinator.recover(b.recovery) == ("alpha " + NOISE + " and more").encode()


def test_a_handle_that_is_not_a_hash_or_names_nothing_is_an_error(coordinator):
    for handle in ("", "../../etc/passwd", "vault:sha256:xyz", "g" * 64):
        with pytest.raises(C.ArtifactError):
            coordinator.recover(handle)
    with pytest.raises(C.ArtifactError):
        coordinator.recover("a" * 64)


# --- fail closed ----------------------------------------------------------

def test_a_backend_that_raises_returns_the_payload_raw_with_the_refusal_recorded(vault):
    def broken(text):
        raise RuntimeError("model down: sk-do-not-log-this")

    coordinator = C.CompressionCoordinator(vault, {"broken": broken})
    out, record = coordinator.transform(NOISE, C.SUMMARIZE, backend="broken",
                                        hint=C.SUMMARIZABLE)
    assert out == NOISE and record.action == C.PASS_RAW and record.requested == C.SUMMARIZE
    assert "RuntimeError" in record.refusal and "sk-do-not-log" not in record.refusal
    assert not vault.root.exists(), "nothing is stored for a transformation that did not happen"


@pytest.mark.parametrize("backends,name,why", [
    ({}, "missing", "no SUMMARIZE backend"),
    ({"odd": lambda t: 7}, "odd", "not text")])
def test_a_missing_or_malformed_backend_is_a_recorded_refusal(vault, backends, name, why):
    out, record = C.CompressionCoordinator(vault, backends).transform(
        NOISE, C.SUMMARIZE, backend=name, hint=C.SUMMARIZABLE)
    assert out == NOISE and record.action == C.PASS_RAW and why in record.refusal


def test_a_vault_that_cannot_write_returns_the_payload_raw(tmp_path, coordinator):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the vault directory should be")
    coordinator.vault = C.ArtifactVault(blocked)
    out, record = coordinator.transform(NOISE, C.SUMMARIZE, backend="head", hint=C.SUMMARIZABLE)
    assert out == NOISE and record.action == C.PASS_RAW and "vault refused" in record.refusal


def test_a_tampered_vault_entry_fails_closed_on_write_and_on_read(coordinator, vault):
    out, record = coordinator.transform(NOISE, C.SUMMARIZE, backend="head",
                                        hint=C.SUMMARIZABLE)
    assert out == NOISE[:40]
    (blob,) = [p for p in blobs(vault) if p.is_file()]
    blob.write_bytes(b"something else entirely")
    with pytest.raises(C.ArtifactError):
        coordinator.recover(record.transformed_sha256)
    again, refused = coordinator.transform(NOISE, C.SUMMARIZE, backend="head",
                                           hint=C.SUMMARIZABLE)
    assert again == NOISE and refused.action == C.PASS_RAW and "hash" in refused.refusal


def test_a_stack_whose_origin_was_tampered_between_steps_stops_there(coordinator, vault):
    first, one = coordinator.transform(NOISE, C.SUMMARIZE, backend="head", hint=C.DROP_AFTER_EXTRACTION)
    (blob,) = [p for p in blobs(vault) if p.is_file()]
    blob.write_bytes(b"tampered")
    out, two = coordinator.transform(first, C.DROP, hint=C.DROP_AFTER_EXTRACTION, parent=one)
    assert out == first and two.action == C.PASS_RAW and "vault refused" in two.refusal
    assert two.parent_sha256 == one.transformed_sha256


def test_a_stack_stops_at_the_first_refusal_and_keeps_the_last_good_text(coordinator):
    out, records = coordinator.stack(
        NOISE, [(C.SUMMARIZE, "head"), (C.SUMMARIZE, "absent"), (C.DROP, "")],
        hint=C.DROP_AFTER_EXTRACTION)
    assert out == NOISE[:40] and len(records) == 2 and records[1].refusal


def test_a_stack_of_a_lossless_payload_is_refused_before_any_step(coordinator, vault):
    with pytest.raises(compaction.LossyCompaction):
        coordinator.stack(RULE, [(C.SUMMARIZE, "head")], facts=FACTS, hint=C.SUMMARIZABLE)
    assert not vault.root.exists()


# --- the registry and the vault's home ------------------------------------

def test_a_backend_registers_under_a_name_and_must_be_callable(vault):
    coordinator = C.CompressionCoordinator(vault)
    coordinator.register("caveman", lambda t: t.upper()[:10])
    out, record = coordinator.transform("quiet words here", C.SUMMARIZE, backend="caveman",
                                        hint=C.SUMMARIZABLE)
    assert out == "QUIET WORD" and record.backend == "caveman"
    for bad in (("", len), ("x", None)):
        with pytest.raises(contracts.ContractError):
            coordinator.register(*bad)


def test_the_vault_lives_in_its_own_directory_and_the_environment_moves_it(monkeypatch, tmp_path):
    monkeypatch.delenv("FRIDAY_ARTIFACT_VAULT", raising=False)
    home = C.default_root()
    assert home == config.DATA_DIR / "compression_vault" and home.name != "ada.sqlite3"
    monkeypatch.setenv("FRIDAY_ARTIFACT_VAULT", str(tmp_path / "elsewhere"))
    assert C.ArtifactVault().root == tmp_path / "elsewhere"


def test_the_shared_coordinator_recovers_through_the_module_function(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDAY_ARTIFACT_VAULT", str(tmp_path / "shared"))
    _, record = C.default().transform(NOISE, C.DROP, hint=C.DROP_AFTER_EXTRACTION)
    assert C.recover(record.recovery) == NOISE.encode()
