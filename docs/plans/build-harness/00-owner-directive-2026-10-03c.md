# Owner ruling 2026-10-03 (c): SEC-005 commands, and EXEC-SBX-001

The owner's answer, verbatim, to the controller's question after SEC-005's verifier failed it (2026-10-03):
during a scoped Hermes delegation `bridge_run_command` and interpreters (python, node) could still read or write
anywhere, so the file envelope was advisory for commands. The options offered were: disable now and sandbox
later (recommended); OS sandbox first; disable permanently; accept advisory commands.

---

Select option 1. Merge SEC-005 with command execution disabled for scoped delegations. Add EXEC-SBX-001 as the immediate restoration path. Build an OS-enforced scoped command runner using AppContainer/LPAC as the preferred normal Windows backend and Windows Sandbox as the stronger isolated backend. Use Job Objects for process-tree/resource control, not as the filesystem sandbox. Restore bridge_run_command/interpreters to scoped delegations only after real escape tests prove that Python, Node, PowerShell, child processes, traversal, junctions and network paths cannot exceed the ExecutionEnvelope.
