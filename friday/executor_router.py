"""
Which coding agent gets the work, decided from evidence where there is any.

Friday's routing has been assumption all the way down - "Opus is probably
best for this" - and an assumption that nobody measures never gets corrected,
because there is nothing for it to be corrected against. `evaluation.py`
records what actually happened; this is the thing that reads those records
and acts on them.

Three rules, in order:

    1. An executor that is not installed is not a choice.
    2. Where there is enough evidence, the evidence decides.
    3. Where there is not, the default decides, and says so.

Rule 3 is the one worth defending. `Record.best_for()` returns None until a
minimum number of attempts exist, and this passes that through rather than
breaking the tie itself. A router that swings on one lucky run is worse than
a fixed default because it looks informed, and the honest output of "I have
seen this twice" is "I do not know yet".

Discovery is a PATH check, not a config file. A config file saying opencode
is available on a machine where it is not is a config file that produces a
confusing failure ten minutes into a run; `shutil.which` cannot be wrong
about it.

Only `claude` has a builder here. `opencode` and `codex` are declared so
discovery is real and so adding one later is data rather than a redesign,
but writing an executor for a binary that is not on this machine would be
untestable code for a thing nobody can run - it would be speculation wearing
the shape of support.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field

logger = logging.getLogger("friday-agent")

#: Chosen when nothing else can be. The one that is installed and proven.
DEFAULT = "claude"


@dataclass(frozen=True)
class Executor:
    """One coding agent Friday could hand work to."""

    id: str
    #: What to look for on PATH. Discovery is this and nothing else.
    binary: str
    #: Human words, for the reason a choice is explained with.
    title: str = ""
    #: Whether Friday can actually construct and drive it. An executor can be
    #: installed and still have no builder here, and saying so is better than
    #: choosing it and failing at launch.
    buildable: bool = False
    notes: str = ""
    #: How to find it, when PATH is not the whole answer. A dotted
    #: `module:function` returning a path or None.
    #:
    #: `claude` needs this. It installs to `~/.local/bin`, which is not on
    #: the PATH this process inherits, so `shutil.which` returns None for a
    #: binary that is sitting right there - measured on this machine.
    #: `cli.claude_path()` already knew that and already had the fallback
    #: list; the router deferring to it is cheaper and more correct than a
    #: second search that would drift from the first.
    locator: str = ""

    def path(self) -> str | None:
        if self.locator:
            module_name, _, function_name = self.locator.partition(":")
            try:
                import importlib

                found = getattr(importlib.import_module(module_name),
                                function_name)()
                return found or None
            except Exception:                               # noqa: BLE001
                logger.exception("could not locate %s", self.id)
                return None
        return shutil.which(self.binary)

    def installed(self) -> bool:
        return self.path() is not None

    def usable(self) -> bool:
        return self.buildable and self.installed()


#: Everything Friday knows about. Being here is not a promise it works.
KNOWN: tuple[Executor, ...] = (
    Executor(id="claude", binary="claude", title="Claude Code",
             buildable=True,
             locator="friday.executors.cli:claude_path",
             notes="the executor this codebase was built around"),
    Executor(id="opencode", binary="opencode", title="OpenCode",
             buildable=False,
             notes="declared for discovery; no builder written, because it "
                   "is not installed here and untestable code is worse than "
                   "absent code"),
    Executor(id="codex", binary="codex", title="Codex CLI",
             buildable=False,
             notes="declared for discovery; no builder written, same reason"),
    Executor(id="cline", binary="cline", title="Cline CLI",
             buildable=False,
             notes="declared for discovery; no builder written, same reason - "
                   "@cline/cli 3.0.60, Apache-2.0, pinned 1d5d3b005575. It is "
                   "a genuine headless coding CLI with its own worktrees and "
                   "checkpoints, so it is a real candidate; it is simply not "
                   "installed on this machine, and a builder nobody can run "
                   "is worse than an honest gap"),
)

BY_ID = {executor.id: executor for executor in KNOWN}


@dataclass
class Choice:
    """Who was chosen, and the honest reason."""

    executor: str
    because: str
    #: What else could have been picked. Empty when there was no contest.
    alternatives: tuple[str, ...] = ()
    #: True when a measurement decided rather than a default.
    from_evidence: bool = False
    considered: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"executor": self.executor, "because": self.because,
                "alternatives": list(self.alternatives),
                "from_evidence": self.from_evidence,
                "considered": self.considered}


def installed() -> tuple[str, ...]:
    """Every known executor whose binary is on PATH."""
    return tuple(e.id for e in KNOWN if e.installed())


def usable() -> tuple[str, ...]:
    """Every executor Friday could actually run right now."""
    return tuple(e.id for e in KNOWN if e.usable())


def discover() -> dict:
    """
    What is here, for a person to read.

    Reports installed-but-unbuildable separately from absent, because they
    are different problems: one needs code, the other needs an install.
    """
    return {
        "usable": list(usable()),
        "installed_without_a_builder": [
            e.id for e in KNOWN if e.installed() and not e.buildable],
        "not_installed": [e.id for e in KNOWN if not e.installed()],
    }


def choose(task: str = "", *, record=None, minimum: int = 3,
           prefer: str = "") -> Choice:
    """
    Pick an executor for this task.

    `record` is a `friday.evaluation.Record`. Without one, or without enough
    attempts in it, the default is chosen and `from_evidence` is False - so a
    caller can tell a measured decision from a fallback, which matters when
    someone later asks why a particular agent keeps getting the work.
    """
    choices = usable()
    if not choices:
        return Choice(executor="", because=(
            "no coding executor is both installed and supported here; "
            f"install one of {', '.join(e.binary for e in KNOWN)}"))

    if prefer:
        if prefer in choices:
            return Choice(executor=prefer, because="asked for by name",
                          alternatives=tuple(c for c in choices if c != prefer))
        known = BY_ID.get(prefer)
        reason = (f"{prefer} is not usable here"
                  + (f": {known.notes}" if known else " and is not known"))
        logger.info("executor.preference_refused %s", reason)
        return Choice(executor="", because=reason)

    if len(choices) == 1:
        only = choices[0]
        return Choice(executor=only, because=(
            f"{BY_ID[only].title} is the only executor available here"))

    scores = {}
    if record is not None:
        for candidate in choices:
            scores[candidate] = record.scored(agent=candidate)
        winner = record.best_for(task, minimum=minimum)
        if winner in choices:
            rate = scores.get(winner, {}).get("pass_rate")
            return Choice(
                executor=winner, from_evidence=True, considered=scores,
                alternatives=tuple(c for c in choices if c != winner),
                because=(f"measured best on {task!r}"
                         + (f" ({rate:.0%} of attempts passed)"
                            if rate is not None else "")))

    fallback = DEFAULT if DEFAULT in choices else choices[0]
    return Choice(
        executor=fallback, considered=scores,
        alternatives=tuple(c for c in choices if c != fallback),
        because=(f"not enough evidence yet on {task!r} - fewer than {minimum} "
                 f"decided attempts - so the default was used"))


def build(executor_id: str, store, **kwargs):
    """
    Construct the chosen executor.

    Raises rather than substituting. A caller that asked for one agent and
    silently got another cannot interpret its own results, and the whole
    point of the record is that the results mean something.
    """
    known = BY_ID.get(executor_id)
    if known is None:
        raise LookupError(f"no executor called {executor_id!r}")
    if not known.buildable:
        raise NotImplementedError(
            f"{executor_id} has no builder here: {known.notes}")
    if not known.installed():
        raise FileNotFoundError(
            f"{known.binary} is not on PATH, so {executor_id} cannot run")

    from friday.executors.claude_code import ClaudeCodeExecutor

    return ClaudeCodeExecutor(store, **kwargs)
