"""
MCP adapter for browser identity.

`open_in_browser` is the one that matters: it is the difference between
"a browser opened" and "the browser he is signed into opened".
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.store import DEFAULT_DB, Store
from friday.toolsets import identity as I

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
    run = c.Run.create(request, capability="identity")
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
    def browser_profiles() -> dict:
        """
        List the browser profiles on this machine and which account each is
        signed into. Use this to answer "which accounts do you know about?"
        and before opening anything where the account matters.
        """
        return _execute("list browser profiles", I.browser_profiles)

    @mcp.tool()
    def open_in_browser(url: str, account: str = "", service: str = "") -> dict:
        """
        Open a URL in the REAL browser the user is signed into - not a blank
        automation window. Use this for ordinary browsing: YouTube, Gmail,
        a dashboard, anything where being logged in matters.

        `account` is whatever they said - "my aicodepro one", an email
        address, a profile name. Leave it empty to use the profile they were
        last in, which is usually right.

        `service` ("youtube", "gmail", ...) lets the choice be remembered.

        Status 'partial' with needs_choice means several accounts fit: read
        the candidates back and ask which one. Do not pick for them - opening
        the wrong Google account is not a small mistake.
        """
        return _execute(f"open {url[:60]}", I.open_in_browser, url,
                        account=account, service=service, store=_get_store())
