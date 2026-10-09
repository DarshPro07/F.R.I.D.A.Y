"""
Risk class of a physical-world device action (DEVICE-004).

FRIDAY decides the class, from the action name it recognises, through the same
`PolicyEngine` as every other tool. A device's own description of an action -
its label, its self-declared risk - is untrusted input and has no parameter
here, the same structural rule as `PolicyEngine.decide`: what cannot be passed
in cannot lower the class. An action FRIDAY has not classified needs a
confirmation, so a new or renamed action never runs unasked.
"""

from __future__ import annotations

from friday import policy


def assess(action: str, *, engine: policy.PolicyEngine | None = None) -> policy.Verdict:
    """The policy verdict for a device action such as `door.unlock`."""
    name = (action or "").strip().lower()
    tool_id = f"device.{name}"
    engine = engine or policy.default_engine
    if engine.category_of(tool_id) is None:
        return policy.Verdict(
            tool_id, "UNKNOWN", policy.CONFIRM,
            "physical action has no declared risk class; unclassified is not allowed")
    return engine.decide(tool_id)
