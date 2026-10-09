# CAP-FORGE-003: repository quarantine

**Status:** Implemented, attempt 3 (OS boundary added). **Service:** objective-engine / fabric seam.

## Objective
A new repository is analysed outside the trusted runtime before it receives
FRIDAY authority, secrets, user data or standing network.

## Why attempt 3 exists
Attempts 1-2 enforced file and network refusal with an in-process audit hook.
The verifier showed that cannot be the line: `_winapi.CreateFile`/`ReadFile`
reads a user file, `_winapi.CreateProcess` starts an unhooked child, and
`_overlapped` ConnectEx / `_socket` reach the network, all as ordinary Python,
and the event list is open ended (some of those raise no audit event at all).
Adding names to the list would only move the hole. The fix is a boundary the
planted code cannot step around, with the hook kept for the record.

## What was built
- `friday/appcontainer.py` (new) - a Windows AppContainer through ctypes: a
  per-quarantine profile and SID, `grant` (icacls ACE for that SID), a
  `provision_runtime` copy of the interpreter and stdlib (the real install is
  under the user's profile, whose ACLs are not edited), and `run` =
  CreateProcess with SECURITY_CAPABILITIES + a handle list, started suspended,
  assigned to the Job Object, then resumed. Output goes to inherited temp
  files and is capped.
- `friday/quarantine.py` - `isolation="os"` is the default: the child runs in
  the container. The container's SID is granted on that one root only, so user
  files, `data/`, and a sibling quarantine are all unreachable. If there is no
  AppContainer the default REFUSES (`QuarantineError`); `isolation="tripwire"`
  is an explicit opt-in to the old, weaker tier and reports itself as such.
  The guard is recopied before every run.
- `friday/execution.py` - `NativeExecutionEnvironment(container=)`: commands
  run through the container; a container that cannot start a command is a
  refusal (exit 127), never a fall back to an uncontained process; only
  DENY_ALL egress is allowed with a container; `strength()` reads
  `APPCONTAINER+JOB_OBJECT+TRIPWIRE`.
- `friday/quarantine_guard.py` - now the RECORDER: also checks
  `_winapi.CreateFile` paths and denies `_winapi.CreateProcess/OpenProcess/
  CreateJunction/CreateNamedPipe`; path checks use the lexical form as well as
  the resolved one (inside a container `realpath` keeps unresolved `..`).
- `friday/reachability.py` - `appcontainer` declared FUTURE with the other two.

## Honest tier
`APPCONTAINER+JOB_OBJECT+TRIPWIRE`. The kernel refuses files and network for
the child and every descendant (the token is inherited, the job has no
breakaway). An attempt that avoids the hook is refused but not recorded by it.
An AppContainer is a security boundary, not a VM: kernel and broker-service
bugs, and the Windows/Program Files trees every packaged app can read, are out
of scope. `friday/forge.py` keeps its own refusal; relaxing it is the
CAP-FORGE-004/014 decision.

## Assumptions, revisit if wrong
- Windows is the product host. Other platforms get no OS tier (the default
  refuses; `isolation="tripwire"` is the opt-in).
- Fetching from a URL (git clone with hooks off, through `netguard`) is a
  follow-up; a fetch needs network, which this requirement forbids inside.
- `store` is required: a quarantine must not drop the evidence with its root.
- The container runtime copy (~20 s once, then cached under
  `DATA_DIR/quarantine/_runtime`) is acceptable first-use cost.

## Verification
`tests/test_quarantine.py`. The OS-boundary tests run the planted script
straight under the container with NO hook: `_winapi.CreateFile` -> winerror 5,
open/list/write/append/delete denied, a `_winapi.CreateProcess` grandchild
denied, raw `_socket`, asyncio (ConnectEx) and UDP never reach a local
listener, a second quarantine cannot read the first. A control test shows the
same `_winapi` read succeeds in the tripwire tier. Guard events are unit
tested; the hook-recorded negatives (env, file, socket, process) run through
the full path with denials persisted.
