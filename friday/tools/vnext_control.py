"""MCP tools for the vnext operating layer: permissions, first-run
contract, organization operations, skill ladder.

Thin adapters over friday/user_policy.py, friday/first_run.py,
friday/orgplane.py, friday/skill_ladder.py - the production surface that
makes Friday's model able to read policy, run the first-run contract,
manage persistent operations, and walk the skill ladder.
"""

from __future__ import annotations

import logging

from friday import orgplane as op
from friday import skill_ladder as sl
from friday.first_run import FirstRunContract
from friday.user_policy import UserPolicy

logger = logging.getLogger("friday-agent")


def _record_state_snapshot(source: str, *, count: int, health: str = "", evidence: str = "",
                           ttl_s: float = 300.0) -> None:
    """An authoritative reading of some fleet state, for the STATE-claim
    audit (`friday.action_evidence`). Never raises: a ledger hiccup must not
    cost the read that produced the snapshot."""
    try:
        import time
        from friday import action_evidence as AE
        AE.ledger().snapshot(AE.CapabilityStateSnapshot(
            source=source, collected_at=time.time(), count=count, health=health,
            evidence=evidence, ttl_s=ttl_s))
    except Exception:                                    # noqa: BLE001
        logger.exception("state snapshot %s not recorded", source)


def register(mcp):
    # -- permissions -------------------------------------------------------

    @mcp.tool()
    def policy_snapshot() -> dict:
        """Current delegated-permission states for every domain (AUTO /
        CONFIRM / DENY), plus what is constitutionally denied. Read this
        before consequential actions instead of guessing."""
        try:
            policy = UserPolicy()
            return {"status": "succeeded", "domains": policy.snapshot(),
                    "constitutional_deny": sorted(
                        __import__("friday.user_policy",
                                   fromlist=["CONSTITUTIONAL_DENY"])
                        .CONSTITUTIONAL_DENY)}
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    @mcp.tool()
    def policy_set(domain: str, state: str, reason: str) -> dict:
        """
        Record an EXPLICIT permission change the boss just stated in
        conversation ("I trust you to publish without asking" ->
        social_publish AUTO). `reason` must quote the boss's own words.
        Constitutional classes are refused and the attempt is audited.
        """
        try:
            return UserPolicy().grant(domain, state, reason=reason)
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    @mcp.tool()
    def spend_gate(platform: str, amount: float,
                    purpose: str = "") -> dict:
        """
        The pre-spend gate: AUTO only inside an authorized envelope;
        otherwise CONFIRM with the boss showing amount + platform. Call
        BEFORE any action that creates or increases monetary spend.
        """
        try:
            return UserPolicy().can_spend(platform=platform, amount=amount,
                                          purpose=purpose)
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    @mcp.tool()
    def spend_envelope_store(platform: str, total_cap: float,
                                 daily_cap: float = 0, purpose: str = "",
                                 currency: str = "INR") -> dict:
        """
        Store a spend envelope the boss just CONFIRMED (platform, caps,
        purpose). Only call after explicit confirmation of the numbers -
        this is the record of that confirmation, not a way around it.
        """
        try:
            return UserPolicy().authorize_envelope(
                platform=platform, purpose=purpose, daily_cap=daily_cap,
                total_cap=total_cap, currency=currency)
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    # -- first-run contract ------------------------------------------------

    @mcp.tool()
    def contract_pending_questions() -> dict:
        """The first-run operating-contract questions still unanswered.
        Empty list = contract complete. Ask them conversationally, a few
        at a time - never as a questionnaire prison."""
        try:
            contract = FirstRunContract()
            return {"status": "succeeded",
                    "configured": contract.exists(),
                    "pending": contract.pending_questions()}
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    @mcp.tool()
    def contract_record(answers_json: str) -> dict:
        """
        Persist first-run answers (JSON object: field -> value; fields
        from contract_pending_questions). Permission-shaped answers also
        update the live policy store so contract and runtime agree.
        """
        import json as _json
        try:
            return FirstRunContract().record(_json.loads(answers_json))
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    # -- organization control plane ----------------------------------------

    @mcp.tool()
    def operation_create(name: str, goal: str = "") -> dict:
        """
        Create a PERSISTENT operation (org-scale: multiple workers,
        budgets, routines, long-lived status). Only after the boss
        confirms the org moment ("WatchCo" confirmation). Simple tasks
        never need this - delegate directly instead.
        """
        try:
            return op.control_plane().create_operation(name, goal=goal)
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    @mcp.tool()
    def operation_status(op_id: str) -> dict:
        """Customer-shaped status of a persistent operation: objective,
        work, progress, cost, decisions. No backend jargon."""
        try:
            return op.control_plane().get_status(op_id)
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    @mcp.tool()
    def operation_assign(op_id: str, description: str,
                         assignee: str = "") -> dict:
        """Add a work item to a persistent operation (assignee free-form:
        'hermes', 'friday', a role name)."""
        try:
            return op.control_plane().assign_work(op_id, description,
                                                  assignee)
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    @mcp.tool()
    def operation_update(op_id: str, goal: str = "",
                         budget_json: str = "") -> dict:
        """Change a persistent operation's goal and/or budget (JSON
        object like {"total": 5000, "currency": "INR"}). Budget changes
        the boss confirmed only."""
        import json as _json
        try:
            plane = op.control_plane()
            out: dict = {"status": "succeeded", "op_id": op_id}
            if goal:
                out["goal"] = plane.update_goal(op_id, goal)["status"]
            if budget_json:
                out["budget"] = plane.set_budget(
                    op_id, _json.loads(budget_json))["status"]
            return out
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    # -- skill ladder ------------------------------------------------------

    @mcp.tool()
    def skill_capture(name: str, procedure: str, criteria: str,
                      evidence: str) -> dict:
        """
        Capture a skill CANDIDATE after work that met a promotion
        criterion (comma-separated from: repeated_procedure,
        expensive_rediscovery, safety_critical,
        project_operational_knowledge). Refused without criteria +
        evidence - one-off facts belong in project memory instead.
        """
        try:
            wanted = [c.strip() for c in criteria.split(",") if c.strip()]
            return sl.SkillLadder().capture(name, procedure,
                                            criteria=wanted,
                                            evidence=evidence)
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    @mcp.tool()
    def skill_list(state: str = "") -> dict:
        """Skill candidates/validated skills (optionally by state:
        CANDIDATE / VALIDATED / NEEDS_REVALIDATION / REJECTED / DEPRECATED)."""
        try:
            skills = sl.SkillLadder().listing(state)
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}
        # This read IS the authoritative state (D): a spoken "N skills are
        # VALIDATED" is licensed by it for a few minutes, never by memory.
        _record_state_snapshot("skills", count=len(skills),
                               health=",".join(sorted({str(s.get("state", "")) for s in skills}))[:80],
                               evidence=f"skill_list(state={state!r}) -> {len(skills)} rows")
        return {"status": "succeeded", "skills": skills}

    # -- skill intelligence + self-model -----------------------------------
    #
    # These seven are thin over `friday/toolsets/skills.py` so that a durable
    # objective can call them through capability_runtime with a verified
    # ActionResult beneath; they used to return plain dicts from here, which
    # made them unreachable to the objective engine (test_core01_red).

    def _execute(request: str, fn, *args, **kwargs) -> dict:
        from friday import contracts as c
        run = c.Run.create(request, capability="skills")
        result = fn(run, *args, **kwargs)
        run.transition("completed" if run.all_succeeded else "partial",
                       None if run.all_succeeded else (result.error or "not verified"))
        return result.to_dict()

    @mcp.tool()
    def skill_declare_dependencies(name: str, dependencies: str) -> dict:
        """
        Record what a VALIDATED skill's procedure rests on, so a later code
        change can say whether it invalidates THIS skill - and leave every
        unrelated skill alone. `dependencies` is comma-separated
        `kind:target` pairs; kinds are file, directory, package, schema,
        skill (e.g. `file:friday/model_gateway.py,package:livekit`).
        Digests are taken now, at validation state.
        """
        from friday.toolsets import skills as S
        return _execute(f"declare dependencies of {name}", S.skill_declare_dependencies, name, dependencies)

    @mcp.tool()
    def skill_revalidation_sweep(changed_paths: str = "") -> dict:
        """
        After a code change, mark the skills whose declared dependencies
        actually moved as NEEDS_REVALIDATION and report which ones were
        left alone. `changed_paths` is comma-separated (feed it
        `git diff --name-only`); empty means check every validated skill.
        A README typo must invalidate nothing - the `untouched` list is the
        proof.
        """
        from friday.toolsets import skills as S
        return _execute("sweep validated skills for stale dependencies", S.skill_revalidation_sweep, changed_paths)

    @mcp.tool()
    def skill_declare_permissions(name: str, manifest_yaml: str) -> dict:
        """
        Record the scope a skill REQUESTS (never grants): a YAML manifest
        with `risk: LOW|MEDIUM|HIGH` and `permissions:` {capabilities,
        prohibited, filesystem: {read, write}, network, commands}. Linted
        deterministically; a FAIL is refused with every finding and the
        skill stays read-only. Workers that follow the skill run under
        worker ∩ manifest ∩ objective ∩ policy - a manifest cannot widen
        anything, and a bare '*' is refused.
        """
        from friday.toolsets import skills as S
        return _execute(f"declare permissions of {name}", S.skill_declare_permissions, name, manifest_yaml)

    @mcp.tool()
    def skill_behavior_scenarios(skill: str, task: str, competing_pressure: str = "") -> dict:
        """
        The three prompts a skill must be judged under: supportive (follow the
        procedure exactly), neutral (just the task), competing (an explicit
        instruction to skip the checks). Run the same worker on each, collect
        the traces, then grade with skill_behavior_grade. A verdict needs all
        three; the competing one finds skills a worker abandons under pressure.
        """
        from friday.toolsets import skills as S
        return _execute(f"behaviour scenarios for {skill}", S.skill_behavior_scenarios,
                        skill, task, competing_pressure)

    @mcp.tool()
    def skill_behavior_grade(spec_json: str, trace_json: str, report_dir: str = "") -> dict:
        """
        Grade whether a worker FOLLOWED a skill, deterministically. `spec_json`
        is {skill, steps:[{id, description, detector:{tool, arguments_match,
        output_match, status, after_step, before_step}, required, forbidden}]};
        `trace_json` is {supportive: <trace>, neutral: <trace>, competing:
        <trace>} where each trace is a Claude stream-json transcript string
        or a list of {tool, arguments, status, output}. No model reads the
        trace; a step that failed never satisfies a later step's dependency;
        the verdict is INCOMPLETE unless all three scenarios were run. With
        `report_dir`, a redacted markdown+json report is written there.
        """
        from friday.toolsets import skills as S
        return _execute("grade skill behaviour", S.skill_behavior_grade, spec_json, trace_json, report_dir)

    # -- runtime self-model (ML-05) ------------------------------------------

    @mcp.tool()
    def self_model_snapshot() -> dict:
        """
        What Friday actually has right now, from runtime state: screen and
        camera modalities (snapshot only, never live), capability families
        and their state, anything switched off and why, the live tool count,
        model routes with current health evidence. This is the source of
        every capability claim in the prompts; there is no static list.
        """
        from friday.toolsets import skills as S
        # This tool runs INSIDE the server that owns the inventory, so the
        # count is a fact here - it used to be passed as None and the answer
        # said "the MCP tool inventory is not readable right now" from the
        # one process where it always is.
        try:
            tool_count = len(mcp._tool_manager.list_tools())
        except Exception:                                    # noqa: BLE001
            tool_count = None
        return _execute("self-model snapshot", S.self_model_snapshot, tool_count=tool_count)

    @mcp.tool()
    def self_model_switch(name: str, enabled: bool, reason: str = "") -> dict:
        """
        Switch a capability off or back on by name (screen, camera, browser,
        desktop, web, hermes). Off = the self-model reports it DISABLED with
        the reason and the prompts stop offering it on the next refresh; on =
        it returns without any prompt edit. Durable across restarts.
        """
        from friday.toolsets import skills as S
        return _execute(f"switch {name} {'on' if enabled else 'off'}", S.self_model_switch,
                        name, enabled, reason)
