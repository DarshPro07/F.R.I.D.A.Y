"""
Objective verdicts on work an agent says it finished.

    NO MODEL PROMOTES ITS OWN CODE BECAUSE IT SAID THE TESTS PASSED.

That sentence is the whole module. A coding agent reporting success is a
claim, and this repository already knows what to do with claims: check them.
The check has to be something the agent cannot influence - a command, run
afterwards, in the boundary, whose exit code is the verdict.

The idea is Harbor's: run the same task through different agents and models,
score every attempt with the same objective verifier, and keep the numbers so
that routing is decided by evidence instead of by "Opus is probably best for
this". The implementation is native, because the alternative is a second
Python environment, a pinned upstream commit and a download, to get something
that is - at this scale - a subprocess and a table.

Two things are deliberately separate:

    Verifier      did the work actually work? exit code, nothing else.
    Attempt       what it cost to get there. time, tokens, retries.

Conflating them is how a fast wrong answer beats a slow right one. A verifier
that fails makes the attempt worthless no matter what it cost; the cost only
ranks attempts that already passed.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger("friday-agent")

#: A verdict nobody argued about.
PASSED = "PASSED"
FAILED = "FAILED"
#: The verifier itself could not run - a missing interpreter, a broken
#: command. Distinct from FAILED on purpose: "the tests say no" and "we never
#: found out" must never be averaged together.
INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class Verifier:
    """
    The command that decides, and the reason it is trusted.

    `command` runs in the workspace after the agent has finished. Exit zero
    is a pass. There is no partial credit and no model in the loop, which is
    the point - a verifier an agent could talk its way past is decoration.
    """

    command: tuple[str, ...]
    #: Seconds. A verifier that hangs must not hold a promotion open forever.
    seconds: float = 600.0
    #: Said in the report, so a reader knows what passing actually proved.
    proves: str = ""

    def describe(self) -> str:
        return " ".join(self.command)


@dataclass
class Attempt:
    """One agent's try at one task, and what came of it."""

    task: str
    agent: str
    model: str = ""
    verdict: str = INCONCLUSIVE
    seconds: float = 0.0
    #: Exit code of the verifier, not of the agent. The agent's own exit code
    #: says whether it finished, which is a different question from whether
    #: it was right.
    exit_code: int | None = None
    tokens: int = 0
    retries: int = 0
    artifacts: tuple[str, ...] = ()
    #: Trimmed. Enough to see why, not enough to store a build log forever.
    detail: str = ""
    at: float = field(default_factory=time.time)

    @property
    def passed(self) -> bool:
        return self.verdict == PASSED


def verify(workspace: str | Path, verifier: Verifier, *,
           sandbox=None) -> tuple[str, int | None, str]:
    """
    Run the verifier and return (verdict, exit_code, detail).

    Inside the sandbox when one is given, because a verifier is a command an
    agent chose the inputs to. `npm test` runs whatever the repository's
    package.json says, and the agent just edited that file.
    """
    from friday import execution as ex

    own = sandbox is None
    box = sandbox or ex.NativeExecutionEnvironment(workspace, name="verify")
    if own:
        box.__enter__()
    try:
        result = box.run(list(verifier.command), timeout=verifier.seconds)
    except Exception as exc:                                # noqa: BLE001
        logger.exception("the verifier could not run")
        return INCONCLUSIVE, None, f"the verifier could not run: {exc}"[:2000]
    finally:
        if own:
            box.terminate()

    if result.timed_out:
        return INCONCLUSIVE, None, f"the verifier exceeded {verifier.seconds:.0f}s"
    if result.exit_code == 127:
        # "Command not found" is not a failing test. Calling it FAILED would
        # blame the code for a missing interpreter.
        return INCONCLUSIVE, 127, result.stderr[:2000]

    verdict = PASSED if result.exit_code == 0 else FAILED
    detail = (result.stdout or "")[-1500:] + (result.stderr or "")[-1500:]
    return verdict, result.exit_code, detail


@dataclass
class Record:
    """
    Every attempt anyone has made, and what it is safe to conclude.

    Persisted as one JSON file. A table would be tidier and this is a few
    hundred rows on a developer machine; the store already has enough tables
    that earn their schema.
    """

    attempts: list[Attempt] = field(default_factory=list)

    def add(self, attempt: Attempt) -> Attempt:
        self.attempts.append(attempt)
        logger.info("eval.attempt task=%s agent=%s model=%s verdict=%s in=%.1fs",
                    attempt.task, attempt.agent, attempt.model or "-",
                    attempt.verdict, attempt.seconds)
        return attempt

    # -- what the numbers support -----------------------------------------

    def scored(self, agent: str = "", model: str = "") -> dict:
        """
        How an agent or model has actually done.

        `INCONCLUSIVE` attempts are counted and excluded from the rate rather
        than silently dropped: a setup that fails to run half the time is a
        real problem with that setup, and hiding it flatters the survivor.
        """
        rows = [a for a in self.attempts
                if (not agent or a.agent == agent)
                and (not model or a.model == model)]
        decided = [a for a in rows if a.verdict in (PASSED, FAILED)]
        passed = [a for a in decided if a.passed]
        return {
            "attempts": len(rows),
            "decided": len(decided),
            "inconclusive": len(rows) - len(decided),
            "passed": len(passed),
            "pass_rate": round(len(passed) / len(decided), 3) if decided else None,
            "median_seconds": (round(statistics.median(a.seconds for a in passed), 1)
                               if passed else None),
            "median_tokens": (int(statistics.median(a.tokens for a in passed))
                              if passed and any(a.tokens for a in passed) else None),
        }

    def best_for(self, task: str, *, minimum: int = 3) -> str | None:
        """
        Which agent to prefer for this kind of task, or None to keep guessing.

        `minimum` is doing the real work. Two attempts is not evidence, and a
        router that swings on one lucky run is worse than a fixed default
        because it looks informed. None means "not enough to say", and the
        caller keeps its existing behaviour.
        """
        by_agent: dict[str, list[Attempt]] = {}
        for attempt in self.attempts:
            if attempt.task == task and attempt.verdict in (PASSED, FAILED):
                by_agent.setdefault(attempt.agent, []).append(attempt)

        ranked: list[tuple[float, float, str]] = []
        for agent, rows in by_agent.items():
            if len(rows) < minimum:
                continue
            passed = [a for a in rows if a.passed]
            rate = len(passed) / len(rows)
            # Correctness first; speed only separates equals. A fast wrong
            # answer must never outrank a slow right one.
            speed = statistics.median(a.seconds for a in passed) if passed else 1e9
            ranked.append((rate, -speed, agent))
        if not ranked:
            return None
        ranked.sort(reverse=True)
        return ranked[0][2]

    def compare(self, task: str) -> list[dict]:
        """Every agent's record on one task, best first. For a human to read."""
        agents = sorted({a.agent for a in self.attempts if a.task == task})
        rows = []
        for agent in agents:
            scored = self.scored(agent=agent)
            scored["agent"] = agent
            rows.append(scored)
        rows.sort(key=lambda r: (r["pass_rate"] is None, -(r["pass_rate"] or 0)))
        return rows

    # -- persistence -------------------------------------------------------

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"attempts": [asdict(a) for a in self.attempts]}, indent=1),
            encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: str | Path) -> "Record":
        source = Path(path)
        if not source.is_file():
            return cls()
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("evaluation: %s is unreadable; starting empty", source)
            return cls()
        record = cls()
        for row in raw.get("attempts", []):
            row["artifacts"] = tuple(row.get("artifacts", ()))
            record.attempts.append(Attempt(**row))
        return record


def record_path() -> Path:
    """Where the evidence lives."""
    from friday.config import DATA_DIR

    return Path(DATA_DIR) / "evaluation" / "attempts.json"


def remember(attempt: Attempt) -> Attempt:
    """
    Persist one attempt where the router will find it next time.

    The reachability audit caught this: `record_path` had no production
    caller, which meant the evidence the router routes from was computed,
    logged and thrown away. `best_for()` would have returned None for ever,
    the router would have defaulted for ever, and both would have looked
    exactly like working correctly.

    Load-add-save rather than a held-open handle: attempts arrive minutes
    apart at most and two processes appending is a real possibility, so the
    cheap thing is also the safe thing here.
    """
    try:
        path = record_path()
        record = Record.load(path)
        record.attempts.append(attempt)
        record.save(path)
    except OSError:
        # A full disk or a locked file must not turn a finished piece of
        # work into a crash; the attempt is returned either way.
        logger.exception("could not persist the attempt")
    return attempt


def graded(task: str, agent: str, workspace: str | Path, verifier: Verifier, *,
           model: str = "", seconds: float = 0.0, tokens: int = 0,
           retries: int = 0, sandbox=None, record: Record | None = None) -> Attempt:
    """
    Verify one finished piece of work and record what happened.

    The one call a caller needs. Everything above it is there so that this
    can be a single line at the end of a development run.
    """
    started = time.monotonic()
    verdict, exit_code, detail = verify(workspace, verifier, sandbox=sandbox)
    attempt = Attempt(
        task=task, agent=agent, model=model, verdict=verdict,
        seconds=seconds or (time.monotonic() - started),
        exit_code=exit_code, tokens=tokens, retries=retries,
        detail=detail[:3000])
    if record is not None:
        record.add(attempt)
    else:
        # No caller-held record: this attempt is still evidence, so it is
        # logged and persisted where the router will read it.
        logger.info("eval.attempt task=%s agent=%s verdict=%s in=%.1fs",
                    attempt.task, attempt.agent, attempt.verdict,
                    attempt.seconds)
        remember(attempt)
    return attempt
