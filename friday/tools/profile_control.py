"""
MCP adapter for the user model.

Thin on purpose. Everything this used to do - the policy gate, the shaping of
outcomes, the decision about what counts as success - moved to
`friday/toolsets/profile.py`, because a request that arrives over MCP and a
request that arrives from a durable objective should not be able to get
different answers. This layer is now transport: it makes a run, calls the
capability, and flattens the ActionResult back into the response shape that
already existed.

The flattening is deliberate rather than lazy. `friday/autolearn.py` reads
`learned`, `reinforced` and `conflicts` straight off the top level of this
response, and the briefing loop is a live consumer; changing the wire format
would have been a second change wearing the first one's clothes.

`profile_learn_from_turn` is the one the agent should call after a substantive
turn. Everything else answers "what do you know about me?" and "why do you
think that?".
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import profile as PT
from friday.toolsets.profile import reset_store, store   # re-exported

_engine: PolicyEngine | None = None

__all__ = ["register", "store", "reset_store"]


def _get_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
        for tool_id in (t.strip() for t in
                        os.getenv("ADA_PREAPPROVED_TOOLS", "").split(",") if t.strip()):
            try:
                _engine.approve_for_session(tool_id)
            except PolicyError:
                continue
    return _engine


def _flatten(result: c.ActionResult) -> dict:
    """The ActionResult in the shape callers of this tool already expect."""
    body: dict = {
        "status": result.status,
        "may_claim_completion": result.status == c.SUCCEEDED,
    }
    if result.error:
        body["error"] = result.error
    for key, value in (result.output or {}).items():
        if key != "execution_scope":
            body[key] = value
    return body


def _execute(request: str, fn, *args, **kwargs) -> dict:
    run = c.Run.create(request, capability="profile")
    return _flatten(fn(run, *args, engine=_get_engine(), **kwargs))


def register(mcp):

    @mcp.tool()
    def profile_learn_from_turn(user_said: str, you_replied: str = "") -> dict:
        """
        Learn durable facts about the user from a turn of conversation.

        Call this after the user says something revealing about themselves:
        what they have, want, are working toward, how they like things done,
        or how they think. It is safe to call on ordinary turns - most contain
        nothing and it will say so.

        Returns what was stored, reinforced, rejected, or raised as a
        CONFLICT. A conflict means the user has now said something that
        contradicts something they said before: tell them both values and ask
        which is right. Do not pick one yourself.
        """
        return _execute("learn from this turn", PT.profile_learn_from_turn,
                        user_said, you_replied)

    @mcp.tool()
    def profile_get() -> dict:
        """
        Everything known about the user, grouped: identity, thinking,
        possessions, wants, goals, preferences. Use for "what do you know
        about me?".
        """
        return _execute("what do you know about me", PT.profile_get)

    @mcp.tool()
    def profile_explain(subject: str) -> dict:
        """
        Why does ADA believe something? Returns the belief, the words it was
        learned from, anything it replaced, and any disagreements.
        """
        return _execute(f"why do you think {subject}", PT.profile_explain,
                        subject)

    @mcp.tool()
    def profile_open_conflicts() -> dict:
        """
        Contradictions waiting on the user. Each is something they said twice,
        differently. Ask which is right; do not choose.
        """
        return _execute("what conflicts are open", PT.profile_open_conflicts)

    @mcp.tool()
    def profile_resolve_conflict(conflict_id: int, keep: str,
                                 rationale: str = "user decided") -> dict:
        """
        Settle a contradiction after the user has answered. `keep` is "new"
        or "existing". Only call this once they have actually told you which.
        """
        return _execute(f"resolve conflict {conflict_id}",
                        PT.profile_resolve_conflict, conflict_id, keep,
                        rationale)
