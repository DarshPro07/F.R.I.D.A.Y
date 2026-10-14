from __future__ import annotations

import pytest

from friday import capability_search as C
from friday import capability_sources as S
from friday import forge


def clean_scan(directory):
    return forge.ScanReport(
        status=forge.SCAN_CLEAN,
        findings=[],
        target_sha256=forge.tree_digest(directory),
    )


class Marketplace(S.CapabilitySource):
    kind = "market"

    def __init__(self, entries):
        self.entries = entries
        self.fetched = []

    def health(self):
        return S.SourceHealth(S.AVAILABLE, "test")

    def catalog(self):
        return [S.CatalogEntry(**entry, source=self.kind) for entry in self.entries]

    def fetch(self, entry, destination):
        self.fetched.append(entry.name)
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text(f"# {entry.name}\n", encoding="utf-8")


class BrokenHealth(Marketplace):
    kind = "broken-health"

    def health(self):
        raise RuntimeError("health blew up")


class BrokenHarvest(Marketplace):
    kind = "broken-harvest"

    def fetch(self, entry, destination):
        raise RuntimeError("fetch exploded")


def test_local_catalogue_is_searched_before_marketplace(tmp_path):
    source = Marketplace([{"name": "market-pdf", "category": "documents"}])
    calls = []

    def local(query, *, limit):
        calls.append((query, limit))
        return [{"capability": "documents_extract", "what_it_does": "read PDFs"}]

    result = C.search_for_missing_capability(
        "read this pdf",
        local_catalogue=local,
        marketplace_sources=[source],
        quarantine_root=tmp_path / "quarantine",
        scanner=clean_scan,
    )

    assert calls == [("read this pdf", 6)]
    assert result.decision == C.LOCAL_MATCH
    assert result.local_matches[0]["capability"] == "documents_extract"
    assert result.marketplace_candidates == []
    assert source.fetched == []
    assert not (tmp_path / "quarantine").exists()


def test_marketplace_candidates_are_scanned_but_not_installed(tmp_path):
    source = Marketplace([
        {"name": "video-editor", "category": "video", "popularity": 1},
        {"name": "pdf-reader", "category": "documents", "popularity": 50},
    ])

    result = C.search_for_missing_capability(
        "need a pdf reader",
        local_catalogue=lambda query, limit: [],
        marketplace_sources=[source],
        quarantine_root=tmp_path / "quarantine",
        scanner=clean_scan,
    )

    assert result.decision == C.CANDIDATE_READY
    assert source.fetched == ["pdf-reader"]
    assert [c.entry.name for c in result.passing_candidates()] == ["pdf-reader"]
    candidate = result.marketplace_candidates[0]
    assert candidate.state == S.SCANNED_CLEAN
    assert candidate.admitted
    assert candidate.directory == tmp_path / "quarantine" / "market" / "pdf-reader"
    assert result.message == "1 gated candidate is ready for policy approval"


def test_no_passing_candidate_returns_honest_no_match(tmp_path):
    source = Marketplace([{"name": "calendar-pack", "category": "calendar"}])

    result = C.search_for_missing_capability(
        "transcribe this audio",
        local_catalogue=lambda query, limit: [],
        marketplace_sources=[source],
        quarantine_root=tmp_path / "quarantine",
        scanner=clean_scan,
    )

    assert result.decision == C.NO_CANDIDATE
    assert result.passing_candidates() == []
    assert result.marketplace_candidates == []
    assert result.message == "no local capability or passing marketplace candidate matched"


def test_source_health_failure_does_not_abort_other_sources(tmp_path):
    good = Marketplace([{"name": "pdf-reader", "category": "documents"}])

    result = C.search_for_missing_capability(
        "need a pdf reader",
        local_catalogue=lambda query, limit: [],
        marketplace_sources=[BrokenHealth([]), good],
        quarantine_root=tmp_path / "quarantine",
        scanner=clean_scan,
    )

    assert result.decision == C.CANDIDATE_READY
    assert [c.entry.name for c in result.passing_candidates()] == ["pdf-reader"]
    assert result.sources[0].health.state == S.UNAVAILABLE
    assert "health blew up" in result.sources[0].health.detail


def test_harvest_failure_does_not_abort_other_sources(tmp_path):
    good = Marketplace([{"name": "pdf-reader", "category": "documents"}])

    result = C.search_for_missing_capability(
        "need a pdf reader",
        local_catalogue=lambda query, limit: [],
        marketplace_sources=[
            BrokenHarvest([{"name": "pdf-tool", "category": "documents"}]),
            good,
        ],
        quarantine_root=tmp_path / "quarantine",
        scanner=clean_scan,
    )

    assert result.decision == C.CANDIDATE_READY
    assert [c.entry.name for c in result.passing_candidates()] == ["pdf-reader"]
    assert result.sources[0].health.state == S.UNAVAILABLE
    assert "fetch exploded" in result.sources[0].health.detail


def test_approved_candidate_runs_sandbox_activate_and_retries_original_task(tmp_path):
    source = Marketplace([{"name": "pdf-reader", "category": "documents"}])
    events = []

    def approve(candidate):
        events.append(("approve", candidate.entry.name))
        return C.GateDecision(True, "owner approved")

    def sandbox(candidate, query):
        events.append(("sandbox", candidate.entry.name, query))
        return C.GateDecision(True, "sandbox passed")

    def activate(candidate):
        events.append(("activate", candidate.entry.name))
        return {"capability": "pdf_reader", "state": "ACTIVE"}

    def retry(original_task, activation):
        events.append(("retry", original_task, activation["capability"]))
        return {"status": "ok", "used": activation["capability"]}

    result = C.resolve_missing_capability(
        "need a pdf reader",
        local_catalogue=lambda query, limit: [],
        marketplace_sources=[source],
        quarantine_root=tmp_path / "quarantine",
        scanner=clean_scan,
        approval_gate=approve,
        sandbox_gate=sandbox,
        activator=activate,
        retry_original=retry,
    )

    assert result.decision == C.ORIGINAL_TASK_RETRIED
    assert result.activation == {"capability": "pdf_reader", "state": "ACTIVE"}
    assert result.retry_result == {"status": "ok", "used": "pdf_reader"}
    assert events == [
        ("approve", "pdf-reader"),
        ("sandbox", "pdf-reader", "need a pdf reader"),
        ("activate", "pdf-reader"),
        ("retry", "need a pdf reader", "pdf_reader"),
    ]


def test_pipeline_stops_before_activation_without_approval(tmp_path):
    source = Marketplace([{"name": "pdf-reader", "category": "documents"}])
    activated = []

    result = C.resolve_missing_capability(
        "need a pdf reader",
        local_catalogue=lambda query, limit: [],
        marketplace_sources=[source],
        quarantine_root=tmp_path / "quarantine",
        scanner=clean_scan,
        approval_gate=lambda candidate: C.GateDecision(False, "approval required"),
        sandbox_gate=lambda candidate, query: C.GateDecision(True, "sandbox passed"),
        activator=lambda candidate: activated.append(candidate.entry.name),
        retry_original=lambda original_task, activation: {"status": "ok"},
    )

    assert result.decision == C.POLICY_APPROVAL_REQUIRED
    assert result.message == "approval required"
    assert activated == []


# --- honest reporting, approval reachability, live branch ---------------------


class _Unavailable(Marketplace):
    kind = "offline-market"

    def health(self):
        return S.SourceHealth(S.UNAVAILABLE, "no transport")


def _search(tmp_path, srcs, query="need a pdf reader"):
    return C.search_for_missing_capability(
        query,
        local_catalogue=lambda query, limit: [],
        marketplace_sources=srcs,
        quarantine_root=tmp_path / "quarantine",
        scanner=clean_scan,
    )


def test_spoken_summary_never_claims_a_search_that_did_not_run(tmp_path):
    result = _search(tmp_path, [_Unavailable([]), _Unavailable([])])

    spoken = C.spoken_summary(result).lower()

    assert "could not reach" in spoken
    assert "searched" not in spoken
    assert "no matching" not in spoken


def test_spoken_summary_says_none_matched_when_a_source_was_searched(tmp_path):
    result = _search(tmp_path, [_Unavailable([]),
                                Marketplace([{"name": "calendar-pack", "category": "calendar"}])])

    spoken = C.spoken_summary(result).lower()

    assert result.decision == C.NO_CANDIDATE
    assert "searched" in spoken and "none" in spoken
    assert "could not reach every" in spoken or "some sources" in spoken


def test_spoken_summary_for_a_ready_candidate_asks_for_approval(tmp_path):
    result = _search(tmp_path, [Marketplace([{"name": "pdf-reader", "category": "documents"}])])

    spoken = C.spoken_summary(result).lower()

    assert result.decision == C.CANDIDATE_READY
    assert "approval" in spoken and "pdf-reader" in spoken
    assert "activated" not in spoken


def test_marketplace_activation_has_a_declared_policy_category():
    from friday import policy

    assert "capability_marketplace_activate" in policy.TOOL_CATEGORIES


def test_default_approval_gate_is_reachable_and_never_auto_when_guarded(monkeypatch, tmp_path):
    from friday import policy

    monkeypatch.setattr(policy, "default_engine", policy.PolicyEngine(autonomy=policy.GUARDED))
    result = _search(tmp_path, [Marketplace([{"name": "pdf-reader", "category": "documents"}])])
    candidate = result.passing_candidates()[0]

    gate = C._default_approval_gate(candidate)

    assert gate.allowed is False
    assert "no declared policy category" not in gate.reason
    # ... and the approval is a real, grantable ASK, not an unknown tool.
    engine = policy.default_engine
    assert engine.decide("capability_marketplace_activate").decision == policy.ASK
    engine.approve_for_session("capability_marketplace_activate")
    assert C._default_approval_gate(candidate).allowed is True


def test_local_catalogue_type_error_is_not_swallowed_or_run_twice():
    calls = []

    def local(query, limit):
        calls.append(1)
        raise TypeError("bad limit handling inside the catalogue")

    with pytest.raises(TypeError):
        C._search_local(local, "q", limit=3)
    assert calls == [1]


def test_local_catalogue_accepts_positional_or_keyword_limit():
    assert C._search_local(lambda q, limit: [{"q": q, "l": limit}], "x", limit=4) == [{"q": "x", "l": 4}]
    assert C._search_local(lambda q, *, limit: [limit], "x", limit=4) == [4]
    assert C._search_local(lambda q: ["only"], "x", limit=4) == ["only"]


def test_live_search_with_nothing_matching_reports_honestly(monkeypatch, tmp_path):
    import asyncio
    import json

    import agent_friday as af

    monkeypatch.setattr(af, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(C, "default_marketplace_sources",
                        lambda: [_Unavailable([]), _Unavailable([])])
    agent = af.FridayAgent.__new__(af.FridayAgent)
    agent._router = af.capability_router.Router()
    agent._router.all_tools = {"zz_present": object()}
    agent._tools_offline = False

    out = json.loads(asyncio.run(
        af.FridayAgent.search_capabilities.__wrapped__(agent, "qqxyzzy flibbertigibbet")))

    assert out["found"] == 0
    missing = out["missing_capability"]
    assert missing["decision"] == C.NO_CANDIDATE
    assert [s["health"] for s in missing["sources"]] == [S.UNAVAILABLE, S.UNAVAILABLE]
    assert missing["passing_candidates"] == []
    spoken = out["say_this"].lower()
    assert "could not reach" in spoken
    assert "searched" not in spoken
    # nothing was fetched, quarantined or installed
    assert not any("capability-quarantine" in str(p) for p in tmp_path.rglob("*"))


# --- every resolve outcome is spoken truthfully (verifier repair) -------------


def _resolve(tmp_path, **gates):
    return C.resolve_missing_capability(
        "need a pdf reader",
        local_catalogue=lambda query, limit: [],
        marketplace_sources=[Marketplace([{"name": "pdf-reader", "category": "documents"}])],
        quarantine_root=tmp_path / "quarantine",
        scanner=clean_scan,
        **gates,
    )


_YES = {"approval_gate": lambda candidate: C.GateDecision(True, "approved"),
        "sandbox_gate": lambda candidate, query: C.GateDecision(True, "sandbox passed")}


def test_activation_without_a_retry_is_not_reported_as_a_retried_task(tmp_path):
    result = _resolve(tmp_path, activator=lambda candidate: {"capability": "pdf_reader"}, **_YES)

    assert result.decision == C.CANDIDATE_ACTIVATED
    assert result.retry_result is None
    assert "retried" not in result.message
    spoken = C.spoken_summary(result).lower()
    assert "pdf-reader" in spoken and "not run your original task" in spoken


def test_a_failing_retry_is_reported_as_a_failed_task_not_a_completion(tmp_path):
    def retry(original_task, activation):
        raise RuntimeError("tool returned 500")

    result = _resolve(tmp_path, activator=lambda candidate: {"capability": "pdf_reader"},
                      retry_original=retry, **_YES)

    assert result.decision == C.ORIGINAL_TASK_FAILED
    assert "tool returned 500" in result.message
    assert result.retry_result is None
    spoken = C.spoken_summary(result).lower()
    assert "failed" in spoken and "runtimeerror" in spoken
    # The exception text stays in the structured result, never in speech.
    assert "tool returned 500" not in spoken


def test_spoken_summary_for_activation_failure_and_successful_retry(tmp_path):
    def boom(candidate):
        raise RuntimeError("install refused")

    failed_result = _resolve(tmp_path / "a", activator=boom, **_YES)
    assert failed_result.decision == C.ACTIVATION_FAILED
    assert "install refused" in failed_result.message
    failed = C.spoken_summary(failed_result).lower()
    assert "pdf-reader" in failed and "runtimeerror" in failed
    assert "install refused" not in failed
    assert "none had" not in failed

    done = _resolve(tmp_path / "b", activator=lambda candidate: {"capability": "pdf_reader"},
                    retry_original=lambda task, activation: {"status": "ok"}, **_YES)
    assert done.decision == C.ORIGINAL_TASK_RETRIED
    spoken = C.spoken_summary(done).lower()
    assert "pdf-reader" in spoken and "original task" in spoken


@pytest.mark.parametrize("decision", [
    C.POLICY_APPROVAL_REQUIRED, C.SANDBOX_NOT_CONFIGURED, C.SANDBOX_TEST_FAILED,
    C.ACTIVATION_NOT_CONFIGURED, C.ACTIVATION_FAILED,
    C.CANDIDATE_ACTIVATED, C.ORIGINAL_TASK_FAILED, C.ORIGINAL_TASK_RETRIED,
])
def test_a_found_candidate_is_never_spoken_as_no_match(decision, tmp_path):
    result = _resolve(tmp_path)
    result.decision = decision

    spoken = C.spoken_summary(result).lower()

    assert "pdf-reader" in spoken
    assert "none had" not in spoken and "could not reach" not in spoken


def test_spoken_summary_refuses_an_outcome_it_does_not_know(tmp_path):
    result = _resolve(tmp_path)
    result.decision = "SOMETHING_NEW"

    with pytest.raises(ValueError):
        C.spoken_summary(result)


def _live_search(monkeypatch, tmp_path, autonomy):
    import asyncio
    import functools
    import json

    import agent_friday as af
    from friday import policy

    monkeypatch.setattr(af, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(policy, "default_engine", policy.PolicyEngine(autonomy=autonomy))
    monkeypatch.setattr(C, "default_marketplace_sources",
                        lambda: [Marketplace([{"name": "pdf-reader", "category": "documents"}])])
    # Pin the scan so the test does not depend on the scanner installed here;
    # the approval, sandbox and activation gates stay the live defaults.
    monkeypatch.setattr(C, "resolve_missing_capability",
                        functools.partial(C.resolve_missing_capability, scanner=clean_scan))
    agent = af.FridayAgent.__new__(af.FridayAgent)
    agent._router = af.capability_router.Router()
    agent._router.all_tools = {"zz_present": object()}
    agent._tools_offline = False
    return json.loads(asyncio.run(
        af.FridayAgent.search_capabilities.__wrapped__(agent, "need a pdf reader")))


def test_live_search_asks_for_approval_for_a_passing_candidate(monkeypatch, tmp_path):
    from friday import policy

    out = _live_search(monkeypatch, tmp_path, policy.GUARDED)

    missing = out["missing_capability"]
    assert missing["decision"] == C.POLICY_APPROVAL_REQUIRED
    assert [c["name"] for c in missing["passing_candidates"]] == ["pdf-reader"]
    spoken = out["say_this"].lower()
    assert "pdf-reader" in spoken and "approval" in spoken
    assert "none had" not in spoken
    # Nothing on the live path can sandbox or activate it, so FRIDAY must not
    # promise that approval leads anywhere.
    assert "not wired" in spoken
    assert "then a sandbox test" not in spoken


def test_live_search_under_full_autonomy_reports_the_sandbox_gate(monkeypatch, tmp_path):
    from friday import policy

    out = _live_search(monkeypatch, tmp_path, policy.FULL)

    missing = out["missing_capability"]
    # No sandbox exists on the live path, so no sandbox test ran or failed.
    assert missing["decision"] == C.SANDBOX_NOT_CONFIGURED
    spoken = out["say_this"].lower()
    assert "pdf-reader" in spoken and "no sandbox" in spoken
    assert "did not pass" not in spoken
    assert "none had" not in spoken and "activated it" not in spoken


# --- an unconfigured gate is not a failed gate (review repair) ----------------


def test_a_missing_sandbox_gate_is_not_reported_as_a_failed_sandbox_test(tmp_path):
    activated = []

    result = _resolve(tmp_path, approval_gate=lambda candidate: C.GateDecision(True, "ok"),
                      activator=lambda candidate: activated.append(candidate.entry.name))

    assert result.decision == C.SANDBOX_NOT_CONFIGURED
    assert activated == []
    spoken = C.spoken_summary(result).lower()
    assert "no sandbox" in spoken and "pdf-reader" in spoken
    assert "did not pass" not in spoken


def test_a_real_sandbox_failure_is_still_reported_as_a_failed_test(tmp_path):
    result = _resolve(tmp_path,
                      approval_gate=lambda candidate: C.GateDecision(True, "ok"),
                      sandbox_gate=lambda candidate, query: C.GateDecision(False, "3 tests failed"),
                      activator=lambda candidate: {"capability": "pdf_reader"})

    assert result.decision == C.SANDBOX_TEST_FAILED
    assert result.message == "3 tests failed"
    assert "did not pass the sandbox test" in C.spoken_summary(result).lower()


def test_a_missing_activator_is_not_reported_as_a_failed_activation(tmp_path):
    result = _resolve(tmp_path, **_YES)

    assert result.decision == C.ACTIVATION_NOT_CONFIGURED
    spoken = C.spoken_summary(result).lower()
    assert "pdf-reader" in spoken and "not wired" in spoken
    assert "failed" not in spoken


def test_approval_is_only_invited_when_the_rest_of_the_pipeline_is_wired(tmp_path):
    wired = _resolve(tmp_path / "a",
                     approval_gate=lambda candidate: C.GateDecision(False, "ask"),
                     sandbox_gate=lambda candidate, query: C.GateDecision(True, "ok"),
                     activator=lambda candidate: {"capability": "pdf_reader"})
    unwired = _resolve(tmp_path / "b",
                       approval_gate=lambda candidate: C.GateDecision(False, "ask"))

    assert wired.decision == unwired.decision == C.POLICY_APPROVAL_REQUIRED
    assert wired.activation_wired is True and unwired.activation_wired is False
    assert "needs your approval" in C.spoken_summary(wired).lower()
    unwired_spoken = C.spoken_summary(unwired).lower()
    assert "not wired" in unwired_spoken and "needs your approval" not in unwired_spoken
