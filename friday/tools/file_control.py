"""
MCP adapter for the Phase 1C files toolset.

Same thin shape as system_control and web_control. All path validation happens
in friday/fsjail.py before anything touches disk; this layer never sees a raw
path it is trusted to sanitise itself.
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.store import DEFAULT_DB, Store
from friday.toolsets import files as F

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


def _execute(request: str, fn, *args, **kwargs) -> dict:
    run = c.Run.create(request, capability="files")
    result = fn(run, *args, engine=_get_engine(), **kwargs)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    try:
        _get_store().save_run(run)
    except Exception:
        pass
    return result.to_dict()


def register(mcp):

    @mcp.tool()
    def files_read(path: str, max_chars: int = 40000) -> dict:
        """
        Read a text file from the user's computer and return its contents.

        Only paths inside the configured workspace roots are permitted;
        anything else returns 'failed'. Say so rather than guessing at the
        contents.
        """
        return _execute(f"read {path}", F.files_read, path, max_chars=max_chars)

    @mcp.tool()
    def files_roots() -> dict:
        """
        Which directories you can read, and what stays protected inside them.

        Call this when asked "can you access my code" or when a path is
        refused, so you can say exactly where you can look instead of just
        failing.
        """
        return _execute("what can I access", F.files_roots)

    @mcp.tool()
    def files_list(path: str = ".") -> dict:
        """List a directory inside the workspace. '.' means the first root."""
        return _execute(f"list {path}", F.files_list, path)

    @mcp.tool()
    def files_info(path: str) -> dict:
        """Size, type and modification time of a file or directory."""
        return _execute(f"info {path}", F.files_info, path)

    @mcp.tool()
    def files_search(pattern: str = "*", root: str | None = None,
                     contains: str | None = None) -> dict:
        """
        Find files by glob pattern, optionally filtering to those containing
        a piece of text.
        """
        return _execute(f"search {pattern}", F.files_search, pattern,
                        root=root, contains=contains)

    @mcp.tool()
    def files_create(path: str, content: str = "") -> dict:
        """
        Create a NEW file. Fails if it already exists. Requires approval and
        may return APPROVAL_REQUIRED.
        """
        return _execute(f"create {path}", F.files_create, path, content)

    @mcp.tool()
    def files_write(path: str, content: str) -> dict:
        """
        Write a file, replacing it if present. Requires approval and may
        return APPROVAL_REQUIRED.
        """
        return _execute(f"write {path}", F.files_write, path, content)

    @mcp.tool()
    def files_edit(path: str, old: str, new: str) -> dict:
        """
        Replace one exact, unique snippet of text in a file. Fails if the text
        is absent or appears more than once - pass a longer snippet then.
        Requires approval.
        """
        return _execute(f"edit {path}", F.files_edit, path, old, new)

    @mcp.tool()
    def files_copy(source: str, destination: str) -> dict:
        """Copy a file within the workspace. Requires approval."""
        return _execute(f"copy {source}", F.files_copy, source, destination)

    @mcp.tool()
    def files_move(source: str, destination: str) -> dict:
        """Move a file within the workspace. Requires approval."""
        return _execute(f"move {source}", F.files_move, source, destination)

    @mcp.tool()
    def files_recycle(path: str) -> dict:
        """
        Send a file to the Recycle Bin. It can be restored from there.

        This is what "delete that file" means on Windows - the boss expects to
        be able to change their mind. It does not destroy the file, and you
        should not say that it did: say it has gone to the Recycle Bin.

        Directories are refused. So is anything outside the workspace.
        """
        return _execute(f"recycle {path}", F.files_recycle, path)
