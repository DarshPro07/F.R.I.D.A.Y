"""
The claim guard, and what it must not change about the tools it wraps.

The claim semantics themselves are gated in test_core01_gates.py. This file is
about the wrapper: `guard()` sits in front of all 125 registered tools, so a
mistake in it is a mistake in every one of them at once - which is exactly what
happened, and the only symptom was a RuntimeWarning in a log nobody was reading
during a voice session.
"""


def test_an_async_tool_stays_async_through_the_guard():
    """
    The guard wrapped every tool in a plain `def`. For a coroutine function
    that returns a coroutine, and FastMCP decides whether to await by
    inspecting the callable it was handed - so it saw a sync function, did not
    await, and the tool "succeeded" instantly having done nothing.

    Every async tool broke at once: web_search, web_fetch, web_answer,
    web_crawl, web_deep_research, automations_run and both news tools. The
    only evidence was a RuntimeWarning in the MCP server log next to a web
    search that finished in 19 milliseconds.
    """
    import asyncio
    import inspect
    from mcp.server.fastmcp import FastMCP
    from friday.tools import register_all_tools
    server = FastMCP(name='async-guard-test')
    register_all_tools(server)
    tools = asyncio.run(server.list_tools())
    assert len(tools) > 100, f"only {len(tools)} tools registered; proves nothing"
    manager = server._tool_manager
    asynchronous = []
    for tool in tools:
        registered = manager.get_tool(tool.name)
        function = getattr(registered, 'fn', None)
        if function is None:
            continue
        wrapped = getattr(function, '__wrapped__', None)
        if wrapped is not None and inspect.iscoroutinefunction(wrapped):
            asynchronous.append(tool.name)
            assert inspect.iscoroutinefunction(function), f"{tool.name} is async underneath but the guard exposes a sync callable; FastMCP will not await it and the model receives a coroutine object"
    assert asynchronous, 'no guarded async tools found; this test would pass over an empty set'


def test_a_guarded_async_tool_actually_runs():
    """The end of it: a real call returning a real result, not a coroutine."""
    import asyncio
    from mcp.server.fastmcp import FastMCP
    from friday.tools import register_all_tools
    server = FastMCP(name='async-guard-call')
    register_all_tools(server)
    result = asyncio.run(server.call_tool('web_fetch', {'url': 'not a url'}))
    body = result[1] if isinstance(result, tuple) else result
    assert 'coroutine' not in repr(body).lower(), f"a coroutine leaked to the caller: {repr(body)[:200]}"