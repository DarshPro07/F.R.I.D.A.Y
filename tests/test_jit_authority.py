"""
Authority is checked again at the moment of action (PRD v7 JIT-001).

A plan is made against the capabilities as they were then. If one of them
changes before it runs - its code, or what it is registered to do - the run
does not carry on with something nobody planned for: the call is refused at
execution and the change named, until the objective is revalidated.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from friday import capabilities as C
from friday import capability_runtime as CR
from friday import contracts as c
from friday import objectives as O
from friday import policy as PO
from friday.store import Store


@pytest.fixture
def compiled(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    created = O.compile_objective(
        store, request="check the system", objective_summary="check the system",
        tasks=[{"capability": "system_get_info", "arguments": {}}],
        manifest=[{"id": "system_get_info", "description": "info"}],
        provenance=c.PERSON, attended=True)
    raw = json.loads(store.objective_run(created["run_id"])["envelope"])
    return store, created["run_id"], c.Envelope.from_dict(raw)


def _run_under(envelope):
    return c.Run(run_id=c.new_run_id(), request="x", provenance=envelope.provenance,
                 attended=envelope.attended, envelope=envelope)


def test_the_plan_records_what_each_capability_was(compiled):
    _, _, envelope = compiled
    assert envelope.capability_hashes == {"system_get_info": CR.fingerprint("system_get_info")}


def test_an_unchanged_capability_runs(compiled):
    _, _, envelope = compiled
    result = CR.CapabilityRuntime(engine=PO.PolicyEngine(autonomy=PO.FULL)).execute(
        "system_get_info", {}, run=_run_under(envelope))
    assert result.status == c.SUCCEEDED, result.error


def test_a_capability_changed_since_the_plan_is_refused_at_execution(compiled):
    _, _, envelope = compiled
    planned_against_another_version = dataclasses.replace(
        envelope, capability_hashes={"system_get_info": "0" * 64})
    result = CR.CapabilityRuntime(engine=PO.PolicyEngine(autonomy=PO.FULL)).execute(
        "system_get_info", {}, run=_run_under(planned_against_another_version))
    assert result.status == c.NOT_PERMITTED
    assert result.error.startswith("JIT:") and "system_get_info" in result.error


def test_the_fingerprint_follows_what_the_capability_is_registered_to_do(monkeypatch):
    before = CR.fingerprint("system_get_info")
    original = C.by_id("system_get_info")
    # The hints must agree with side_effect (CAP-004), so a record that now
    # acts outside the process says so in all of them, not only in one.
    changed = dataclasses.replace(original, side_effect="external_action",
                                  read_only=False, external_side_effect=True,
                                  idempotent=False)
    monkeypatch.setattr(C, "by_id", lambda cid: changed if cid == "system_get_info" else original)
    assert CR.fingerprint("system_get_info") != before


def test_the_fingerprint_follows_the_code_it_runs(monkeypatch):
    before = CR.fingerprint("system_get_info")
    monkeypatch.setattr(CR, "_function_source",
                        lambda module, function: "def system_get_info(run): return 'else'")
    assert CR.fingerprint("system_get_info") != before


def test_a_narrower_envelope_cannot_swap_a_capability_version(compiled):
    _, _, envelope = compiled
    with pytest.raises(c.ContractError, match="version"):
        envelope.narrow(capability_hashes={"system_get_info": "f" * 64})


def test_a_narrower_envelope_cannot_drop_a_capability_version(compiled):
    """Dropping a planned version would switch the check at the moment of
    action off for that capability; a child may pin more, never fewer."""
    _, _, envelope = compiled
    with pytest.raises(c.ContractError, match="version"):
        envelope.narrow(capability_hashes={})


def test_a_stored_envelope_keeps_the_versions():
    envelope = O.envelope_for("RUN-jit", ["system_get_info"], provenance=c.PERSON,
                              attended=True)
    assert envelope.capability_hashes, "compile recorded no versions"
    assert c.Envelope.from_dict(json.loads(json.dumps(envelope.to_dict()))) == envelope


def test_compile_records_the_versions_on_an_envelope_it_is_handed(tmp_path):
    """A caller cannot know what each capability is now; compile writes the
    versions itself, and an envelope that pins a different one is refused."""
    store = Store(tmp_path / "ada.sqlite3")

    def compile_with(run_id, hashes):
        envelope = dataclasses.replace(
            O.envelope_for(run_id, ["system_get_info"], provenance=c.PERSON, attended=True),
            capability_hashes=hashes)
        return O.compile_objective(
            store, request="check the system", objective_summary="check the system",
            tasks=[{"capability": "system_get_info", "arguments": {}}],
            manifest=[{"id": "system_get_info", "description": "info"}],
            provenance=c.PERSON, attended=True, run_id=run_id, envelope=envelope)

    run_id = compile_with("OBJ-JIT-1", {})["run_id"]
    stored = c.Envelope.from_dict(json.loads(store.objective_run(run_id)["envelope"]))
    assert stored.capability_hashes == {"system_get_info": CR.fingerprint("system_get_info")}
    with pytest.raises(c.ContractError, match="version"):
        compile_with("OBJ-JIT-2", {"system_get_info": "f" * 64})


@pytest.fixture
def probes(monkeypatch):
    monkeypatch.setattr(CR, "_authority_probes", {})
    return CR


def _execute_capability(capability_id, arguments=None):
    return CR.CapabilityRuntime(engine=PO.PolicyEngine(autonomy=PO.FULL)).execute(
        capability_id, arguments or {})


def _execute(arguments=None):
    return _execute_capability("system_get_info", arguments)


def test_a_capability_with_no_probe_runs(probes):
    assert _execute().status == c.SUCCEEDED


def test_authentication_lost_since_the_plan_is_refused_at_execution(probes):
    probes.register_authority_probe("system_get_info", lambda args: "the session has expired")
    result = _execute()
    assert result.status == c.NOT_PERMITTED
    assert result.error.startswith("JIT:") and "session has expired" in result.error


def test_a_target_that_changed_is_refused_using_the_arguments(probes):
    probes.register_authority_probe(
        "system_get_info", lambda args: "target is locked" if args.get("target") == "x" else "")
    assert _execute({}).status == c.SUCCEEDED
    assert _execute({"target": "x"}).status == c.NOT_PERMITTED


def test_a_probe_that_cannot_answer_refuses(probes):
    def broken(args):
        raise TimeoutError("auth service silent")

    probes.register_authority_probe("system_get_info", broken)
    result = _execute()
    assert result.status == c.NOT_PERMITTED and "auth service silent" in result.error


def test_a_file_that_vanished_since_the_plan_is_refused_by_its_probe(tmp_path, monkeypatch):
    from friday.toolsets import files as F

    target = tmp_path / "note.txt"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(F, "_jail", F.FileJail(roots=(tmp_path,)))
    probe = F._target_state_probe("path")
    assert probe({"path": str(target)}) == ""
    target.unlink()
    assert "no longer exists" in probe({"path": str(target)})


def test_the_file_capabilities_register_a_probe():
    from friday.toolsets import files as F  # noqa: F401 - registers on import

    for capability in ("files_edit", "files_copy", "files_move", "files_recycle", "files_delete",
                       "files_create", "files_write"):
        assert CR._authority_probes.get(capability), capability


def test_a_file_that_took_the_place_of_the_destination_folder_is_refused(tmp_path, monkeypatch):
    from friday.toolsets import files as F

    destination = tmp_path / "reports" / "new.txt"
    monkeypatch.setattr(F, "_jail", F.FileJail(roots=(tmp_path,)))
    for must_be_absent in (True, False):
        probe = F._destination_state_probe("path", must_be_absent)
        assert probe({"path": str(destination)}) == ""
    (tmp_path / "reports").write_text("not a folder", encoding="utf-8")
    for must_be_absent in (True, False):
        probe = F._destination_state_probe("path", must_be_absent)
        assert "destination folder is now a file" in probe({"path": str(destination)})


def test_a_destination_in_a_folder_not_made_yet_is_allowed(tmp_path, monkeypatch):
    from friday.toolsets import files as F

    destination = tmp_path / "src" / "deeper" / "new.py"
    monkeypatch.setattr(F, "_jail", F.FileJail(roots=(tmp_path,)))
    for must_be_absent in (True, False):
        assert F._destination_state_probe("path", must_be_absent)({"path": str(destination)}) == ""


def test_a_create_over_a_file_that_appeared_since_the_plan_is_refused(tmp_path, monkeypatch):
    from friday.toolsets import files as F

    destination = tmp_path / "new.txt"
    monkeypatch.setattr(F, "_jail", F.FileJail(roots=(tmp_path,)))
    probe = F._destination_state_probe("path", True)
    assert probe({"path": str(destination)}) == ""
    destination.write_text("someone else's", encoding="utf-8")
    assert "destination already exists" in probe({"path": str(destination)})


def test_a_write_over_an_existing_file_is_still_allowed(tmp_path, monkeypatch):
    from friday.toolsets import files as F

    destination = tmp_path / "note.txt"
    destination.write_text("old", encoding="utf-8")
    monkeypatch.setattr(F, "_jail", F.FileJail(roots=(tmp_path,)))
    assert F._destination_state_probe("path", False)({"path": str(destination)}) == ""


def test_a_destination_moved_out_of_the_jail_is_refused(tmp_path, monkeypatch):
    from friday.toolsets import files as F

    root = tmp_path / "jail"
    root.mkdir()
    monkeypatch.setattr(F, "_jail", F.FileJail(roots=(root,)))
    outside = tmp_path / "elsewhere.txt"
    for must_be_absent in (True, False):
        probe = F._destination_state_probe("path", must_be_absent)
        assert "no longer reachable" in probe({"path": str(outside)})


def test_a_destination_probe_with_no_path_names_nothing():
    from friday.toolsets import files as F

    probe = F._destination_state_probe("path", True)
    assert probe({}) == "" and probe({"path": ""}) == "" and probe({"path": 3}) == ""


def test_the_browser_capabilities_register_a_probe():
    from friday.toolsets import web as W  # noqa: F401 - registers on import

    for capability in ("browser_act", "browser_automate"):
        assert CR._authority_probes.get(capability), capability


def test_a_step_on_a_page_whose_session_has_closed_is_refused_at_execution(monkeypatch):
    from friday.toolsets import web as W

    monkeypatch.setattr(W.session, "_page", None)
    result = _execute_capability("browser_act", {"primitive": "click", "target": "#go"})
    assert result.status == c.NOT_PERMITTED
    assert result.error.startswith("JIT:") and "no longer running" in result.error
    assert "before click" in result.error


def test_opening_a_page_needs_no_running_session(monkeypatch):
    from friday.toolsets import web as W

    monkeypatch.setattr(W.session, "_page", None)
    for primitive in ("open", "navigate", " Open ", "", "no-such-primitive"):
        assert CR.authority_refusal("browser_act", {"primitive": primitive}) == "", primitive


def test_a_step_on_a_running_session_is_not_refused(monkeypatch):
    from friday.toolsets import web as W

    monkeypatch.setattr(W.session, "_page", object())
    for primitive in ("click", "type", "inspect", "screenshot", "verify"):
        assert CR.authority_refusal("browser_act", {"primitive": primitive}) == "", primitive


def test_browser_automation_without_its_credential_is_refused_at_execution(monkeypatch):
    from friday.toolsets import web as W  # noqa: F401 - registers on import

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    result = _execute_capability("browser_automate", {"task": "find the weather"})
    assert result.status == c.NOT_PERMITTED
    assert result.error.startswith("JIT:") and "credential is no longer configured" in result.error


def test_browser_automation_with_its_credential_is_not_refused_and_never_echoes_it(monkeypatch):
    from friday.toolsets import web as W  # noqa: F401 - registers on import

    monkeypatch.setenv("GOOGLE_API_KEY", "sentinel-key-value")
    assert CR.authority_refusal("browser_automate", {"task": "find the weather"}) == ""
    monkeypatch.setenv("GOOGLE_API_KEY", "   ")
    assert "credential" in CR.authority_refusal("browser_automate", {"task": "x"})
    monkeypatch.setenv("GOOGLE_API_KEY", "sentinel-key-value")
    refusal = CR.authority_refusal(
        "browser_automate", {"task": "x", "start_url": "https://www.paypal.com/signin"})
    assert refusal and "sentinel-key-value" not in refusal


def test_the_first_call_meets_the_probe_its_module_registers_on_import(monkeypatch):
    """A probe is registered when its module is imported. The module is loaded
    before the probes are asked, so a capability's first call is checked too."""
    loaded = []

    class Stub:
        def load(self):
            loaded.append(True)
            CR.register_authority_probe("system_get_info", lambda args: "no longer valid")
            return lambda run, **arguments: pytest.fail("ran past a refusing probe")

    monkeypatch.setattr(CR, "_authority_probes", {})
    monkeypatch.setattr(CR, "resolutions", lambda: {"system_get_info": Stub()})
    result = _execute_capability("system_get_info", {})
    assert loaded == [True]
    assert result.status == c.NOT_PERMITTED
    assert result.error.startswith("JIT:") and "no longer valid" in result.error
