"""
Web tools - news briefings and the World Monitor dashboards.

Transport only. This module used to carry a second, parallel news
implementation: its own feed lists, its own fetch-and-parse, its own
formatting, all async, all returning strings, and none of it reachable from a
durable objective. `friday/toolsets/web.py` already did the same work with a
run and a verification behind it, so the feeds were merged into that module
and this one calls it.

The World Monitor tools have the same history. `open_world_monitor` was fixed
in CORE-02 to build a real view and report `partial` until a browser can be
observed; `open_finance_world_monitor` was missed and still did the original
thing - `webbrowser.open` followed by "Displaying the Finance World Monitor on
your primary screen now, sir", which is a statement about the boss's screen
made by something that never looked at it. Both go through the view builder
now.
"""

from friday import contracts as c
from friday.toolsets import web as W


def _briefing(heading: str, result: c.ActionResult, empty: str) -> str:
    """The articles an ActionResult carries, in the briefing shape."""
    if result.status != c.SUCCEEDED:
        return f"{empty} ({result.error})" if result.error else empty
    lines = [f"### {heading}\n"]
    for entry in (result.output or {}).get("articles", []):
        lines.append(f"**[{entry['source']}]** {entry['title']}")
        lines.append(f"{entry['summary']}")
        lines.append(f"Link: {entry['link']}\n")
    return "\n".join(lines)


def _monitor(request: str, **kwargs) -> dict:
    run = c.Run.create(request, capability="web")
    result = W.world_monitor_open(run, **kwargs)
    output = result.output or {}
    return {
        "outcome": result.status,
        "url": output.get("url", ""),
        "view": output.get("view", ""),
        "time_range": output.get("time_range", ""),
        "layers": output.get("layers", []),
        "browser_state_observed": bool(output.get("browser_state_observed")),
        "detail": result.error or "",
    }


def register(mcp):

    @mcp.tool()
    async def get_world_news() -> str:
        """
        The latest global headlines from several major outlets at once.

        Use this for "what's going on in the world?" or for recent events.
        """
        run = c.Run.create("what is going on in the world", capability="web")
        result = await W.get_world_news(run)
        return _briefing(
            "GLOBAL NEWS BRIEFING (LIVE)", result,
            "The global news grid is unresponsive, sir. I'm unable to pull headlines.")

    @mcp.tool()
    async def get_world_finance_news() -> str:
        """
        The latest finance and market headlines from several outlets at once.

        Use this for finance news, market updates or economic developments.
        """
        run = c.Run.create("what is the market doing", capability="web")
        result = await W.get_world_finance_news(run)
        return _briefing(
            "FINANCE BRIEFING (LIVE)", result,
            "The financial feeds are unresponsive right now, sir. I can't pull "
            "market headlines.")

    @mcp.tool()
    def open_world_monitor(focus: str = '', time_range: str = '') -> dict:
        """
        Open the global intelligence dashboard, at the view the boss asked for.

        `focus` narrows the layers: weather, conflicts, economic, nuclear.
        `time_range` is 24h, 7d, 30d or 90d. Both empty gives the whole world
        over the last week with all twelve layers, which is the default view.

        The outcome is `partial` by design until the Browser Companion can
        watch the browser: the address is verified, where the browser actually
        landed is not. Say it is coming up on screen - do not say you watched
        it load.
        """
        return _monitor("open the world monitor", focus=focus,
                        time_range=time_range)

    @mcp.tool()
    def open_finance_world_monitor(time_range: str = '') -> dict:
        """
        Open the World Monitor showing the economic layers.

        Finance is not a separate dashboard - it is this one focused on
        sanctions, waterways, outages and economics. Same honesty as
        `open_world_monitor`: the address is verified, the browser is not
        watched, so the outcome is `partial`. Do not say you saw it load.
        """
        return _monitor("open the finance world monitor", focus="economic",
                        time_range=time_range)
