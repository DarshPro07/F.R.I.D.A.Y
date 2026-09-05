
"""
ADA hands development work to Claude Code, and stays in charge of it.

    ADA run_id  <->  Claude session_id

ADA owns that mapping, and it lives in the database. That is the whole point:
a Claude session is a conversation and conversations end, but the run, the task
bundle, the questions already answered and the decisions already made belong to
ADA and survive. Kill either side and `resume()` picks the same session back up.

The CLI is driven as a subprocess, not through the Agent SDK, because the
subscription is what pays for it - see cli.py for what that costs and what it
rules out.

What Claude says at the end is not the answer to "did it work". `finish()`
builds an ActionResult, and an ActionResult cannot say succeeded without
Verification. "Done" with no diff and no passing test is a claim, not a result.
"""

from __future__ import annotations

import json

import logging

import os

import uuid

from dataclasses import dataclass, field, replace

from pathlib import Path

from friday import contracts as c

from friday.executors import cli

from friday.executors.brokers import Profile, QuestionBroker, denials_from, profile_for

from friday.executors.runs import Recovery, RunManager, session_file_exists

from friday.executors.worktrees import PROMOTED as WT_PROMOTED

from friday.executors.worktrees import (
    Promotion, WorktreeError, WorktreeManager, guard_before_resume,
)

from friday.store import Store

logger = logging.getLogger("friday-agent.executors.claude")

EXECUTION_SCOPE = "local_machine"

#: A development task is not a voice turn. Half an hour is a working session,
#: and the cap exists to stop a wedged run, not to hurry a real one.
DEFAULT_TIMEOUT = float(os.getenv("ADA_CLAUDE_TIMEOUT", "1800"))

HOST = 'HOST'

SANDBOX = 'SANDBOX'

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
CODING_HOSTS = (
    '.anthropic.com',
    '.claude.ai',
    '.npmjs.org',
    '.yarnpkg.com',
    '.pypi.org',
    '.pythonhosted.org',
    '.github.com',
    '.githubusercontent.com',
    '.crates.io',
    '.golang.org',
)


@dataclass
class TaskBundle:
    """
    What ADA hands over. Everything Claude needs and nothing ADA cannot verify.

    `acceptance` is not decoration - it is what `finish()` checks against, and
    a bundle without it can only ever come back PARTIAL.
    """

    goal: str
    workspace: str
    run_id: str = field(default_factory=lambda: f"DEV-{uuid.uuid4().hex[:12]}")
    project: str = ""
    context: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    #: Paths the agent may touch. Also becomes the bridge's ALLOWED SCOPE.
    allowed_paths: tuple[str, ...] = ()
    #: Commands/description of how "done" gets checked afterwards.
    verification: tuple[str, ...] = ()
    #: Who is doing this (from the compiled team), for the worker prompt.
    role: str = ""
    #: How many attempts this task gets before it escalates. 0 = unset.
    iteration_budget: int = 0
    #: The repo-map summary, distinct from `context` (which also carries
    #: team instructions and assumptions for the local prompt).
    known_facts: tuple[str, ...] = ()
    #: What the brief proceeds on without confirmation, kept separate from
    #: `context` so the bridge can render its own ASSUMPTIONS section.
    assumptions: tuple[str, ...] = ()
    isolate: bool = True          # a git worktree, not the live checkout

    def worktree_name(self) -> str:
        """One name, used by the launch flag and by the guard that checks it."""
        return f"ada-{self.run_id.lower()}"

    def run_id_as_uuid(self) -> str:
        """
        --session-id needs a uuid, and a run id is not one.

        Derived rather than random, so the same run always claims the same
        session - which is what makes an interrupted run resumable even if the
        mapping was never written.
        """
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ada-run:{self.run_id}"))

    def prompt(self, *, ask_tool: str = "") -> str:
        parts = [
            "You are executing a task for ADA, which is an assistant that will",
            "verify your work afterwards. Do the task; do not ask for approval",
            "to begin.",
            "",
            f"# Goal\n{self.goal}",
        ]
        if self.context:
            parts.append("\n# What ADA already knows\n"
                         + "\n".join(f"- {line}" for line in self.context))
        if self.role:
            from friday.roles import claude_agent_for
            parts.append(f"\n# Role\nUse the `{claude_agent_for(self.role)}` "
                         f"subagent for this work.")
        constraints = list(self.constraints)
        if self.iteration_budget > 0:
            constraints.append(f"ITERATION BUDGET: {self.iteration_budget} attempts")
        if constraints:
            parts.append("\n# Constraints\n"
                         + "\n".join(f"- {line}" for line in constraints))
        if self.acceptance:
            parts.append("\n# Done means\n"
                         + "\n".join(f"- {line}" for line in self.acceptance))
        if ask_tool:
            parts.append(
                f"\n# When you need a decision\n"
                f"Call `{ask_tool}` with the question and the options you are\n"
                f"choosing between. ADA answers from project memory when it can\n"
                f"and asks the user when it cannot. Do not guess a material\n"
                f"decision, and do not stop to ask in prose - prose is not a\n"
                f"channel ADA can answer on."
            )
        parts.append(
            "\n# Reporting\n"
            "Finish with what you changed and how you checked it. If you could\n"
            "not verify something, say so plainly - ADA re-checks everything\n"
            "and an unverifiable claim is worse than an admitted gap."
        )
        return "\n".join(parts)


@dataclass
class Progress:
    """A running account of the run, for the boss and for the record."""

    tools: list[str] = field(default_factory=list)
    said: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)

    def note(self, event: cli.Event) -> None:
        if event.kind == "tool" and event.name:
            self.tools.append(event.name)
        elif event.kind == "text" and event.text.strip():
            self.said.append(event.text.strip())

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for name in self.tools:
            counts[name] = counts.get(name, 0) + 1
        return ", ".join(f"{name}x{n}" if n > 1 else name
                         for name, n in sorted(counts.items())) or "nothing"


class ClaudeCodeExecutor:
    """
    One development run, from bundle to verified result.

    Not a chat wrapper: it owns the session id, the permission profile, the
    question channel and the verdict.
    """

    def __init__(self, store: Store, *, mcp_config: str | None = None,
                 ask_user=None, model: str | None = None,
                 backend: str = HOST) -> None:
        self.store = store
        self.mcp_config = mcp_config
        self.ask_user = ask_user
        self.model = model
        self.backend = backend
        self.run: cli.Run | None = None
        self.progress = Progress()
        self.runs = RunManager(store)
        self.sandbox = None

    # -- session bookkeeping ----------------------------------------------

    def _remember_session(self, bundle: TaskBundle, session_id: str) -> None:
        """ADA owns the mapping, so a lost CLI is not a lost task."""
        try:
            self.store.remember(
                subject=f"executor.claude.session.{bundle.run_id}",
                value=session_id, kind="FACT",
                source=f"claude code run for: {bundle.goal[:120]}",
                scope="goals", run_id=bundle.run_id)
        except Exception:
            logger.exception("could not persist the session id; resume will not work")

    def session_for(self, run_id: str) -> str | None:
        rows = self.store.recall(f"executor.claude.session.{run_id}")
        return str(rows[0]["value"]) if rows else None

    # -- running -----------------------------------------------------------

    def launch_for(self, bundle: TaskBundle, *, profile: Profile | None = None,
                   resume: str | None = None) -> cli.Launch:
        chosen = profile or profile_for(bundle.goal)
        ask_tool = "mcp__ada__ada_ask" if self.mcp_config else ""
        return cli.Launch(
            prompt=bundle.prompt(ask_tool=ask_tool),
            cwd=bundle.workspace,
            session_id=None if resume else bundle.run_id_as_uuid(),
            resume=resume,
            worktree=bundle.worktree_name() if bundle.isolate else None,
            mcp_config=self.mcp_config,
            model=self.model,
            **chosen.as_launch_kwargs(),
        )

    async def execute(self, bundle: TaskBundle, *, profile: Profile | None = None,
                      resume: str | None = None,
                      timeout: float = DEFAULT_TIMEOUT,
                      on_event=None) -> c.ActionResult:
        run_ctx = c.Run.create(bundle.goal, capability="development")
        started = c.started(run_ctx.run_id, "executor.claude_code")

        if not cli.available():
            return run_ctx.record(c.failed(
                started, "the claude CLI is not on PATH; nothing was run"))

        # FR-013 / FR-056: same admission gate as the Hermes worker. A
        # secondary worker under pressure is refused with the measured
        # reason, recorded as a failed action - never silently started.
        from friday import governor as G
        decision = G.governor().admit(G.WORKER, label=f"claude:{bundle.goal[:40]}",
                                      objective_id=bundle.run_id)
        if not decision.admitted:
            return run_ctx.record(c.failed(
                started, f"governor {decision.decision}: {decision.reason}; "
                         f"nothing was run"))
        lease = decision.lease
        # Recorded before anything starts, so a run that dies during startup
        # still leaves something to recover. DuplicateRun is deliberately not
        # caught: one run means at most one process, and the caller decides.
        try:
            self.runs.open(bundle)
        except BaseException:
            G.governor().release(lease)
            raise

        launch = self.launch_for(bundle, profile=profile, resume=resume)
        self.sandbox = self._sandbox_for(bundle)
        self.run = cli.Run(launch, sandbox=self.sandbox)
        self.progress = Progress()
        final: dict = {}

        try:
            async for event in self.run.events(timeout=timeout):
                self.progress.note(event)
                if event.kind == "init" and event.session_id:
                    self._remember_session(bundle, event.session_id)
                    self.runs.running(
                        bundle.run_id, session_id=event.session_id,
                        pid=self.run.process.pid if self.run.process else None)
                elif event.kind == "tool":
                    self.runs.saw(bundle.run_id, f"using {event.name}")
                elif event.kind == "result":
                    final = event.raw
                if on_event is not None:
                    try:
                        on_event(event)
                    except Exception:
                        logger.exception("progress callback failed")
        except TimeoutError as exc:
            result = run_ctx.record(c.failed(started, f"{exc}; the run was killed"))
            self.runs.close(bundle.run_id, result)
            self._close_sandbox()
            G.governor().release(lease)
            return result
        except Exception as exc:
            result = run_ctx.record(c.failed(
                started, f"the executor failed: {type(exc).__name__}: {exc}"))
            self.runs.close(bundle.run_id, result)
            self._close_sandbox()
            G.governor().release(lease)
            return result

        result = run_ctx.record(self.finish(bundle, started, final))
        self.runs.close(bundle.run_id, result)
        self._close_sandbox()
        G.governor().release(lease)
        return result

    def _sandbox_for(self, bundle: TaskBundle):
        """
        The boundary this run gets, or None to run on the host as before.

        The coding agent genuinely needs the network - it talks to its own
        API - so this is an allowlist rather than a denial. What it does not
        need is every provider key in Friday's environment, an unbounded
        process count, or the ability to leave something running afterwards.
        Those are what the boundary is actually for.
        """
        if self.backend != SANDBOX:
            return None
        from friday import execution as ex

        try:
            box = ex.NativeExecutionEnvironment(
                bundle.workspace, name=bundle.run_id,
                egress=ex.Egress(mode=ex.ALLOWLIST, hosts=CODING_HOSTS))
            box.__enter__()
            logger.info("executor.sandboxed run=%s strength=%s",
                        bundle.run_id, box.strength())
            return box
        except Exception:
            # Asked for a boundary and could not build one: that is a refusal
            # to run, not a reason to run on the host after all.
            logger.exception("could not sandbox run %s", bundle.run_id)
            raise

    def _close_sandbox(self) -> None:
        if self.sandbox is not None:
            self.sandbox.terminate()
            self.sandbox = None

    async def cancel(self) -> bool:
        """"ADA, stop the coding task." Kills the tree, not just the parent."""
        return await self.run.cancel() if self.run is not None else False

    async def resume(self, run_id: str, bundle: TaskBundle, **kwargs) -> c.ActionResult:
        """Pick a run back up after either side died."""
        session = self.session_for(run_id)
        if session is None:
            raise LookupError(f"no claude session recorded for {run_id}")
        return await self.execute(bundle, resume=session, **kwargs)

    async def continue_run(self, run_id: str, **kwargs) -> c.ActionResult:
        """
        Continue a run found in the database rather than started here.

        This is what a brand-new ADA conversation calls. It does not need the
        bundle, the session or the workspace passed in - all of it was written
        down when the run opened, which is the entire point.
        """
        recovery = self.runs.recover(run_id, session_exists=session_file_exists)
        run_ctx = c.Run.create(f"continue {run_id}", capability="development")
        started = c.started(run_ctx.run_id, "executor.claude_code.continue")

        if recovery.action == "attach":
            # Already being worked on. Returning status is the correct answer;
            # launching a second executor for one run is not.
            return run_ctx.record(c.partial(
                started, recovery.reason,
                output={"execution_scope": EXECUTION_SCOPE, "run_id": run_id,
                        "status": recovery.run.get("status"),
                        "pid": recovery.run.get("pid"),
                        "already_running": True}))

        if recovery.action == "ask":
            return run_ctx.record(c.failed(started, recovery.reason))

        bundle = self.runs.bundle_of(recovery.run)

        if bundle.isolate:
            # The dangerous case. Without its worktree the session can fall
            # back to the directory it was launched from, and an isolated run
            # would quietly become an unisolated one.
            manager = WorktreeManager(bundle.workspace)
            safe, why = guard_before_resume(manager, bundle.worktree_name())
            if not safe:
                if recovery.action == "resume":
                    logger.warning("%s: %s", run_id, why)
                    # A restart makes a fresh worktree; a resume would not.
                    recovery = Recovery("restart", recovery.run, why)
                else:
                    logger.info("%s: worktree missing, a new one will be made",
                                run_id)

        if recovery.action == "resume":
            logger.info("resuming %s into session %s", run_id, recovery.session_id)
            return await self.execute(bundle, resume=recovery.session_id, **kwargs)

        # restart: the session is gone, the work is not. A fresh session gets
        # the original bundle plus what the last one managed to do, so it
        # continues rather than starting the task over.
        logger.info("restarting %s from the task bundle", run_id)
        previous = str(recovery.run.get("summary") or "").strip()
        if previous:
            bundle = replace(bundle, context=bundle.context + (
                "A previous executor worked on this and its session is gone. "
                "What it reported: " + previous[:1200],
                "Check the repository as it stands before changing anything - "
                "some of the work may already be done.",
            ))
        return await self.execute(bundle, **kwargs)

    # -- the verdict -------------------------------------------------------

    def finish(self, bundle: TaskBundle, started: c.ActionResult,
               final: dict) -> c.ActionResult:
        """
        Turn "Claude says it is done" into something with evidence behind it.

        Claude's own summary is recorded and is never the verification. What
        verifies a run is what changed on disk and what the checks did.
        """
        said = str(final.get("result") or "").strip()
        denials = denials_from(final)
        output = {
            "execution_scope": EXECUTION_SCOPE,
            "run_id": bundle.run_id,
            "session_id": self.run.session_id if self.run else "",
            "workspace": bundle.workspace,
            "tools_used": self.progress.summary(),
            "claude_says": said[:4000],
            "refused": denials,
            "cost_usd": final.get("total_cost_usd"),
            "turns": final.get("num_turns"),
        }

        if not final:
            return c.failed(started, "claude produced no result event")
        if final.get("is_error"):
            return c.failed(started, f"claude reported an error: {said[:300]}")

        # An isolated run changed the worktree, not the checkout it was
        # launched from. Asking the wrong directory would report "nothing
        # changed" for every successful isolated run.
        where = str(WorktreeManager(bundle.workspace).path_for(
            bundle.worktree_name())) if bundle.isolate else bundle.workspace
        changed = self.changed_files(where)
        output["changed_in"] = where
        output["changed_files"] = changed[:40]

        if denials:
            return c.partial(
                started,
                f"{len(denials)} action(s) were refused by policy: "
                + ", ".join(sorted({d['tool'] for d in denials})),
                output=output)

        if not changed:
            # Nothing moved. For an exploration that is the correct outcome;
            # for a build it means the work did not land, whatever was said.
            return c.partial(
                started, "claude finished but nothing changed on disk",
                output=output)

        return c.succeeded(
            started, output=output,
            verification=c.Verification(
                method="worktree_diff",
                evidence=f"{len(changed)} file(s) changed in {where}: "
                         f"{', '.join(changed[:6])}"
                         + (" ..." if len(changed) > 6 else "")
                         + f"; tools used: {self.progress.summary()}",
            ))

    # -- promotion ---------------------------------------------------------

    def promote(self, bundle: TaskBundle, *, target: str = "",
                message: str = "") -> "Promotion":
        """
        Move verified work from the worktree onto the target branch.

        Called only after the run's ActionResult says the work is real - the
        gate is proof, not Claude's summary, and this method does not re-decide
        that.
        """
        manager = WorktreeManager(bundle.workspace)
        target = target or manager.current_branch()
        promotion = manager.promote(
            bundle.worktree_name(), target=target,
            message=message or f"ADA {bundle.run_id}: {bundle.goal[:90]}")
        self.store.record_promotion(bundle.run_id,
                                    worktree=bundle.worktree_name(),
                                    promotion=promotion)
        return promotion

    def reject(self, bundle: TaskBundle, reason: str) -> "Promotion":
        """Nothing moves, and the worktree stays so the failure can be read."""
        manager = WorktreeManager(bundle.workspace)
        promotion = manager.reject(bundle.worktree_name(), reason)
        self.store.record_promotion(bundle.run_id,
                                    worktree=bundle.worktree_name(),
                                    promotion=promotion)
        return promotion

    def rollback(self, bundle: TaskBundle, *, reason: str = "") -> "Promotion":
        """
        Undo a promotion from what was recorded, not from anyone's memory.

        The rollback target was written down at promotion time precisely
        because the moment you need it is the moment you cannot afford to work
        it out.
        """
        from friday.executors.worktrees import Promotion

        row = self.store.latest_promotion(bundle.run_id)
        if row is None or row["state"] != WT_PROMOTED:
            raise WorktreeError(
                f"{bundle.run_id} has no completed promotion to roll back")
        previous = Promotion(
            state=row["state"], branch=row["branch"], target=row["target_branch"],
            base_commit=row["base_commit"] or "", result_commit=row["result_commit"] or "",
            merge_commit=row["merge_commit"] or "", reason=row["reason"] or "")

        rolled = WorktreeManager(bundle.workspace).rollback(previous, reason=reason)
        self.store.record_promotion(bundle.run_id,
                                    worktree=bundle.worktree_name(),
                                    promotion=rolled)
        return rolled

    def cleanup(self, bundle: TaskBundle, *, keep: bool = False) -> dict:
        """A headless run cleans up nothing. The lifecycle is ADA's."""
        return WorktreeManager(bundle.workspace).cleanup(
            bundle.worktree_name(), keep=keep)

    @staticmethod
    def changed_files(workspace: str) -> list[str]:
        """git's answer, not Claude's."""
        import subprocess

        try:
            out = subprocess.run(
                ["git", "status", "--porcelain"], cwd=workspace,
                capture_output=True, text=True, timeout=60)
        except Exception:
            return []
        return [line[3:].strip() for line in (out.stdout or "").splitlines()
                if line.strip()]


def broker_for(store: Store, bundle: TaskBundle, ask_user=None) -> QuestionBroker:
    return QuestionBroker(store=store, project=bundle.project, ask_user=ask_user)


def workspace_is_sane(path: str) -> tuple[bool, str]:
    """
    A worktree needs a repository, and a run needs a workspace that exists.

    Checked before launch because the CLI's failure for this is a wall of
    node output that says nothing useful.
    """
    target = Path(path)
    if not target.is_dir():
        return False, f"{path} is not a directory"
    if not (target / ".git").exists():
        return False, f"{path} is not a git repository - isolation needs one"
    return True, "ok"
