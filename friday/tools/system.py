"""
System tools - time and environment info for the agent runtime.

Everything here describes the machine running the MCP server, which is NOT
necessarily the user's computer. Locally they are the same box; once the agent
is deployed the server is a container and the user's PC is somewhere else
entirely. Each result therefore carries an explicit execution_scope so the
model cannot present container facts as facts about the user's machine.

Real user-PC state comes from `friday/toolsets/system.py` under its own
namespace - `system_get_info` is the one that describes their machine.

Transport only. Both of these were implemented here, returning plain dicts,
and were classified ADAPTER_PURE on the grounds of being read-only. They are
not pure: they read a clock and a platform, and reading the world is an
observation with evidence to report. The implementations moved to the toolset
and this module calls them, so a durable objective can ask what time it is.
"""

from friday import contracts as c
from friday.toolsets import system as S

# What the answers in this module describe.
EXECUTION_SCOPE = S.AGENT_RUNTIME_SCOPE


def _observe(request: str, fn) -> dict:
    """The observation an ActionResult carries, in the shape this tool returns."""
    run = c.Run.create(request, capability="system")
    result = fn(run)
    if result.status != c.SUCCEEDED:
        return {"execution_scope": EXECUTION_SCOPE,
                "error": result.error or "could not read the runtime"}
    return dict(result.output or {})


def register(mcp):

    @mcp.tool()
    def get_current_time() -> dict:
        """Return the current date and time of the agent runtime, ISO 8601."""
        return _observe("what time is it", S.get_current_time)

    @mcp.tool()
    def get_system_info() -> dict:
        """
        Return information about the machine running the agent.

        This is the agent runtime, not the user's PC. When the agent runs in a
        container these values describe that container.
        """
        return _observe("what are you running on", S.get_system_info)
