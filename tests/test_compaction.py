"""
A compacted packet keeps every critical fact, or it is refused (PRD v7
CTX-003, §19.8).

The facts come from what the objective engine recorded - the contract, the
tasks, the approvals, the envelope - and each one must reach the packet
verbatim. Dropping any single one, of any kind, is rejected; so is carrying a
literal in other words.
"""
from __future__ import annotations

import pytest

from friday import capabilities
from friday import compaction as K
from friday import contracts as c
from friday import objectives as O
from friday.store import Store


@pytest.fixture
def objective(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(
        goal="write the Q3 summary to 'Q3 report.md' and tidy the old drafts",
        purpose=("the board meets on Friday",),
        hard_constraints=("keep the numbers exactly as the finance sheet has them",),
        exact_literals=("Q3 report.md",),
        open_questions=("which drafts count as old?",),
        governance={"forbidden": ["power"], "protected": {"delete": ["config.yaml"]},
                    "max_side_effects": 5})
    created = O.compile_objective(
        store, request=contract.goal, objective_summary=contract.goal,
        tasks=[{"id": "t1", "capability": "files_create",
                "arguments": {"path": "Q3 report.md", "content": "summary"}},
               {"id": "t2", "capability": "files_delete", "arguments": {"path": "draft1.txt"}},
               {"id": "t3", "capability": "files_delete", "arguments": {"path": "draft2.txt"}}],
        manifest=capabilities.as_dicts(), provenance=c.PERSON, attended=True,
        contract=contract)
    run_id = created["run_id"]
    first, second, _ = store.objective_tasks(run_id)
    store.update_objective_task(first["task_id"], status=O.TaskStatus.SUCCEEDED,
                                evidence="wrote Q3 report.md (412 bytes)")
    store.update_objective_task(second["task_id"], status=O.TaskStatus.FAILED,
                                evidence="PermissionError: draft1.txt is open in Word")
    store.append_objective_approval(
        run_id, operation="files_delete", target="draft2.txt", parameters={"path": "draft2.txt"},
        decision="APPROVED", decided_by="boss", expires_at="2999-01-01T00:00:00+00:00",
        plan_hash=O.plan_hash(store, run_id), graph_version=1)
    return store, run_id


def test_every_kind_of_critical_fact_is_read_from_the_record(objective):
    store, run_id = objective
    facts = K.for_objective(store, run_id)
    text = facts.render()
    assert "OBJECTIVE: write the Q3 summary to 'Q3 report.md' and tidy the old drafts" in text
    assert "OBJECTIVE (why): the board meets on Friday" in text
    assert "EXACT: Q3 report.md" in text
    assert "MUST: keep the numbers exactly as the finance sheet has them" in text
    assert "MUST: never power" in text
    assert "MUST: do not delete config.yaml" in text
    assert "MUST: at most 5 changes" in text
    assert "DECIDED: boss approved files_delete draft2.txt" in text
    assert "OPEN QUESTION: which drafts count as old?" in text
    assert any(line.startswith("EVIDENCE: task ") and "files_create" in line
               for line in facts.evidence)
    assert any("draft1.txt" in line and "open in Word" in line for line in facts.avoid)
    assert facts.next_action == ('NEXT: files_delete {"path": "draft2.txt"}',)
    assert any(line.startswith("AUTHORITY: from PERSON, attended") for line in facts.authority)


def test_a_packet_that_carries_every_fact_is_accepted(objective):
    store, run_id = objective
    facts = K.for_objective(store, run_id)
    packet = "Where we are: one draft could not be removed.\n\n" + facts.render()
    K.validate(packet, facts)
    # Spacing and line breaks between words are not content.
    K.validate(packet.replace(": ", ":\n    "), facts)


def test_dropping_any_one_fact_is_rejected(objective):
    """The lossy-summary mutation, once per fact: whichever line a summary
    loses, of whichever kind, the packet is refused and the loss named."""
    store, run_id = objective
    facts = K.for_objective(store, run_id)
    lines = facts.lines()
    assert len(lines) >= 12
    for dropped in lines:
        packet = "\n".join(line for line in lines if line != dropped)
        with pytest.raises(K.LossyCompaction) as caught:
            K.validate(packet, facts)
        assert dropped in caught.value.missing


def test_a_literal_in_other_words_is_not_kept(objective):
    store, run_id = objective
    facts = K.for_objective(store, run_id)
    reworded = facts.render().replace("Q3 report.md", "q3 report.md")
    with pytest.raises(K.LossyCompaction, match="Q3 report.md"):
        K.validate(reworded, facts)


def test_a_literal_keeps_its_own_spacing(tmp_path):
    """Spacing between words is not content, except inside a literal the
    person gave: "draft  2.txt" with two spaces is a different file."""
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(goal="delete 'draft  2.txt'", exact_literals=("draft  2.txt",))
    created = O.compile_objective(
        store, request=contract.goal, objective_summary=contract.goal,
        tasks=[{"capability": "files_delete", "arguments": {"path": "draft  2.txt"}}],
        manifest=capabilities.as_dicts(), provenance=c.PERSON, attended=True, contract=contract)
    facts = K.for_objective(store, created["run_id"])
    K.validate(facts.render(), facts)
    with pytest.raises(K.LossyCompaction, match="draft  2.txt"):
        K.validate(facts.render().replace("draft  2.txt", "draft 2.txt"), facts)


def test_a_run_without_a_contract_still_keeps_its_request(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    created = O.compile_objective(
        store, request="check the system", objective_summary="check the system",
        tasks=[{"capability": "system_get_info", "arguments": {}}],
        manifest=[{"id": "system_get_info", "description": "info"}],
        provenance=c.PERSON, attended=True)
    facts = K.for_objective(store, created["run_id"])
    assert facts.objective == ("OBJECTIVE: check the system",)
    with pytest.raises(K.LossyCompaction):
        K.validate("carry on with the checks", facts)


def test_what_tools_recorded_is_kept_apart_from_what_the_person_gave(objective):
    """The person's objective and constraints bind a worker; what tools and
    the plan recorded is information about the run and must be presented as
    data. Every fact is one or the other, and nothing is both."""
    store, run_id = objective
    facts = K.for_objective(store, run_id)
    binding, recorded = facts.binding(), facts.recorded()
    assert set(binding) | set(recorded) == set(facts.lines())
    assert not set(binding) & set(recorded)
    assert all(line.split(":")[0] in {"OBJECTIVE", "OBJECTIVE (why)", "EXACT", "MUST", "DECIDED",
                                      "OPEN QUESTION", "AUTHORITY"} for line in binding)
    assert any("open in Word" in line for line in recorded)
    assert not any("open in Word" in line for line in binding)


def test_a_step_line_keeps_its_arguments_bounded(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    created = O.compile_objective(
        store, request="write the long note", objective_summary="write the long note",
        tasks=[{"capability": "files_create",
                "arguments": {"path": "long.txt", "content": "x" * 5000}}],
        manifest=capabilities.as_dicts(), provenance=c.PERSON, attended=True)
    (line,) = K.for_objective(store, created["run_id"]).next_action
    assert len(line) <= K.ARGUMENT_CHARS + 60, len(line)
