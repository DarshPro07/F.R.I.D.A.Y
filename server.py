import os
from mcp.server.fastmcp import FastMCP
from friday.tools import register_all_tools
from friday.prompts import register_all_prompts
from friday.resources import register_all_resources
from friday.config import config
from friday import build_identity as _build

# Core build snapshot telemetry
print(f"friday.build {_build.describe()}", flush=True)

# Host configuration fallback tree
srv_host = os.getenv("ADA_MCP_HOST", "127.0.0.1")
srv_port = int(os.getenv("ADA_MCP_PORT", 8000))

# Initialize runtime container
mcp = FastMCP(
    name=config.SERVER_NAME,
    instructions=(
        "You are Friday, a Tony Stark-style AI assistant. "
        "You have access to a set of tools to help the user. "
        "Be concise, accurate, and a little witty."
    ),
    host=srv_host,
    port=srv_port,
)

# Pipe extensions into core runner instance
for registrar in (register_all_tools, register_all_prompts, register_all_resources):
    registrar(mcp)

if __name__ == "__main__":
    # Spin up transport adapter
    mcp.run(transport="sse")
