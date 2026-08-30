"""
One development run, from a goal to a decision.

Everything this needs already existed as of an hour ago and none of it was
connected, which is the exact failure that has just been fixed one layer up:
`text_input_callback` called `prepare_turn` and nothing else, so the whole
research pipeline was dead for typed input. Not broken. Not connected.

A module that is built, tested, and never called is decoration. So this is
the thin layer that composes them, and it deliberately adds nothing of its
own - no second run system, no rival task graph, no new store table. It
reuses `TaskBundle`, `ClaudeCodeExecutor`, `WorktreeManager` and `RunManager`
and simply puts the new pieces in the order that makes them mean something:

    understand   codegraph   what is already here, without reading all of it
    staff        roles       who works on it, and why those
    contain      execution   where it runs, and what it may reach
    execute      executor    the coding agent (injected, so it can be swapped)
    verify       evaluation  a command, run afterwards, that decides
    gate         promotion   default no, with a reason

The executor is a constructor argument rather than a hard-coded import. That
is what makes an OpenCode or Codex executor a one-line change later, and it
is what lets the tests drive the whole pipeline without an authenticated CLI
and without spending money on every suite run.

Nothing here promotes anything. `gate()` returns a decision; acting on it is
the caller's, and ultimately a person's.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from friday import (codegraph, evaluation, execution, executor_router,
                    product, promotion, roles)

logger = logging.getLogger("friday-agent")

#: Stages, in order. Named so a half-finished run can say where it stopped
#: rather than just failing.
PREPARED = "PREPARED"
EXECUTED = "EXECUTED"
VERIFIED = "VERIFIED"
DECIDED = "DECIDED"
FAILED = "FAILED"


@dataclass
class DevelopmentRun:
    """
    A goal, and everything learned while pursuing it.

    Built incrementally: each stage fills in its part and the report is
    whatever has been learned so far. A run that died during execution still
    has its graph and its team, and saying so is more useful than an
    exception with nothing attached.
    """

    goal: str
    project: str
    #: The repository this is about. Not the worktree - the executor makes
    #: that, and two owners for one directory is how directories get deleted
    #: twice.
    root: str
    stage: str = ""
    run_id: str = ""
    #: The commit the work started from, so promotion can refuse a patch
    #: verified against one base and applied to another.
    base_commit: str = ""
    readiness: product.Readiness | None = None
    #: Which execution backend verified this, by name.
    backend: str = ""
    artifacts: tuple[str, ...] = ()

    graph: codegraph.CodeGraph | None = None
    team: roles.Team | None = None
    choice: executor_router.Choice | None = None
    attempt: evaluation.Attempt | None = None
    decision: promotion.Decision | None = None
    boundary: dict = field(default_factory=dict)
    changed: tuple[str, ...] = ()
    error: str = ""
    seconds: float = 0.0

    def ready(self, *, scope: str = '', db=None) -> product.Readiness:
        """
        Whether the requirements allow this to start.

        Asked before anything is prepared, because the alternative is handing
        an agent a spec with a hole in it and hoping it guesses the same way
        the boss would. It will not, and the guess arrives looking like work.

        `scope` narrows it to the piece being built, so an open question about
        the netcode does not stop the menu. A blocking question in scope stops
        the run; assumptions do not - they travel with it instead, labelled,
        so the agent knows which parts of its brief nobody actually confirmed.
        """
        self.readiness = product.readiness(self.project, scope=scope, db=db)
        logger.info("development.readiness project=%r state=%s scope=%r",
                    self.project, self.readiness.state, scope)
        return self.readiness

    def note_the_base(self) -> str:
        """Record the commit this run starts from."""
        import subprocess

        try:
            out = subprocess.run(
                ["git", "-C", self.root, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=30)
            self.base_commit = ((out.stdout or "").strip()
                                if not out.returncode else "")
        except (OSError, subprocess.SubprocessError):
            # Not a git repository, or no git: the gate then cannot check
            # the base, which it reports rather than refuses on.
            logger.exception("could not read the base commit")
            self.base_commit = ""
        return self.base_commit

    # -- 1. understand -----------------------------------------------------

    def understand(self, *, refresh: bool = True) -> "DevelopmentRun":
        """
        Build or refresh the structural map, if there is anything to map.

        A new project has nothing to understand. `ensure` returns None for
        one, and that is not a failure - running against an empty repository
        and calling the result a graph is how a bootstrap step becomes a lie.
        """
        try:
            self.graph = codegraph.ensure(self.root, refresh=refresh)
        except Exception:                                   # noqa: BLE001
            # A map that cannot be built costs the run its shortcut, not its
            # life. The agent falls back to reading the repository itself.
            logger.exception("could not map %s; continuing without it", self.root)
            self.graph = None
        return self

    def context_for(self, *, limit: int = 12) -> tuple[str, ...]:
        """
        What to hand the agent about the codebase.

        A summary, never the whole graph. Putting the entire map in the
        prompt reproduces the problem the map exists to solve.
        """
        if self.graph is None:
            return ()
        overview = self.graph.repo_map(limit=limit)
        lines = [f"{overview['files']} files, {overview['symbols']} symbols, "
                 f"{overview['classes']} classes."]
        lines += [f"  {row['path']} ({row['symbols']})"
                  for row in overview["largest"]]
        return tuple(lines)

    # -- 2. staff ----------------------------------------------------------

    def staff(self, *, budget: int = 1200) -> "DevelopmentRun":
        """Choose the specialists, from the goal and the size of the repo."""
        files = len(self.graph.fingerprints) if self.graph else 0
        self.team = roles.compile_team(self.goal, files=files, budget=budget)
        return self

    def pick(self, *, record=None, prefer: str = '') -> executor_router.Choice:
        """
        Choose which coding agent does the work.

        Recorded on the run whether or not it was a measurement, because
        "why did claude get this?" is a question somebody asks weeks later
        and "it was the default" is a perfectly good answer that has to
        survive to be given.
        """
        if record is None:
            # The router reads the evidence the evaluator persists; loading
            # it here rather than requiring it means the one-line caller at
            # the end of a run still gets a measured choice, not a default
            # because nobody passed the record along.
            record = evaluation.Record.load(evaluation.record_path())
        self.choice = executor_router.choose(
            self.project or "development", record=record, prefer=prefer)
        logger.info("development.executor run=%s chose=%s measured=%s",
                    self.run_id or "-", self.choice.executor or "none",
                    self.choice.from_evidence)
        return self.choice

    # -- 3 and 4. contain, and execute ------------------------------------

    async def execute(self, executor, *, allowed_paths: tuple[str, ...] = (),
                      network: bool = True, timeout: float = 1800.0,
                      **kwargs):
        """
        Run the coding agent inside the boundary.

        `executor` must expose `execute(bundle, timeout=...)` returning an
        ActionResult. That is the existing contract, unchanged - this does
        not wrap it, replace it, or reimplement it.

        The environment here contains the *verification* and anything the
        agent spawns. The executor may build its own boundary too when its
        backend says SANDBOX; both are the same object and closing either
        is safe, because `terminate` is idempotent.
        """
        from friday.executors.claude_code import TaskBundle

        started = time.monotonic()
        self.stage = PREPARED
        bundle = TaskBundle(
            goal=self.goal, workspace=self.root, project=self.project,
            context=self.context_for() + (
                (self.team.instructions(),) if self.team else ())
            + self._assumptions(),
            constraints=tuple(f"stay inside {p}" for p in allowed_paths))
        self.run_id = bundle.run_id

        try:
            result = await executor.execute(bundle, timeout=timeout, **kwargs)
            self.stage = EXECUTED
            return result
        except Exception as exc:                            # noqa: BLE001
            self.stage = FAILED
            self.error = f"{type(exc).__name__}: {exc}"
            logger.exception("development run %s failed", bundle.run_id)
            return None
        finally:
            self.seconds = time.monotonic() - started

    def _assumptions(self) -> tuple[str, ...]:
        """
        What the brief is proceeding on without confirmation.

        Passed to the agent as assumptions rather than as requirements. An
        assumption presented as a decision is one the agent will defend later
        in a code review, and nobody ever actually made it.
        """
        if self.readiness is None or not self.readiness.assumptions:
            return ()
        return (("These are ASSUMPTIONS, not decisions he made. If one looks "
                 "wrong, say so rather than building on it:",)
                + tuple(f"  - {a}" for a in self.readiness.assumptions[:6]))

    # -- 5. verify ---------------------------------------------------------

    def verify(self, verifier: evaluation.Verifier, *, agent: str = '', model: str = '', record: evaluation.Record | None = None, backend: str = '', artifacts: tuple[str, ...] = (), into: str | Path | None = None) -> 'DevelopmentRun':
        """
        Decide objectively whether the work works.

        Through an `ExecutionBackend`, with the network off. A verifier is a
        command the agent chose the inputs to - `npm test` runs whatever
        package.json says, and the agent just edited that file.

        Going through the backend rather than constructing a Windows
        environment directly is what keeps this portable. Nothing here knows
        about job objects, and when a container backend exists it will slot
        in behind the same six calls without this method changing.
        """
        try:
            runner = execution.backend_named(backend)
        except LookupError as exc:
            self.attempt = evaluation.Attempt(
                task=self.project or "development", agent=agent or "unknown",
                verdict=evaluation.INCONCLUSIVE, detail=str(exc)[:2000])
            self.stage = VERIFIED
            return self
        self.backend = runner.name
        environment = None
        try:
            environment = runner.create(
                self.root, run_id=f"verify-{self.run_id or self.project}",
                egress=execution.Egress())
            self.attempt = evaluation.graded(
                task=self.project or "development", agent=agent or "unknown",
                workspace=self.root, verifier=verifier, model=model,
                seconds=self.seconds, sandbox=environment, record=record)
            self.boundary = runner.status(environment)
            if artifacts and into is not None:
                self.artifacts = tuple(
                    str(path) for path in
                    runner.collect_artifacts(environment, artifacts, into=into))
        except Exception as exc:                            # noqa: BLE001
            logger.exception("verification could not run")
            self.attempt = evaluation.Attempt(
                task=self.project or "development", agent=agent or "unknown",
                verdict=evaluation.INCONCLUSIVE,
                detail=f"verification could not run: {exc}"[:2000])
        finally:
            if environment is not None:
                runner.terminate(environment)
        self.stage = VERIFIED
        return self

    # -- 6. gate -----------------------------------------------------------

    def gate(self, changed, *, allowed_paths: tuple[str, ...] = (),
             approved: bool = False) -> promotion.Decision:
        """
        May this land? Default no.

        Returns the decision; it does not act on it. Promotion is the
        worktree manager's job and, before that, a person's.
        """
        self.changed = tuple(changed or ())
        self.decision = promotion.decide(
            self.root, self.changed, attempt=self.attempt,
            allowed_paths=allowed_paths, base_commit=self.base_commit,
            approved=approved)
        self.stage = DECIDED
        return self.decision

    # -- the account -------------------------------------------------------

    def report(self) -> dict:
        """
        Everything this run knows about itself.

        A run nobody can explain afterwards is a run nobody should trust,
        and every field here is something a person asked about at least once
        while this was being built.
        """
        return {
            "goal": self.goal,
            "project": self.project,
            "run_id": self.run_id,
            "stage": self.stage or "NOT_STARTED",
            "seconds": round(self.seconds, 1),
            "error": self.error,
            "graph": self.graph.repo_map(limit=5) if self.graph else None,
            "team": self.team.as_dict() if self.team else None,
            "base_commit": self.base_commit[:12],
            "readiness": {
                "state": self.readiness.state,
                "because": self.readiness.because,
                "blockers": list(self.readiness.blockers),
                "assumptions": list(self.readiness.assumptions),
                "blocked": list(self.readiness.blocked),
            } if self.readiness else None,
            "executor": self.choice.as_dict() if self.choice else None,
            "backend": self.backend,
            "environment": self.boundary or None,
            "artifacts": list(self.artifacts),
            "verdict": self.attempt.verdict if self.attempt else None,
            "changed": list(self.changed),
            "gate": self.decision.as_dict() if self.decision else None,
        }

    def explain(self) -> str:
        """The report as something to say out loud."""
        if self.decision is None:
            return (f"{self.goal}: stopped at {self.stage or 'the start'}"
                    + (f" - {self.error}" if self.error else ""))
        if self.decision.allowed:
            return (f"{self.goal}: verified, {len(self.changed)} file(s) "
                    f"changed, ready to promote.")
        return (f"{self.goal}: not promoted - {self.decision.detail}")


def for_goal(goal: str, root: str | Path, *, project: str = "") -> DevelopmentRun:
    """A run that has already understood the repository and chosen a team."""
    run = DevelopmentRun(goal=goal, project=project or Path(root).name,
                         root=str(Path(root).resolve()))
    return run.understand().staff()
