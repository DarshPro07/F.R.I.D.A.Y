"""
Data resources - expose static content or dynamic data via MCP resources.
"""

import json

from friday import capabilities


def register(mcp):

    @mcp.resource("friday://info")
    def server_info() -> str:
        """Returns basic info about this MCP server."""
        return (
            "Friday MCP Server\n"
            "A Tony Stark-inspired AI assistant.\n"
            "Built on the official MCP SDK (mcp.server.fastmcp)."
        )

    @mcp.resource("friday://capabilities")
    def capability_manifest() -> str:
        """
        Declared metadata for every tool: execution scope, side effect, and
        whether it needs the Edge Controller to work off this machine.
        Feeds the policy engine later; useful now for auditing the
        cloud/local boundary.
        """
        return json.dumps(capabilities.as_dicts(), indent=2)
