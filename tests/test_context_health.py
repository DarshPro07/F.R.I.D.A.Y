"""CTX-004: context health (PRD v7 19.5) scores a context, names its state and acts on it.

Before this there was no module that measured the live context, no
HEALTHY/NOISY/DEGRADED/ROTTING state and nothing that pruned, summarized,
folded or handed off on one. Everything here runs on aggregate-shaped fixtures
built by hand, so it needs no store; the wiring tests at the bottom use the
real `memory_stack.aggregate`, `WorkingSet` and `voice_brain._memory_context`.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from friday import compaction
from friday import compression
from friday import context_health as H

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=30)).isoformat()
FRESH = (NOW - timedelta(days=1)).isoformat()


def line(text, tier="specs", ts=None, kind=""):
    return {"tier": tier, "text": text, "ts": ts, "kind": kind}


def head(text):
    return {"tier": None, "text": text}


def agg(layout, budget=900, task=""):
    used = sum(max(1, len(e["text"]) // 4) for e in layout if e.get("tier"))
    return {"task": task, "budget_tokens": budget, "tokens_used": used, "layout": layout}


SPREAD = ("specs", "episodes", "contacts", "outcomes")


def unique(n, tier=None, prefix="- note", ts=None):
    """`n` distinct lines, spread over four tiers unless `tier` pins one."""
    return [line("%s %d about topic %d" % (prefix, i, i * 7), tier or SPREAD[i % 4], ts)
            for i in range(n)]


def duplicated(total, dups):
    """`total` lines of which `dups` repeat an earlier one: ratio dups/total."""
    base = unique(total - dups)
    return base + [dict(base[0]) for _ in range(dups)]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("FRIDAY_CTX_"):
            monkeypatch.delenv(key)


# --- metrics --------------------------------------------------------------

def test_an_empty_context_is_healthy_and_asks_for_nothing():
    v = H.assess(agg([]), NOW)
    assert (v["state"], v["action"], v["advisory"]) == (H.HEALTHY, H.NONE, False)
    assert v["metrics"]["duplicate_ratio"] == 0.0


def test_metrics_on_a_fixed_fixture():
    layout = (duplicated(8, 2)                                    # 8 lines, 2 of them repeats
              + [line("- stale talk", "episodes", OLD)]
              + [line("- OPEN CONTRADICTION on x: 'a' vs 'b'", "relations", kind=H.CONTRADICTION)])
    m = H.measure(agg(layout, budget=100, task="topic notes"), NOW)
    assert m["entries"] == 10
    assert m["duplicate_ratio"] == 0.2
    assert m["stale_ratio"] == 0.1
    assert m["contradiction_score"] == 0.1
    assert m["raw_output_ratio"] == 0.0
    assert m["utilization"] == round(m["tokens"] / 100, 4)
    assert m["relevance_density"] == 0.8            # the 8 note lines name "topic"


def test_unmeasured_metrics_are_named_and_never_given_a_number():
    v = H.assess(agg(unique(8)), NOW)
    assert set(v["unmeasured"]) == {"failed_attempt_density", "struggle_score",
                                    "retrieval_confidence"}
    assert not set(v["unmeasured"]) & set(v["metrics"])
    # No line carries a timestamp and no task was given: those are None, not 0.
    assert v["metrics"]["stale_ratio"] is None
    assert v["metrics"]["relevance_density"] is None


def test_a_raw_dump_is_counted_by_the_share_of_tokens_it_holds():
    layout = unique(5) + [line("OUT " * 400)]                      # 1600 chars > 600
    m = H.measure(agg(layout), NOW)
    assert 0.8 < m["raw_output_ratio"] < 1.0


# --- states and thresholds ------------------------------------------------

@pytest.mark.parametrize("dups,state", [
    (2, H.HEALTHY),     # 0.10, under the NOISY bound
    (3, H.NOISY),       # 0.15, exactly on it
    (6, H.DEGRADED),    # 0.30, exactly on it
    (10, H.ROTTING),    # 0.50, exactly on it
])
def test_duplicate_ratio_boundaries(dups, state):
    assert H.assess(agg(duplicated(20, dups)), NOW)["state"] == state


def test_each_state_selects_its_action():
    assert H.assess(agg(duplicated(20, 3)), NOW)["action"] == H.PRUNE
    assert H.assess(agg(duplicated(20, 6)), NOW)["action"] == H.SUMMARIZE
    rotting = H.assess(agg(duplicated(20, 10)), NOW)
    assert (rotting["action"], rotting["advisory"]) == (H.HANDOFF, True)


def test_a_degraded_context_dominated_by_one_section_folds():
    bulky = [line("- [p%d] %s" % (i, "word " * 80), "specs") for i in range(8)]   # ~100 tokens each
    layout = bulky + unique(8, "contacts") + [dict(bulky[0]) for _ in range(8)]
    v = H.assess(agg(layout, budget=5000), NOW)
    assert v["state"] == H.DEGRADED
    assert (v["action"], v["advisory"], v["bulk_tier"]) == (H.FOLD, True, "specs")


def test_stale_and_contradiction_and_raw_each_drive_the_state():
    stale = unique(10, "episodes", ts=OLD)
    assert H.assess(agg(stale), NOW)["drivers"]["stale_ratio"] == H.ROTTING
    contra = unique(19) + [line("- OPEN CONTRADICTION on a: 'x' vs 'y'", "relations",
                                kind=H.CONTRADICTION)]                # 1 of 20: 0.05
    assert H.assess(agg(contra), NOW)["drivers"]["contradiction_score"] == H.NOISY
    raw = unique(6) + [line("OUT " * 400)]
    assert H.assess(agg(raw), NOW)["drivers"]["raw_output_ratio"] == H.ROTTING


def test_a_context_over_its_budget_is_rotting_however_clean_it_is():
    over = H.assess(agg(unique(8), budget=10), NOW)
    assert over["state"] == H.ROTTING and "utilization" in over["drivers"]
    assert H.assess(agg(unique(8), budget=10_000), NOW)["state"] == H.HEALTHY


def test_a_tiny_context_caps_at_noisy_but_utilization_is_never_capped():
    tiny = [line("- a"), line("- a"), line("- a"), line("- a")]       # ratio 0.75
    assert H.assess(agg(tiny), NOW)["state"] == H.NOISY
    assert H.assess(agg([line("x" * 580)], budget=10), NOW)["state"] == H.ROTTING


def test_relevance_density_is_reported_but_drives_nothing():
    v = H.assess(agg(unique(10), task="zebra quartz"), NOW)
    assert v["metrics"]["relevance_density"] == 0.0
    assert v["state"] == H.HEALTHY


def test_thresholds_are_env_overridable_and_a_bad_value_falls_back(monkeypatch):
    layout = duplicated(20, 6)                                         # 0.30
    monkeypatch.setenv("FRIDAY_CTX_DUPLICATE_RATIO", "0.5,0.6,0.7")
    assert H.assess(agg(layout), NOW)["state"] == H.HEALTHY
    monkeypatch.setenv("FRIDAY_CTX_DUPLICATE_RATIO", "0.7,0.6,0.5")     # not ascending
    assert H.assess(agg(layout), NOW)["state"] == H.DEGRADED
    monkeypatch.setenv("FRIDAY_CTX_DUPLICATE_RATIO", "high")
    assert H.assess(agg(layout), NOW)["state"] == H.DEGRADED


# --- actions --------------------------------------------------------------

RULE = "- never touch data/ada.sqlite3 and never push to main without being asked first, sir"
PREF = "- voice: short sentences, no filler, and never read a URL aloud to the owner"
CONTRA = "- OPEN CONTRADICTION on editor: 'vim' vs 'code'"


def critical_layout(contradiction=True):
    """Preferences, a rule and (by default) one open contradiction. The
    contradiction is critical, so no action can clear it: a context that holds
    one stays at least NOISY until the person resolves it."""
    out = [head("YOUR PREFERENCES AND RULES:"), line(PREF, "preferences"),
           head("RULES OF ENGAGEMENT (git-tracked):"), line(RULE, "rules")]
    if contradiction:
        out += [head("HOW THINGS CONNECT:"),
                line(CONTRA, "relations", kind=H.CONTRADICTION)]
    return out


def test_prune_drops_repeats_stale_and_raw_and_keeps_every_critical_line():
    layout = (critical_layout() + [head("WHAT WAS SAID BEFORE (most recent last):")]
              + [line("- he: hello", "episodes", FRESH)] * 4
              + [line("- he: old news %d" % i, "episodes", OLD) for i in range(3)]
              + [line("DUMP " * 300, "outcomes")])
    before = agg(layout)
    after, verdict = H.remediate(before, NOW)
    assert verdict["state"] == H.ROTTING and verdict["applied"] == H.SUMMARIZE
    pruned = H.prune(layout, NOW)
    texts = [e["text"] for e in pruned]
    assert texts.count("- he: hello") == 1
    assert not any("old news" in t or t.startswith("DUMP") for t in texts)
    for fact in (PREF, RULE, CONTRA):
        assert fact in texts and fact in after["prompt"]


def test_pruning_an_over_budget_context_drops_the_oldest_turns_and_keeps_the_critical_lines():
    turns = [line("- he: turn %02d %s" % (i, "x" * 60), "episodes", FRESH) for i in range(30)]
    before = agg(critical_layout(contradiction=False) + turns, budget=400)
    assert H.assess(before, NOW)["state"] == H.NOISY
    after, verdict = H.remediate(before, NOW)
    assert (verdict["applied"], verdict["after"]) == (H.PRUNE, H.HEALTHY)
    assert after["tokens_used"] <= int(400 * H.FIT_TARGET)
    kept = [e["text"] for e in after["layout"] if e.get("tier") == "episodes"]
    assert kept and kept == [t["text"] for t in turns[-len(kept):]], "newest turns survive"
    assert PREF in after["prompt"] and RULE in after["prompt"]


def test_remediation_brings_a_noisy_context_back_to_healthy():
    layout = critical_layout(contradiction=False) + duplicated(18, 5)
    before = agg(layout)
    assert H.assess(before, NOW)["state"] == H.NOISY
    after, verdict = H.remediate(before, NOW)
    assert (verdict["applied"], verdict["after"]) == (H.PRUNE, H.HEALTHY)
    assert H.assess(after, NOW)["state"] == H.HEALTHY
    assert after["tokens_used"] < before["tokens_used"]
    assert after["prompt"] == "\n".join(e["text"] for e in after["layout"])


def test_summarize_clips_caps_and_keeps_critical_lines_verbatim():
    layout = (critical_layout() + [head("RELEVANT SPECS FROM THE VAULT:")]
              + [line("- [doc%d] %s" % (i, "detail " * 60), "specs") for i in range(10)]
              + [head("WHAT WAS SAID BEFORE (most recent last):")]
              + [line("- he: turn %d" % i, "episodes", FRESH) for i in range(9)])
    out = H.summarize(layout, NOW)
    specs = [e for e in out if e.get("tier") == "specs"]
    assert len(specs) == H.SUMMARY_SECTION_LINES
    assert all(len(e["text"]) <= H.SUMMARY_LINE_CHARS for e in specs)
    said = [e["text"] for e in out if e.get("tier") == "episodes"]
    assert said == ["- he: turn %d" % i for i in range(5, 9)]          # the newest four
    texts = [e["text"] for e in out]
    assert PREF in texts and RULE in texts and CONTRA in texts          # not clipped


def test_a_header_with_no_surviving_line_is_dropped():
    layout = [head("PEOPLE HE MENTIONED:"), line("DUMP " * 300, "contacts")] + critical_layout()
    assert "PEOPLE HE MENTIONED:" not in [e["text"] for e in H.prune(layout, NOW)]


def test_an_action_that_would_lose_a_critical_fact_is_refused(monkeypatch):
    layout = critical_layout() + duplicated(18, 5)
    before = agg(layout)

    def lossy(layout, now=None, budget=0, ledger=None):
        return [e for e in layout if e.get("tier") != "rules"]          # drops the rule

    monkeypatch.setattr(H, "prune", lossy)
    after, verdict = H.remediate(before, NOW)
    assert after is before, "the original context must be kept"
    assert verdict["applied"] == H.NONE and RULE in verdict["refused"]
    with pytest.raises(compaction.LossyCompaction):
        compaction.validate("\n".join(e["text"] for e in lossy(layout)),
                            H.critical_facts(layout))


def test_a_rotting_verdict_carries_the_critical_lines_a_fresh_session_starts_from():
    layout = critical_layout() + duplicated(20, 12)
    after, verdict = H.remediate(agg(layout), NOW)
    assert (verdict["state"], verdict["action"], verdict["advisory"]) == (
        H.ROTTING, H.HANDOFF, True)
    packet = verdict["handoff_packet"]
    assert packet.splitlines() == [PREF, RULE, CONTRA]
    # The fresh session holds only the critical lines: far better than rotting,
    # and still NOISY because the open contradiction travels with it.
    fresh = H.assess(agg(critical_layout()), NOW)
    assert fresh["state"] == H.NOISY
    assert H.assess(agg(critical_layout(contradiction=False)), NOW)["state"] == H.HEALTHY


def test_an_open_contradiction_raises_the_state_but_asks_for_no_action():
    # A critical fact: no prune or summary may drop it, only the person can close it.
    before = agg(critical_layout(contradiction=True) + unique(12))        # 1 of 17: 0.059
    verdict = H.assess(before, NOW)
    assert verdict["state"] == H.NOISY
    assert verdict["drivers"] == {"contradiction_score": H.NOISY}
    assert (verdict["action"], verdict["awaiting_person"]) == (H.NONE, ["contradiction_score"])
    after, outcome = H.remediate(before, NOW)
    assert after is before and outcome["applied"] == H.NONE


def test_a_healthy_context_is_returned_untouched():
    before = agg(critical_layout(contradiction=False) + unique(8))
    after, verdict = H.remediate(before, NOW)
    assert after is before and verdict["applied"] == H.NONE


# --- provenance (CTX-007) --------------------------------------------------

@pytest.fixture()
def vault_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDAY_ARTIFACT_VAULT", str(tmp_path / "vault"))
    return tmp_path / "vault"


def stale_layout():
    return (critical_layout() + [head("WHAT WAS SAID BEFORE (most recent last):")]
            + [line("- he: hello", "episodes", FRESH)] * 4
            + [line("- he: old news %d" % i, "episodes", OLD) for i in range(3)]
            + [line("DUMP " * 300, "outcomes")])


def test_remediate_records_every_line_it_lets_go_of_and_each_one_recovers(vault_dir):
    after, verdict = H.remediate(agg(stale_layout()), NOW)
    records = verdict["transformations"]
    assert {r["action"] for r in records} == {compression.DROP}
    assert {r["payload_class"] for r in records} == {compression.DROP_AFTER_EXTRACTION}
    recovered = {compression.recover(r["recovery"]).decode() for r in records}
    assert recovered == {"- he: old news %d" % i for i in range(3)} | {"DUMP " * 300}
    for r in records:
        assert compression.recover(r["transformed_sha256"]) == compression.recover(r["recovery"])
        assert r["source_sha256"] == r["origin_sha256"] and r["parent_sha256"] == ""
    assert not any(t in after["prompt"] for t in recovered)


def test_a_critical_line_is_never_transformed_even_when_something_else_quotes_it(vault_dir):
    quoting = line("- he: you said " + RULE, "episodes", OLD)
    layout = stale_layout() + [quoting]
    after, verdict = H.remediate(agg(layout), NOW)
    recovered = {compression.recover(r["recovery"]).decode() for r in verdict["transformations"]}
    assert not recovered & {PREF, RULE, CONTRA, quoting["text"]}
    assert quoting["text"] in after["prompt"], "a line that carries a critical fact stays"
    for fact in (PREF, RULE, CONTRA):
        assert fact in after["prompt"]


def test_summarize_records_each_clip_and_each_cap_with_the_original_recoverable(vault_dir):
    long_lines = [line("- [doc%d] %s" % (i, "detail " * 60), "specs") for i in range(10)]
    ledger: list = []
    out = H.summarize(critical_layout() + [head("RELEVANT SPECS:")] + long_lines, NOW,
                      ledger=ledger)
    by_action = {a: [r for r in ledger if r.action == a]
                 for a in (compression.SUMMARIZE, compression.DROP)}
    assert len(by_action[compression.SUMMARIZE]) == H.SUMMARY_SECTION_LINES
    assert len(by_action[compression.DROP]) == 10 - H.SUMMARY_SECTION_LINES
    assert {r.backend for r in by_action[compression.SUMMARIZE]} == {H.CLIP}
    originals = {e["text"] for e in long_lines}
    assert {compression.recover(r.recovery).decode() for r in ledger} == originals
    clipped = [e["text"] for e in out if e.get("tier") == "specs"]
    assert {r.transformed_sha256 for r in by_action[compression.SUMMARIZE]} == {
        compression.sha256(t) for t in clipped}


def test_a_line_whose_original_cannot_be_kept_is_not_dropped(monkeypatch, tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the vault directory should be")
    monkeypatch.setenv("FRIDAY_ARTIFACT_VAULT", str(blocked))
    ledger: list = []
    texts = [e["text"] for e in H.prune(stale_layout(), NOW, ledger=ledger)]
    assert ledger and all(r.refusal and r.action == compression.PASS_RAW for r in ledger)
    assert all("vault refused" in r.refusal for r in ledger)
    assert "- he: old news 0" in texts and ("DUMP " * 300) in texts
    assert RULE in texts


# --- wiring ---------------------------------------------------------------

@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("ADA_DB", os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    from friday.toolsets import memory as M
    M.reset_store(None)
    yield M.store()
    M.reset_store(None)


def test_aggregate_exposes_a_layout_and_a_health_verdict(db):
    from friday import memory_stack as M
    for i in range(30):
        db.add_message("c", "user", "turn %d about the cutover" % i)
    out = M.aggregate("what did we say about the cutover", budget_tokens=900)
    assert out["health"]["state"] in H.STATES
    assert out["health"]["metrics"]["entries"] == sum(out["injected"].values())
    assert "\n".join(e["text"] for e in out["layout"]) == out["prompt"]
    episodes = [e for e in out["layout"] if e.get("tier") == "episodes"]
    assert episodes and all(e["ts"] for e in episodes), "episodes carry their timestamp"


def test_aggregate_flags_an_open_contradiction_as_a_scored_entry(db, monkeypatch):
    from friday import memory_stack as M
    monkeypatch.setattr(M, "relations", lambda task="", limit=12: {
        "tier": "relations", "items": [],
        "conflicts": [{"subject": "editor", "existing": "vim", "proposed": "code"}]})
    out = M.aggregate("editor", budget_tokens=900)
    assert out["health"]["metrics"]["contradiction_score"] > 0
    assert [e["kind"] for e in out["layout"] if e.get("kind")] == [H.CONTRADICTION]


def test_the_voice_brain_acts_on_the_verdict(db, monkeypatch):
    from friday import memory_stack as M
    from friday import voice_brain as V
    for _ in range(12):
        db.add_message("c", "user", "ok")
    plain = M.aggregate("anything", budget_tokens=900)
    assert plain["health"]["metrics"]["duplicate_ratio"] > 0.5
    ctx = V._memory_context("anything")
    assert ctx.count("- he: ok") < plain["prompt"].count("- he: ok")
    assert ctx.count("- he: ok") == 1


def test_the_long_run_benchmark_passes_and_writes_its_evidence(tmp_path):
    import importlib.util
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "scripts" / "golden_context.py"
    spec = importlib.util.spec_from_file_location("golden_context", path)
    golden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(golden)
    out = tmp_path / "evidence.json"
    assert golden.main(["--out", str(out)]) == 0
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert [c["name"] for c in evidence["checks"] if not c["ok"]] == []
    assert len(evidence["checks"]) == 7 and len(evidence["controlled"]) == golden.TURNS


def test_a_working_set_carries_the_health_verdict_it_is_given():
    from friday import model_gateway as G
    verdict = H.assess(agg(unique(8)), NOW)
    ws = G.compile_working_set(user="hello", health=verdict)
    assert ws.telemetry()["health"]["state"] == H.HEALTHY
    assert "health" not in G.compile_working_set(user="hello").telemetry()
