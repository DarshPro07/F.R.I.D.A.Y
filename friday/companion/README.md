# Friday Companion

Lets Friday see and operate the browser you are already signed into.

## Install (2 minutes, no admin, reversible)

```
python -m friday.companion.provision
```

That mints the extension key, pins it into the manifest so the id is stable,
and prints the token. Then:

1. Chrome → `chrome://extensions` → turn on **Developer mode**
2. **Load unpacked** → select `friday/companion/extension`
3. Check the id matches the one provision printed
4. **Details → Extension options** → paste the token → **Save**

Edge is the same, at `edge://extensions`.

The badge shows `ok` when it is paired and Friday is listening.

## Verify the id Chrome actually gave it

After **Load unpacked**, `chrome://extensions` must show exactly the id that
`provision` printed. If it differs, the bridge stays failed-closed and says
so with both ids — fix provisioning rather than loosening the origin check.

## When it is awake

An MV3 service worker is ephemeral, and a timer dies with it. So:

| state | behaviour |
| --- | --- |
| no task | the worker sleeps; a `chrome.alarms` wake-up reconnects if Friday is up |
| task running | heartbeat every 20 s, inside Chrome's 30 s idle window, so an unattended run is never paused because nothing looked busy |
| task finished | heartbeat stops, worker sleeps again |

Friday holds a **lease**, not a flag:

```
session.begin {run_id, lease_ms}   take it
session.renew {run_id}             every ~20s while the run is alive
session.end   {run_id}             release it
```

If Friday crashes without ending it, the lease lapses and the worker sleeps on
its own. A boolean would have pinned it awake for the rest of the browser's
life over a run that no longer exists.

Two rules that make the lease mean something:

- **the heartbeat does not renew it.** Otherwise the keepalive becomes its own
  excuse for existing.
- **it lives in `chrome.storage.session`**, not a global — globals die with
  the worker. It clears on browser restart, which is right: after a restart the
  extension assumes nothing about running work and waits for Friday to say.

A late renewal from a finished run cannot resurrect itself over the run that
replaced it.

Alarms rather than timers because **only alarms survive worker termination** —
that is what makes the connection come back at all.

## Two modes

| | how | when |
| --- | --- | --- |
| **attended** | click the Friday icon on a tab | one tab, one time, needs you there |
| **granted** | approve a site in the options page | works while you are away |

`activeTab` needs a gesture *every time*, so it cannot support "finish this
while I'm away". That is why granted origins exist, and why they are a
deliberate click rather than a default.

## Only this extension

The bridge accepts exactly one origin, derived from the public key pinned in
the manifest:

```
chrome-extension://<the id provision printed>
```

"Starts with `chrome-extension://`" would not do — *every* extension has such
an origin, so a second one installed for something unrelated, or compromised
later, would have been handed a channel into your logged-in browser.

An unprovisioned companion accepts nothing at all. A security check that is
not configured fails closed.

## Rotating the token

```python
from friday.companion.bridge import rotate_token; rotate_token()
```

The only recovery from a leaked secret is a different secret. Re-pair the
extension afterwards.

## What it will not do

- read saved passwords, copy cookies, or decrypt anything
- type into a password field — those are yours to fill
- touch `chrome://` or `edge://` pages
- run arbitrary script in a page: the bridge exposes named commands only
- talk to anything except Friday on `127.0.0.1`

## Remove it

Delete it from `chrome://extensions`. Nothing is left behind — no registry
entry, no service, no installed host.


## Status

```
Companion implementation        VERIFIED
Security protocol               VERIFIED
Lease protocol                  VERIFIED
Alarm logic                     VERIFIED BY UNIT TEST
MV3 lifecycle implementation    VERIFIED BY UNIT TEST

Actual Chrome transport         PENDING LIVE
Actual SW sleep/wake            PENDING LIVE
Actual unattended operation     PENDING LIVE
Actual permission revocation    PENDING LIVE
Actual restart recovery         PENDING LIVE
```

736 unit tests prove the implementation and the protocol. They cannot prove
that Chrome suspended the worker, revived it on an alarm, or refused an origin
after it was revoked — **Chrome is the runtime under test**, and only Chrome
can answer for it.

## The live gate

```
python -m friday.companion.provision      # once
# load unpacked, confirm the id, paste the token
python scripts/live_companion.py          # --quick to skip the slow four
```

Twenty tests in four groups: transport, permissions, lifecycle, recovery. It
pauses and says exactly what to do wherever a human is genuinely needed
(clicking the toolbar icon, revoking a permission, restarting Chrome) and
automates everything else.

## The trace

Every lifecycle moment is recorded, and `trace.dump` pulls it back:

```
19:42:01 [worker-7] worker.started    {extension_id: ...}
19:42:01 [worker-7] lease.begin       {run_id: RUN-42, lease_ms: 60000}
19:42:03 [worker-7] ws.paired
19:42:21 [worker-7] heartbeat         {run_id: RUN-42, lease_in_ms: 39000}
19:42:38 [worker-7] lease.expired
19:43:01 [worker-9] worker.started     <- a new id: Chrome killed the worker
```

That last line is why the trace exists. `worker` is minted when the script is
evaluated, so a **new id is direct evidence of termination** rather than
something inferred from silence.

Each event declares the fields it may carry, and the emitter copies **only
those**:

```js
"lease.begin":      ["run_id", "lease_ms"]
"ws.closed":        ["code", "reason_class"]
"permission.check": ["origin", "granted"]
```

Anything else is dropped, and an unrecognised event still gets a line — silence
would hide a bug — but carries nothing, because nothing has been vetted for it.

This is enforced at emit time rather than by inspecting the source. A test can
prove nobody *wrote* `trace(..., {password})`; it cannot prove a variable named
`reason` will never hold something sensitive at runtime. The WebSocket close
reason is free text from the other end, so it is classified
(`origin-refused`, `token-refused`, …) rather than copied.

Never recorded: the token, DOM text, typed values, cookies, passwords.
