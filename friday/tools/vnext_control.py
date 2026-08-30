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
        CANDIDATE / VALIDATED / REJECTED / DEPRECATED)."""
        try:
            return {"status": "succeeded",
                    "skills": sl.SkillLadder().listing(state)}
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}
