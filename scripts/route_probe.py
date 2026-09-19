"""Score the room agent's capability search against the LIVE MCP tool list,
exactly as agent_friday.search_capabilities does (Router.load + Router.search).

Usage: .venv/Scripts/python.exe scripts/route_probe.py "<sentence>" ["<sentence>" ...]
       .venv/Scripts/python.exe scripts/route_probe.py --e2e     # the master-prompt sentences
"""
from __future__ import annotations
import asyncio, json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from mcp import ClientSession
from mcp.client.sse import sse_client
from friday import capability_router as R


class _Info:
    def __init__(self, t):
        self.name = t.name
        self.description = t.description or ""
        # livekit's MCPToolset exposes the server schema as info.raw_schema
        self.raw_schema = {"name": t.name, "description": t.description or "",
                           "parameters": t.inputSchema or {}}


class _Tool:
    def __init__(self, t):
        self.info = _Info(t)


E2E = [
    "Take a live snapshot of yourself. Tell me whether the camera and screen capture are available on this machine right now, how many capability families are live, and which providers are unprobed.",
    "what can you actually do right now? be specific about the camera and the screen",
    "switch your camera off until I say otherwise",
    "can you look through the camera right now?",
    "show me your recent file actions",
    "undo that",
    "wait for a file called jarvis-drop.txt to appear on my desktop then read it back to me",
    "list your skills and their ladder states",
    "declare a permissions manifest for a test skill called e2e-probe that needs files.read only",
    "give me behaviour grading scenarios for the skill e2e-probe",
    "permanently delete jarvis-drop.txt",
    "did you open notepad at any point during this run?",
    "create jarvis-test.txt on my desktop containing exactly the words version one",
    "start an objective that waits for jarvis-drop.txt on my desktop then reads it",
    "what is the status of that objective?",
]


async def main(argv: list[str]) -> int:
    qs = E2E if argv == ["--e2e"] else argv
    async with sse_client("http://127.0.0.1:8000/sse") as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
    router = R.Router()
    router.load([_Tool(t) for t in tools])
    print(f"live tools: {len(router.all_tools)}")
    for q in qs:
        hits = router.search(q, limit=4)
        print(f"{q[:70]:70s} -> {[h.get('capability') or h.get('name') for h in hits]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
