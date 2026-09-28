"""
PRD §79's critical negative controls, and the red-path tests that are them.

"A guard that cannot be made red is not strong evidence." Each entry names
the tests that plant the bad thing - the dummy secret, the forged evidence,
the expired yes - and require the guard to refuse it. `open_row` names the
v7 ledger row that owns building a control that does not exist yet. An
entry with neither is a gap nobody owns, and `tests/test_negative_controls.py`
refuses it.

The release gate (`scripts/release_gate.py::gate_negative_controls`) runs
every test named here and is not PASS while any entry has no test: the PRD
says a red path is required for release, not that one is planned.

Not a test module (no `test_` prefix), so pytest does not collect it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Control:
    #: Pytest node IDs of the red-path tests. Each must be collected offline.
    tests: tuple[str, ...] = ()
    #: The v7 ledger row that owns the missing control, when `tests` is empty.
    open_row: str = ""
    #: What the tests prove, in one line, for the release report.
    proves: str = ""
    #: Tests that may skip on a host lacking a capability, and why. The gate
    #: reports such a skip as a limitation; any other skip fails it.
    host_skips: dict[str, str] = field(default_factory=dict)


CONTROLS: dict[str, Control] = {
    "planted dummy secret": Control(
        tests=("tests/test_promotion.py::test_a_secret_in_a_changed_file_is_refused",),
        proves="four planted key shapes in a changed file refuse promotion "
               "(SECRET_IN_DIFF)"),
    "cross-workspace read": Control(
        tests=("tests/test_workspace_isolation.py::test_recall_cannot_cross",
               "tests/test_workspace_isolation.py::test_free_text_search_cannot_cross",
               "tests/test_workspace_isolation.py::test_an_unfiltered_query_really_would_leak",
               "tests/test_memory_contract.py::test_a_projects_memory_never_leaks_into_another_project"),
        proves="one workspace cannot recall or search another's rows, and "
               "the same query without the filter would have leaked them"),
    "fake action evidence": Control(
        tests=("tests/test_browser_evidence_chain.py::test_a_forged_ok_on_the_step_does_not_survive_verification",
               "tests/test_invariants.py::test_audit_a_completion_claim_the_run_does_not_back_is_refused",
               "tests/test_invariants.py::test_completion_a_run_cannot_be_completed_without_passing_evidence"),
        proves="a hand-set ok, and a completion the run's evidence does not "
               "back, are refused"),
    "stale capability health": Control(
        tests=("tests/test_provider_health.py::test_old_evidence_expires_into_stale_never_into_healthy",),
        proves="health evidence older than its window is STALE, never HEALTHY"),
    "protected-kernel write": Control(
        tests=("tests/test_self_upgrade.py::test_kernel_surface_is_refused",
               "tests/test_invariants.py::test_trust_every_alias_of_a_kernel_path_is_refused",
               "tests/test_invariants.py::test_trust_a_symlink_into_the_kernel_is_refused_by_the_real_path"),
        proves="a self-upgrade touching a kernel file is refused and "
               "journaled, under every alias and through a symlink",
        host_skips={"tests/test_invariants.py::test_trust_a_symlink_into_the_kernel_is_refused_by_the_real_path":
                    "creating a symlink needs a privilege some Windows hosts do not grant"}),
    "lossy compaction": Control(
        tests=("tests/test_compaction.py::test_dropping_any_one_fact_is_rejected",
               "tests/test_compaction.py::test_a_literal_in_other_words_is_not_kept",
               "tests/test_envelope_hermes.py::test_a_delegation_that_would_drop_a_critical_fact_is_not_sent"),
        proves="a packet missing any one critical fact, of any kind, or "
               "carrying a literal in other words, is refused, and an "
               "objective's delegation that would drop one is not sent"),
    "prompt injection": Control(
        tests=("tests/test_trust.py::test_a_payload_that_declares_itself_trusted_stays_untrusted",
               "tests/test_trust.py::test_injected_page_content_cannot_reach_destructive_tools",
               "tests/test_browser_confirmation.py::test_a_yes_written_into_the_arguments_does_not_authorise_an_external_write"),
        proves="read material that claims trust stays untrusted and cannot "
               "reach a destructive tool; a yes written into arguments "
               "authorises nothing"),
    "unauthorized network host": Control(
        tests=("tests/test_netguard.py::test_non_public_addresses_are_refused",
               "tests/test_netguard.py::test_fetch_refuses_a_url_that_check_refuses",
               "tests/test_browser_capability.py::test_private_addresses_blocked_by_netguard",
               "tests/test_trust.py::test_out_of_scope_host_and_action_are_blocked_even_when_the_tool_asks"),
        proves="private, loopback and metadata hosts are refused, and a "
               "security task cannot touch a host outside its authorised "
               "scope (a per-objective host scope is NET-001)"),
    "expired authority": Control(
        tests=("tests/test_confirmation.py::test_an_expired_confirmation_cannot_be_consumed",
               "tests/test_confirmation.py::test_an_expired_confirmation_cannot_be_approved",
               "tests/test_invariants.py::test_authority_no_form_of_non_approval_becomes_authorization"),
        proves="an expired yes can be neither approved nor spent, and no "
               "form of non-approval authorises anything"),
    "duplicated side effect": Control(
        tests=("tests/test_invariants.py::test_scheduler_one_execution_key_has_one_side_effect_across_a_crash",
               "tests/test_confirmation.py::test_a_confirmation_cannot_be_spent_twice"),
        proves="one execution key has one side effect across a crash, and "
               "one yes is spent once"),
    "invented capability": Control(
        tests=("tests/test_planner_model.py::test_a_hallucinated_capability_never_reaches_the_plan",
               "tests/test_objective_dispatch.py::test_dispatch_unknown_capability_raises_lookup_error"),
        proves="a model-named tool never reaches the plan, and dispatch "
               "refuses a capability that is not registered"),
    "evaluator mutant": Control(
        tests=("tests/test_invariants.py::test_every_invariant_guard_is_load_bearing",
               "tests/test_selfdev.py::test_every_judge_surface_is_kernel"),
        proves="removing each invariant guard turns its attack red, and a "
               "run cannot edit the judges that grade it"),
}
