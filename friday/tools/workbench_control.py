"""
MCP adapter for the workbench.

"Create a coffee shop website and show me" is: write the files, preview it
(which validates and serves), then open the returned url in his real browser
with open_in_browser. Three tools, and the last one already exists.
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.store import DEFAULT_DB, Store
from friday.toolsets import workbench as W

_store: Store | None = None
_engine: PolicyEngine | None = None


def _get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
    return _store


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


async def _execute(request: str, fn, *args, **kwargs) -> dict:
    run = c.Run.create(request, capability="workbench")
    result = await fn(run, *args, engine=_get_engine(), **kwargs)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    try:
        _get_store().save_run(run)
    except Exception:
        pass
    return result.to_dict()


def register(mcp):

    @mcp.tool()
    async def workbench_write(project: str, path: str, content: str) -> dict:
        """
        Write a file into a workbench project - a real folder the user keeps.

        Use this to build something you are going to show them: a page, a
        stylesheet, a script. `project` is a short lowercase name like
        "coffee-shop"; `path` is relative, like "index.html" or "css/main.css".

        Start with index.html - that is what gets opened.
        """
        return await _execute(f"write {project}/{path}", W.workbench_write,
                              project, path, content)

    @mcp.tool()
    async def workbench_preview(project: str) -> dict:
        """
        Check the project actually renders, serve it locally, and return a url.

        Fails if there is no index.html, if it is empty, or if it references a
        stylesheet or script that does not exist - so a blank page is never
        presented as a finished one.

        Then pass the returned `url` to open_in_browser so they see it in their
        own browser. Only say it is ready after this succeeds.
        """
        return await _execute(f"preview {project}", W.workbench_preview, project)

    @mcp.tool()
    async def workbench_list(project: str = "") -> dict:
        """
        List workbench projects, or the files in one and whether it is ready
        to show. Use before editing so you change the file that exists.
        """
        return await _execute(f"list workbench {project}", W.workbench_list,
                              project)

    @mcp.tool()
    async def workbench_stop(project: str) -> dict:
        """Stop serving a project's preview. The files stay on disk."""
        return await _execute(f"stop preview {project}", W.workbench_stop,
                              project)
