"""
Auditing every capability, from the registry rather than from prose.

A spoken request to audit everything produced 205 tasks - 132 succeeded, 68
failed, 5 skipped - because the planner read 1967 words as 205 instructions.
The list of things Friday can do was never in the sentence. It is in the
registry, and the registry is data.

What these gates hold is the property that makes an audit an audit: every
registered capability is accounted for, in exactly one group, with exactly one
honest strategy - including the ones that cannot be tested here, because those
are results and not gaps.
"""
from __future__ import annotations
import pytest
from friday import audit_planner as A
from friday import capabilities as C
AUDIT_REQUESTS = ['perform a complete real capability audit of yourself', 'test all your capabilities', 'verify everything Friday can do', 'check every capability you have', 'run a capability audit']
ORDINARY_REQUESTS = ['check my computer and open Paint', 'audit the spreadsheet for errors', 'test the login page', 'check the system']


@pytest.mark.parametrize("text", AUDIT_REQUESTS)
def test_an_audit_request_is_recognised(text):
    assert A.is_an_audit_request(text), text


@pytest.mark.parametrize("text", ORDINARY_REQUESTS)
def test_an_ordinary_request_is_not_an_audit(text):
    """
    The trigger has to be narrow. Reading "check the system" as a request to
    audit 125 capabilities would be a worse failure than the one it replaces.
    """
    assert not A.is_an_audit_request(text), text


def test_the_audit_covers_the_whole_registry():
    """
    The requirement, stated once. A capability missing from the audit is the
    one failure an audit must not have.
    """
    plan = A.plan_audit()

    assert plan.registered == len(C._ALL)
    assert plan.accounted_for == plan.registered, (
        f"{plan.registered} registered but {plan.accounted_for} in groups")


def test_every_capability_has_exactly_one_strategy():
    plan = A.plan_audit()

    registered = {capability.id for capability in C._ALL}
    assert set(plan.strategy) == registered, (
        "these have no strategy: "
        f"{sorted(registered - set(plan.strategy))[:5]}")
    for capability_id, strategy in plan.strategy.items():
        assert strategy in A.STRATEGIES, f"{capability_id} -> {strategy}"


def test_no_capability_appears_in_two_groups():
    plan = A.plan_audit()

    seen: dict[str, str] = {}
    for group in plan.groups:
        for capability_id in group.capabilities:
            assert capability_id not in seen, (
                f"{capability_id} is in both {seen[capability_id]} and "
                f"{group.name}")
            seen[capability_id] = group.name


def test_the_group_count_comes_from_the_registry_not_the_sentence():
    """
    23 groups from 125 capabilities, whatever the request said. The old
    planner's task count was a function of how many words the boss used.
    """
    plan = A.plan_audit()

    assert 5 < len(plan.groups) < 40, len(plan.groups)
    assert len(plan.groups) < plan.registered


def test_a_power_action_is_never_run_to_raise_the_pass_count():
    """
    Shutting the machine down would improve a percentage. The strategy for a
    CONFIRM capability is to test the policy, not the action.
    """
    plan = A.plan_audit()

    for capability_id in ("power_shutdown", "power_restart", "power_sleep",
                          "power_hibernate", "process_terminate"):
        assert plan.strategy[capability_id] == A.CONFIRM_REQUIRED, (
            f"{capability_id} -> {plan.strategy[capability_id]}")


def test_a_pure_transform_is_not_called_a_real_test():
    plan = A.plan_audit()
    assert plan.strategy["format_json"] == A.PURE
    assert plan.strategy["word_count"] == A.PURE


def test_something_needing_a_live_session_says_so():
    plan = A.plan_audit()
    assert plan.strategy["ada_ask"] == A.SESSION_REQUIRED


def test_a_camera_capability_needs_hardware():
    """
    Claiming a camera verified without a frame is the kind of pass that makes
    a whole audit worthless.
    """
    plan = A.plan_audit()
    for capability_id in ("vision_inspect_camera", "vision_camera_frame"):
        assert plan.strategy[capability_id] == A.HARDWARE_REQUIRED


def test_reading_the_machine_is_safe_to_do_for_real():
    plan = A.plan_audit()
    for capability_id in ("system_resource_usage", "windows_list",
                          "files_list"):
        assert plan.strategy[capability_id] in (A.READ_ONLY, A.SAFE_REAL_TEST)


def test_most_of_the_registry_can_actually_be_exercised():
    """
    An audit where everything is NOT_CONFIGURED proves nothing. This is the
    §24 observer check: the plan has to have real work in it.
    """
    plan = A.plan_audit()
    counts = plan.counts()
    testable = (counts.get(A.SAFE_REAL_TEST, 0) + counts.get(A.READ_ONLY, 0))

    assert testable > 80, f"only {testable} of {plan.registered} are testable"


def test_the_audit_becomes_one_goal_per_group():
    """
    Not one per capability. A hundred and twenty five top-level tasks is the
    unreadable list this replaced, however good the capability choices are.
    """
    plan = A.plan_audit()
    goals = A.as_goals(plan)

    assert len(goals) == len(plan.groups)
    assert len(goals) < plan.registered / 3
    for goal in goals:
        assert goal.capability, goal.intent
        assert goal.why, "a group did not say what is in it"


def test_each_group_says_how_much_of_it_is_testable():
    plan = A.plan_audit()
    for group in plan.groups:
        assert group.testable <= len(group.capabilities)
        assert sum(group.strategy_counts.values()) == len(group.capabilities)


def test_every_registered_capability_becomes_exactly_one_leaf(tmp_path):
    """
    The whole point of deriving the audit from the registry: nothing falls
    out of it. A capability that cannot be exercised is still accounted for.
    """
    from friday import audit_fixtures as F
    plan = A.plan_audit()
    goals = A.as_goals(plan, workspace=F.Workspace(tmp_path / 'fx'))
    leaves = [child for goal in goals for child in goal.children]
    assert len(leaves) == plan.registered
    assert len({child['capability'] for child in leaves}) == plan.registered


def test_a_leaf_either_runs_or_says_why_not(tmp_path):
    from friday import audit_fixtures as F
    plan = A.plan_audit()
    goals = A.as_goals(plan, workspace=F.Workspace(tmp_path / 'fx'))
    for goal in goals:
        for child in goal.children:
            runs = 'arguments' in child
            refused = bool(child.get('skipped_because'))
            assert runs != refused, child


def test_nothing_that_changes_the_world_runs_without_an_owned_fixture(tmp_path):
    """
    Measured, and the reason this rule exists. Before it, "takes no required
    arguments" was read as "safe to call", and the audit would have run:

        browser_close       on the boss's real browser
        music_pause         on whatever was playing
        objective_cancel    which, with no run id, means the most recent open
                            run - the audit itself

    Taking no arguments is not proof of ownership. It is the absence of proof.
    """
    from friday import audit_fixtures as F
    workspace = F.Workspace(tmp_path / 'fx')
    plan = A.plan_audit()
    goals = A.as_goals(plan, workspace=workspace)
    for goal in goals:
        for child in goal.children:
            if 'arguments' not in child:
                continue
            if plan.strategy.get(child['capability']) != A.SAFE_REAL_TEST:
                continue
            assert child['arguments'], f"{child['capability']} mutates and was called with nothing"
            named = ' '.join((str(v) for v in child['arguments'].values()))
            assert str(workspace.root) in named, f"{child['capability']} mutates something the audit does not own: {child['arguments']}"


def test_the_audit_never_cancels_itself(tmp_path):
    """The specific self-destruct the ownership rule prevents."""
    from friday import audit_fixtures as F
    goals = A.as_goals(A.plan_audit(), workspace=F.Workspace(tmp_path / 'fx'))
    dispatched = {child['capability'] for goal in goals for child in goal.children if 'arguments' in child}
    for suicide in ('objective_cancel', 'objective_pause', 'power_shutdown', 'power_restart', 'browser_close'):
        assert suicide not in dispatched, suicide


def test_fixtures_live_inside_the_file_jail(tmp_path):
    """
    The first real audit put them in %TEMP% and every file capability refused
    the path - correctly - so six capabilities reported a refusal that was
    the audit's own fault.
    """
    from friday import audit_fixtures as F
    from friday.fsjail import FileJail
    workspace = F.Workspace()
    jail = FileJail()
    jail.resolve(workspace.file('probe.txt'))


def test_a_group_of_only_refusals_still_accounts_for_its_capabilities(tmp_path):
    from friday import audit_fixtures as F
    plan = A.plan_audit()
    goals = A.as_goals(plan, workspace=F.Workspace(tmp_path / 'fx'))
    by_name = {goal.entity: goal for goal in goals}
    for group in plan.groups:
        goal = by_name[group.name]
        assert len(goal.children) == len(group.capabilities)


def test_the_audit_still_exercises_a_useful_share_of_the_registry(tmp_path):
    """
    §24 again, one level down. Refusing everything is safe and useless; this
    number is what makes the ownership rule a constraint rather than an exit.
    """
    from friday import audit_fixtures as F
    goals = A.as_goals(A.plan_audit(), workspace=F.Workspace(tmp_path / 'fx'))
    runs = sum((1 for goal in goals for child in goal.children if 'arguments' in child))
    assert runs > 40, f"only {runs} capabilities would actually be called"


def test_the_audit_plan_passes_the_gate_that_starts_it():
    """
    Groups are not steps, and judged as steps they fail every rule - not a
    capability, wrong target, no arguments. Measured: 46 complaints, and
    `objective_start` refuses a plan with any. A planner that cannot get its
    own flagship plan past validation has not shipped.
    """
    from friday import planner as P
    from friday import planner_model as PM
    plan = PM.plan_objective('audit every capability you have')
    assert P.validate(plan) == []
    specs = P.task_specs(plan)
    assert len(specs) == len(plan.goals)
    assert sum((len(spec.get('children') or []) for spec in specs)) == len(C._ALL)


def test_a_group_with_no_children_is_refused():
    """The marker is not a capability. Nothing may use it as one."""
    from friday import objectives as O
    from friday import planner as P
    plan = P.Plan(goals=[P.Goal('g1', 'do a thing', 'READ', 'SYSTEM', capability=O.COMPOSITE)])
    assert any(('no children' in complaint for complaint in P.validate(plan)))


def test_the_audit_compiles_through_the_real_entry_point(tmp_path, monkeypatch):
    """
    `objective_start`, not a hand-built plan. Everything upstream can be right
    and the thing still not reach the graph - `validate` judged groups as
    steps and refused all 23, and only calling the tool the boss's voice
    reaches would have shown it.

    Compile only. Executing it really runs 46 capabilities and talks to the
    network, which is a thing to do deliberately and not in a unit suite.
    """
    from friday import contracts as c
    from friday import objectives as O
    from friday.store import Store
    from friday.toolsets import objectives as OT
    store = Store(tmp_path / 'live.sqlite3')
    monkeypatch.setattr(OT, 'store', lambda: store)
    run = c.Run.create('audit probe', capability='objective_start')
    result = OT.objective_start(run, objective='audit every capability you have')
    assert result.status == c.SUCCEEDED, result.error
    run_id = result.output['run_id']
    rows = store.objective_tasks(run_id)
    groups = [row for row in rows if not row.get('parent_id')]
    leaves = [row for row in rows if row.get('parent_id')]
    assert len(groups) == result.output['task_count'] == len(A.plan_audit().groups)
    assert len(leaves) == len(C._ALL)
    assert all((row['capability'] == O.COMPOSITE for row in groups))
