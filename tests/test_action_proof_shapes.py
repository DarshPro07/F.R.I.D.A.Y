"""Per-type proof shapes, correlation keys and failure claims (ACT-001)."""
from __future__ import annotations

import pytest

from friday import contracts as c
from friday import honesty as H

HASH = "ab12" * 16


def _run(tool: str, method: str, evidence: str, *, path: str = "C:/w/foo.txt",
         artifact: bool = False, correlation: c.Correlation | None = None):
    run = c.Run.create(f"do {tool}", capability=tool)
    prior = c.started(run.run_id, tool)
    v = c.Verification(method=method, evidence=evidence)
    arts = ()
    if artifact:
        arts = (c.new_artifact(run_id=run.run_id, type="file", title="out.txt",
                               path_or_uri=path, producer=tool, verification=v),)
    run.record(c.succeeded(prior, output={"path": path}, artifacts=arts,
                           verification=v, correlation=correlation))
    return run


@pytest.mark.parametrize("tool,good,weak", [
    ("files_write", ("readback", f"foo.txt sha256={HASH}"),
     ("call", "foo.txt written, 12 bytes")),
    ("claude_code_run", ("diff+tests", "foo.txt diff_files=3 tests_passed=12"),
     ("diff", "no diff, tests not run")),
    ("browser_click", ("post-state", "foo.txt post_state=saved-banner"),
     ("click", "foo.txt clicked")),
    ("email_send", ("provider", "foo.txt sent message_id=<a@b.c>"),
     ("smtp", "foo.txt sent")),
])
def test_default_audit_refuses_weak_proof_and_accepts_typed_proof(tool, good, weak):
    # No flag: the default path is the strict one.
    assert H.audit("I created foo.txt.", _run(tool, *good)).ok
    refused = H.audit("I created foo.txt.", _run(tool, *weak))
    assert not refused.ok
    assert "action" in refused.reason


def test_negated_or_zero_evidence_does_not_satisfy_a_shape():
    for evidence in ("foo.txt diff_files=0 tests_passed=0",
                     "foo.txt no diff_files=3 tests_passed=0",
                     "foo.txt without sha256=" + HASH):
        tool = "files_write" if "sha256" in evidence else "claude_code_run"
        assert not H.audit("I created foo.txt.", _run(tool, "x", evidence)).ok


def test_simulation_needs_a_recorded_artifact():
    assert not H.audit("I created foo.txt.", _run("sim_run", "run", "foo.txt ok")).ok
    assert H.audit("I created foo.txt.",
                   _run("sim_run", "run", "foo.txt ok", artifact=True)).ok


def test_typed_proof_for_target_a_cannot_back_target_b():
    run = _run("files_write", "readback", f"foo.txt sha256={HASH}")
    assert not H.audit("I created bar.txt.", run).ok


# --- correlation keys ------------------------------------------------------


def _corr(**kw):
    return c.Correlation(**{"workspace": "w1", "objective": "OBJ-1",
                            "version": "v7", "action": "write",
                            "target": "C:/w/foo.txt",
                            "executor": "hermes", "operation": "op-1", **kw})


def test_evidence_for_another_objective_cannot_back_a_claim():
    run = _run("files_write", "readback", f"foo.txt sha256={HASH}",
               correlation=_corr(objective="OBJ-2"))
    audit = H.audit("I created foo.txt.", run, correlate={"objective": "OBJ-1"})
    assert not audit.ok and "objective" in audit.reason


@pytest.mark.parametrize("key,value", [
    ("workspace", "w2"), ("version", "v8"), ("action", "delete"),
    ("target", "C:/w/bar.txt"), ("executor", "claude"), ("operation", "op-2")])
def test_every_correlation_key_is_enforced(key, value):
    run = _run("files_write", "readback", f"foo.txt sha256={HASH}",
               correlation=_corr())
    assert H.audit("I created foo.txt.", run, correlate={key: "same"}).ok is False
    assert H.audit("I created foo.txt.", run,
                   correlate={key: getattr(_corr(), key)}).ok
    assert not H.audit("I created foo.txt.", run, correlate={key: value}).ok


def test_uncorrelated_evidence_cannot_satisfy_an_expectation():
    run = _run("files_write", "readback", f"foo.txt sha256={HASH}")
    assert not H.audit("I created foo.txt.", run,
                       correlate={"workspace": "w1"}).ok


def test_evidence_older_than_the_run_is_refused_by_default():
    run = _run("files_write", "readback", f"foo.txt sha256={HASH}",
               correlation=_corr(timestamp="2000-01-01T00:00:00+00:00"))
    audit = H.audit("I created foo.txt.", run)
    assert not audit.ok and "before" in audit.reason


# --- failure claims --------------------------------------------------------


def test_a_failure_claim_with_no_attempt_on_record_is_refused():
    run = c.Run.create("send it", capability="email_send")
    audit = H.audit("The upload failed.", run)
    assert not audit.ok and "attempt" in audit.reason
    assert not H.audit("I tried to send it but it failed.", None).ok


def test_a_failure_claim_backed_by_a_recorded_failure_passes():
    run = c.Run.create("send it", capability="email_send")
    run.record(c.failed(c.started(run.run_id, "email_send"), "smtp refused"))
    assert H.audit("The send failed.", run).ok


def test_a_failure_claim_about_another_objective_is_refused():
    run = c.Run.create("send it", capability="email_send")
    run.record(c.failed(c.started(run.run_id, "email_send"), "smtp refused",
                        correlation=_corr(objective="OBJ-2")))
    assert not H.audit("The send failed.", run,
                       correlate={"objective": "OBJ-1"}).ok


# --- correlation by default ------------------------------------------------


def _declared_run(declared, recorded, *, tool="files_write"):
    """A run declaring `declared`, with one verified write recorded under `recorded`."""
    run = c.Run.create("write foo.txt", capability=tool, correlation=declared)
    prior = c.started(run.run_id, tool, correlation=recorded)
    v = c.Verification(method="readback", evidence=f"foo.txt sha256={HASH}")
    run.record(c.succeeded(prior, output={"path": "C:/w/foo.txt"}, verification=v))
    return run


def test_a_run_declared_correlation_is_enforced_without_being_asked():
    declared = c.Correlation(workspace="w1", objective="OBJ-1")
    assert H.audit("I created foo.txt.", _declared_run(declared, _corr())).ok
    other = H.audit("I created foo.txt.",
                    _declared_run(declared, _corr(objective="OBJ-2")))
    assert not other.ok and "objective" in other.reason
    assert not H.audit("I created foo.txt.", _declared_run(declared, None)).ok


def test_a_caller_cannot_widen_what_the_run_declared():
    run = _declared_run(c.Correlation(objective="OBJ-1"), _corr())
    assert not H.audit("I created foo.txt.", run,
                       correlate={"objective": "OBJ-2"}).ok


def test_a_failure_under_another_declared_objective_backs_no_failure_claim():
    run = c.Run.create("send it", capability="email_send",
                       correlation=c.Correlation(objective="OBJ-1"))
    run.record(c.failed(c.started(run.run_id, "email_send",
                                  correlation=_corr(objective="OBJ-2")),
                        "smtp refused"))
    assert not H.audit("It failed.", run).ok


def test_evidence_time_is_compared_as_a_time_not_as_a_string():
    # 10:00 at +05:30 is 04:30 UTC - before a run that began at 06:00 UTC,
    # though as a string it sorts after it.
    def run_with(stamp):
        run = c.Run(run_id="RUN-tz", request="write foo.txt",
                    created_at="2020-01-01T06:00:00+00:00")
        prior = c.started(run.run_id, "files_write",
                          correlation=c.Correlation(timestamp=stamp))
        v = c.Verification(method="readback", evidence=f"foo.txt sha256={HASH}")
        run.record(c.succeeded(prior, output={"path": "C:/w/foo.txt"},
                               verification=v))
        return run

    early = "2020-01-01T10:00:00+05:30"
    assert early > "2020-01-01T06:00:00+00:00"
    assert not H.audit("I created foo.txt.", run_with(early)).ok
    assert H.audit("I created foo.txt.", run_with("2020-01-01T12:00:00+05:30")).ok


@pytest.mark.parametrize("started_at,stamp,why", [
    ("", "", "no timestamp"),
    (None, "last tuesday", "not a time"),
    (None, "2999-01-01T00:00:00+00:00", "future"),
])
def test_evidence_without_a_readable_time_inside_the_run_is_refused(
        started_at, stamp, why):
    run = c.Run.create("write foo.txt", capability="files_write")
    v = c.Verification(method="readback", evidence=f"foo.txt sha256={HASH}")
    run.record(c.ActionResult(
        run_id=run.run_id, tool_id="files_write", status=c.SUCCEEDED,
        started_at=c.now_iso() if started_at is None else started_at,
        output={"path": "C:/w/foo.txt"}, verification=v,
        correlation=c.Correlation(timestamp=stamp)))
    audit = H.audit("I created foo.txt.", run)
    assert not audit.ok and why in audit.reason


def test_a_recorded_target_is_the_only_target_the_evidence_backs():
    # The evidence text mentions bar.txt in passing; the action was about foo.txt.
    run = c.Run.create("copy it", capability="files_write")
    prior = c.started(run.run_id, "files_write",
                      correlation=c.Correlation(target="C:/w/foo.txt"))
    v = c.Verification(method="readback",
                       evidence=f"copied bar.txt to foo.txt sha256={HASH}")
    run.record(c.succeeded(prior, output={"path": "C:/w/foo.txt"}, verification=v))
    assert H.audit("I created foo.txt.", run).ok
    assert not H.audit("I created bar.txt.", run).ok
    assert not H.audit("I created foo.txt.", run,
                       correlate={"target": "D:/elsewhere/foo.txt"}).ok


# --- every tool has a proof shape --------------------------------------------


def test_a_tool_with_no_typed_shape_still_needs_an_observation():
    assert H.proof_shape_for("apps_open") is H.OBSERVATION
    claim = "I opened Spotify."
    assert not H.audit(claim, _run("apps_open", "launch", "Spotify launched")).ok
    assert H.audit(claim, _run("apps_open", "process", "Spotify.exe pid=4242")).ok
    for weak in ("Spotify.exe pid=0", "pid=none", "https://x.test/?pid=3 loaded"):
        assert not H.audit(claim, _run("apps_open", "process", weak)).ok, weak


# --- failure claims are matched to their attempt ----------------------------


def test_a_failure_claim_is_audited_beside_a_success_claim():
    run = _run("files_write", "readback", f"foo.txt sha256={HASH}")
    audit = H.audit("I created foo.txt. The upload failed.", run)
    assert not audit.ok and audit.unbacked == ("The upload failed.",)
    assert not H.audit("I created foo.txt but the upload failed.", run).ok


def test_failed_in_the_same_sentence_does_not_hide_a_success_claim():
    run = c.Run.create("send it", capability="email_send")
    run.record(c.failed(c.started(run.run_id, "email_send"), "smtp refused"))
    assert H.audit("The send failed.", run).ok
    assert not H.audit("I created foo.txt, but the send failed.", run).ok


def test_a_succeeded_action_is_not_evidence_that_something_failed():
    run = _run("email_send", "provider", "sent message_id=<a@b.c>")
    assert not H.audit("It failed.", run).ok
    assert not H.audit("The upload failed.", run).ok


def test_a_failure_claim_needs_an_attempt_of_its_kind_on_its_target():
    run = c.Run.create("tidy up", capability="files_delete")
    run.record(c.failed(c.started(run.run_id, "email_send"), "smtp refused"))
    assert "nothing in Friday" in H.audit("The upload failed.", run).reason
    assert not H.audit("Deleting foo.txt failed.", run).ok
    run.record(c.failed(
        c.started(run.run_id, "files_delete",
                  correlation=c.Correlation(target="C:/w/foo.txt")),
        "C:/w/foo.txt is locked"))
    assert H.audit("Deleting foo.txt failed.", run).ok
    assert not H.audit("Deleting bar.txt failed.", run).ok


@pytest.mark.parametrize("tool", ["executor.claude_code",
                                  "executor.claude_code.continue",
                                  "executor.hermes"])
def test_real_code_executor_ids_are_held_to_diff_and_tests(tool):
    # The ids production executors record, not stand-ins.
    assert H.proof_shape_for(tool).name == "code"
    claude_style = "diff_files=2; 2 file(s) changed in C:/w: a.py; tools used: Edit"
    assert not H.audit("I created foo.txt.", _run(tool, "worktree_diff", claude_style)).ok
    assert H.audit("I created foo.txt.", _run(
        tool, "worktree_diff", claude_style + " tests_passed=7")).ok


def test_cad_tool_id_needs_an_artifact():
    assert H.proof_shape_for("cad.generate").name == "simulation"
    bare = _run("cad.generate", "file_exists", "foo.txt, 41 KB")
    assert not H.audit("I created foo.txt.", bare).ok
    assert H.audit("I created foo.txt.", _run(
        "cad.generate", "file_exists", "foo.txt, 41 KB", artifact=True)).ok
