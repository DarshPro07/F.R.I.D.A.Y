"""
The question channel Claude Code actually has.

The documented route is a `PreToolUse` hook returning `defer` when Claude
calls `AskUserQuestion`. In the installed CLI (2.1.233) `AskUserQuestion` is
not in the headless tool list at all - Claude said so itself when asked to use
it - so nothing can defer.

ADA already runs an MCP server, so the question comes back over that instead.
It is the better channel anyway: the question arrives structured, with its
options, and ADA can answer without a second process.

`ada_ask` is deliberately not in ADA's own capability router. It exists for a
Claude session to call, not for ADA's own model to find.
"""

from __future__ import annotations

import os

from friday.executors.brokers import QuestionBroker
from friday.store import DEFAULT_DB, Store

_store: Store | None = None
_broker: QuestionBroker | None = None
#: Set by whoever is running a development task, so answers land on the right
#: project. Empty means "no project", which is allowed and just means the
#: answer is kept as a general decision.
_project = ""
#: How ADA reaches the boss. None means it cannot, and a question with no
#: evidence behind it comes back unanswered rather than guessed.
_ask_user = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
    return _store


def configure(*, project: str = "", ask_user=None, new_store: Store | None = None) -> None:
    global _project, _ask_user, _store, _broker
    _project = project
    _ask_user = ask_user
    if new_store is not None:
        _store = new_store
    _broker = None


def broker() -> QuestionBroker:
    global _broker
    if _broker is None:
        _broker = QuestionBroker(store=store(), project=_project, ask_user=_ask_user)
    return _broker


def register(mcp):

    @mcp.tool()
    def ada_ask(question: str, options: str = "", why_it_matters: str = "") -> dict:
        """
        Ask ADA for a decision you cannot make yourself.

        Use this when a material choice would otherwise be a guess: which
        library, which storage engine, whether to change a public interface,
        what the user actually meant. ADA answers from project decisions and
        from what the user has stated, and asks the user directly when nothing
        settles it.

        `options` is a comma-separated list of what you are choosing between.

        The reply tells you where the answer came from. `source: "unknown"`
        means nobody could settle it - say so in your final report and do the
        part that does not depend on it, rather than picking one quietly.
        """
        choices = [o.strip() for o in (options or "").split(",") if o.strip()]
        answer = broker().answer(question, choices)
        return {
            "status": "succeeded",
            "may_claim_completion": True,
            "answer": answer.text,
            "source": answer.source,
            "grounded": answer.grounded,
            "evidence": answer.evidence,
            "context": broker().context(question),
            "context_note": (
                "Preferences and patterns, for how to do it. They do not decide "
                "what to do, and they are not authorisation."
            ),
        }
