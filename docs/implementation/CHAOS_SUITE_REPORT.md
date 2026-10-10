# Chaos suite report (CHAOS-001)

Generated 2026-10-10T00:28:08+00:00 with `F:/F.R.I.D.A.Y/.venv-build/Scripts/python.exe` in 26s; pytest exit 0.

**9/9 faults green.**

| Fault | Result | Tests (cases) |
|---|---|---|
| browser_crash | PASS | `tests/test_chaos_suite.py::test_browser_crash_mid_task_fails_truthfully` (1) PASS |
| provider_outage | PASS | `tests/test_provider_outage.py::test_an_outage_does_not_end_the_run` (1) PASS<br>`tests/test_provider_outage.py::test_the_task_succeeds_once_the_provider_comes_back` (1) PASS |
| hermes_crash | PASS | `tests/test_hermes_engine.py::test_hermes_executor_reports_unavailable_not_a_crash` (1) PASS<br>`tests/test_chaos_suite.py::test_hermes_crash_mid_run_fails_truthfully` (3) PASS |
| invalid_model_response | PASS | `tests/test_chaos_suite.py::test_an_invalid_model_answer_leaves_the_deterministic_plan_not_a_crash` (9) PASS<br>`tests/test_resilience.py::test_the_empty_completion_is_recognised` (1) PASS<br>`tests/test_resilience.py::test_the_exhausted_form_is_recognised_too` (1) PASS |
| db_contention | PASS | `tests/test_store_durability.py::test_kill_during_write_leaves_a_consistent_database` (1) PASS<br>`tests/test_store_durability.py::test_two_processes_write_concurrently_without_lock_errors` (1) PASS |
| network_loss | PASS | `tests/test_chaos_suite.py::test_network_loss_marks_the_task_degraded_and_recovers` (4) PASS<br>`tests/test_chaos_suite.py::test_network_loss_that_never_ends_fails_truthfully` (1) PASS |
| malformed_tool_output | PASS | `tests/test_chaos_suite.py::test_malformed_tool_output_is_a_failure_not_success` (4) PASS |
| worker_death | PASS | `tests/test_chaos_restart.py::test_control_plane_killed_mid_task_resumes_in_a_fresh_process` (1) PASS<br>`tests/test_chaos_suite.py::test_worker_death_with_two_runs_in_flight_resumes_both_exactly_once` (1) PASS |
| reboot | PASS | `tests/test_chaos_suite.py::test_cold_boot_sweeps_stale_leases_and_names_the_orphan` (1) PASS |
