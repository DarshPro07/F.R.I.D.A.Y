# Owner ruling 2026-10-03 (d): CTX-003 - authority to start a development run

The owner's answer, verbatim, to the controller's question after CTX-003 integrated as PARTIAL (the objective
engine reaches workers only through the context compiler) and its next run came back BLOCKED on the same point:
what policy category and router group a capability that starts a development run in a repo carries. Options
offered: like self-development (FILE_WRITE to start, COMMAND_EXECUTION to merge; recommended); ask every time in
one step; auto inside an approved objective; leave unwired until EXEC-SBX-001.

---

Select option 3, modified as follows: starting a development run is a selfdev/FILE_WRITE-class action and may proceed without another prompt only when APPROVAL-001 has a valid lease explicitly covering development work for that workspace/repo and blast radius. The run remains confined to its own sandbox worktree. Merging/promoting into a live branch is a separate higher-authority action and requires fresh approval unless the existing lease explicitly includes promotion to that exact branch. Until EXEC-SBX-001 is available, development runs requiring command execution remain unavailable rather than falling back to unrestricted host commands.
