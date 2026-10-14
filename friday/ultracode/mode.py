"""ULTRA-007: the ultracode mode switch and the substantive-task gate.

While the mode is off no workflow objective is made. While it is on, only a
substantive task runs as a workflow: one the planner's deterministic
classification (`task_class`) routes past Friday herself to an executor
(COMPLEX, LONG_RUNNING, CRITICAL). TRIVIAL, SIMPLE and STANDARD work skips it.

The mode decides only whether a workflow objective is compiled. It never
touches policy: a destructive step inside the workflow still goes through the
policy engine and needs its confirmation nonce like any other call.

The choice outlives the process in `MODE_FILE`, like the autonomy choice in
`policy.AUTONOMY_FILE`. A missing or unreadable file reads as off.
"""
from __future__ import annotations

import json
from typing import Mapping

from friday import execution_economics as ee
from friday import task_class as TC
from friday.config import DATA_DIR
from friday.ultracode import durable
from friday.ultracode.definition import Compiled, Resolution

MODE_FILE = DATA_DIR / "ultracode_mode.json"

#: Routes Friday answers herself; a task classified onto one is not substantive.
_DIRECT_ROUTES = (ee.DETERMINISTIC, ee.FRIDAY_DIRECT)

__all__ = ["MODE_FILE", "is_on", "maybe_start", "set_on", "substantive"]


def is_on() -> bool:
    try:
        return json.loads(MODE_FILE.read_text(encoding="utf-8")).get("on") is True
    except (OSError, ValueError, AttributeError):
        return False


def set_on(on: bool) -> bool:
    MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODE_FILE.write_text(json.dumps({"on": bool(on)}), encoding="utf-8")
    return is_on()


def substantive(request: str) -> bool:
    return TC.route_for(TC.classify(request).task_class) not in _DIRECT_ROUTES


def maybe_start(store, request: str, compiled: Compiled, *,
                resolutions: Mapping[str, Resolution], provenance: str,
                attended: bool) -> dict | None:
    """The workflow objective for `request`, or None when the mode is off or
    the task is not substantive (the caller then handles it as before)."""
    if not is_on() or not substantive(request):
        return None
    return durable.start_definition_run(store, compiled, resolutions=resolutions,
                                        provenance=provenance, attended=attended)
