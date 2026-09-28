"""The control room's read-only view of the skill layer."""
from __future__ import annotations

import friday.ui_server as u


def test_api_skills_is_read_only_and_shaped():
    app = u.create_app()
    assert "/api/skills" in {route.path for route in app.routes}
    state = u._skills_state()
    assert set(state) >= {"skills", "counts", "tiers", "unregistered", "conflicts"}
    assert isinstance(state["skills"], list) and "BUILT_IN" in state["tiers"]
    shipped = "friday-operate"
    assert shipped in state["unregistered"] or any(s["name"] == shipped for s in state["skills"])
