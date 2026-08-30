"""
The channel to the browser he is actually signed into.

Playwright drives a browser signed into nothing, and since Chrome 136 the
remote-debugging switches refuse the normal profile outright - Google's own
guidance is to use an isolated profile for automation. Both roads lead away
from the thing that matters: his real session, his real accounts, his real
tabs.

An extension is the way in, because the extension runs *inside* that session
and Chrome stays in charge of it.

## Why a loopback socket rather than native messaging

Native messaging was the obvious choice and I looked at it first. Chrome spawns
the native host itself, so only the registered extension can reach it - a real
advantage.

It does not survive contact with the shape of this system. Chrome spawns the
host **per connection**, and Friday is a long-lived process that has to be
reached from it. So the host becomes a relay that opens a loopback socket to
Friday anyway: the port does not go away, it just gains a hop, a registry
entry and an installer.

So the extension talks to Friday directly, and the security lives where it was
always going to live:

    bound to 127.0.0.1        never a routable interface
    Origin must be an         a web page's Origin is its own site; it can
      extension                 never be chrome-extension://
    paired token              WebSocket has no CORS, so any local process and
                                any page can *open* a connection here. The
                                token is what makes opening one useless.
    command allow-list        the extension may invoke declared commands and
                                nothing else
    every command logged      an audit trail, because this operates his
                                logged-in accounts

## activeTab is not enough, and that is a design constraint

`activeTab` needs a user gesture every time - clicking the extension, a
keyboard shortcut. Good for a privacy-first manual mode, useless for "finish
this while I'm away". Autonomous operation needs host permissions granted
per-origin, which the extension requests explicitly and the user grants once
per site.

So there are two modes, and the difference is visible rather than buried:

    attended      activeTab, one gesture, one tab
    granted       an origin he has approved; works unattended

## Page content is untrusted input

Everything crossing this bridge from the browser - DOM text, titles, URLs,
form labels - is attacker-influenceable. It may become *data* for the model.
It may never reach the policy engine, the origin allow-list, or the token.
That separation is structural here: the command handlers never call into
policy with page-derived values.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from friday.config import DATA_DIR

logger = logging.getLogger("friday-agent.companion")

HOST = "127.0.0.1"
DEFAULT_PORT = int(os.getenv("ADA_COMPANION_PORT", "8791"))

#: The pairing token. Written on first run; pasted into the extension once.
TOKEN_PATH = Path(os.getenv("ADA_COMPANION_TOKEN")
                  or DATA_DIR / "companion" / "token.txt").resolve()

#: What the extension is allowed to ask for. Anything else is refused by name,
#: so a compromised or outdated extension cannot invent a command.
COMMANDS = frozenset({
    "hello",            # pairing check, and Chrome's own view of the id
    # Friday declares when a run starts and stops. The extension keeps its
    # service worker awake only in between - Chrome terminates idle workers,
    # and an unattended run cannot afford to be paused because nothing looked
    # like it was happening.
    "session.begin",
    "session.renew",
    "session.end",
    "session.status",
    "trace.dump",       # diagnostics; never carries secrets or page text
    "tabs.list",
    "tabs.current",
    "tabs.focus",
    "page.read",
    "page.find",
    "page.click",
    "page.type",
    "nav.open",
    "account.observe",
})

#: Commands that change something. Kept separate so a read-only session is a
#: real thing rather than a promise.
MUTATING = frozenset({"tabs.focus", "page.click", "page.type", "nav.open"})

#: How long to wait for the browser to answer one command.
COMMAND_TIMEOUT = 30.0

#: How long the browser keeps itself awake without hearing from Friday again.
#: Long enough to survive a slow step, short enough that a crashed Friday is
#: forgotten within a minute.
LEASE_MS = 60000

#: Comfortably inside the lease, so an ordinary hiccup does not drop it.
RENEW_EVERY_SECONDS = 20.0


def load_token(*, create: bool = True) -> str:
    """The shared secret, created once and reused. Never logged."""
    if TOKEN_PATH.is_file():
        token = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if token:
            return token
    if not create:
        return ""
    return rotate_token()


def rotate_token() -> str:
    """
    Mint a new secret and invalidate the old one.

    The only recovery from a leaked token is a different token, so this has to
    exist rather than being a redeploy. The extension has to be re-paired
    afterwards, which is the point.
    """
    from friday.companion.pairing import lock_down

    token = secrets.token_urlsafe(32)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(token, encoding="utf-8")
    lock_down(TOKEN_PATH)
    # The path, never the value. A secret in a log file is a secret in a
    # backup, a support bundle and a screen share.
    logger.info("companion: pairing token written to %s", TOKEN_PATH)
    return token


def origin_is_extension(origin: str) -> bool:
    """
    Shape check only: is this an extension origin at all?

    Kept because it is the cheap first filter - a page at https://evil.example
    can open a WebSocket to 127.0.0.1, since WebSocket has no CORS, but its
    Origin is its own site and cannot be forged. It is NOT sufficient on its
    own: see origin_allowed.
    """
    return (origin or "").startswith(("chrome-extension://", "moz-extension://",
                                      "edge-extension://"))


def origin_allowed(origin: str) -> tuple[bool, str]:
    """
    Is this *the* Friday extension?

    "Starts with chrome-extension://" was not enough, and the reason is worth
    being blunt about: every extension has such an origin. A second extension
    the user installs for something unrelated - or one compromised later -
    would have satisfied that check and been handed a channel into their
    logged-in browser.

    So exactly one id is accepted, derived from the public key pinned in the
    manifest. An unprovisioned companion accepts nothing: a security check
    that is not configured must fail closed, not open.
    """
    from friday.companion.pairing import allowed_origin

    if not origin_is_extension(origin):
        return False, "not an extension origin"
    expected = allowed_origin()
    if not expected:
        return False, ("the companion is not provisioned - run "
                       "`python -m friday.companion.provision` first")
    if not secrets.compare_digest(origin, expected):
        # Both ids, because the useful question when this fires is "did Chrome
        # give my extension the id I pinned?" and a bare refusal does not
        # answer it.
        return False, (f"a different extension: got {origin.split('//')[-1]}, "
                       f"expected {expected.split('//')[-1]}")
    return True, "the Friday extension"


@dataclass
class Command:
    name: str
    params: dict = field(default_factory=dict)
    id: str = ""

    def as_json(self) -> str:
        return json.dumps({"id": self.id, "command": self.name,
                           "params": self.params})


@dataclass
class AuditEntry:
    at: float
    command: str
    allowed: bool
    reason: str = ""
    origin: str = ""

    def as_dict(self) -> dict:
        return {"at": self.at, "command": self.command, "allowed": self.allowed,
                "reason": self.reason, "origin": self.origin}


class Companion:
    """
    Friday's end of the channel. One browser at a time.

    Not started with Friday: it listens only once a browser task needs it, and
    nothing is left listening when it does not.
    """

    def __init__(self, *, port: int = DEFAULT_PORT, token: str = "",
                 read_only: bool = False) -> None:
        self.port = port
        self.token = token or load_token()
        self.read_only = read_only
        self._server = None
        self._socket = None            # the connected extension
        self._pending: dict[str, asyncio.Future] = {}
        self._counter = 0
        self.audit: list[AuditEntry] = []
        self.connected_at: float | None = None
        self.last_heartbeat: float | None = None
        self.session_open = False
        self.session_run = ""

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def attached(self) -> bool:
        return self._socket is not None

    async def start(self) -> None:
        import websockets

        if self._server is not None:
            return
        self._server = await websockets.serve(
            self._session, HOST, self.port,
            # The browser is on the same machine; a long ping interval keeps
            # an idle connection alive without chattering.
            ping_interval=30, ping_timeout=30, max_size=4_000_000)
        logger.info("companion: listening on ws://%s:%d", HOST, self.port)

    async def stop(self) -> None:
        if self._socket is not None:
            try:
                await self._socket.close()
            except Exception:
                pass
            self._socket = None
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        logger.info("companion: stopped")

    # -- the connection ----------------------------------------------------

    def _record(self, command: str, allowed: bool, reason: str = "",
                origin: str = "") -> None:
        self.audit.append(AuditEntry(time.time(), command, allowed, reason, origin))
        del self.audit[:-500]

    async def _session(self, socket) -> None:
        origin = ""
        try:
            origin = (socket.request.headers.get("Origin", "")
                      if hasattr(socket, "request") else
                      socket.request_headers.get("Origin", ""))
        except Exception:
            origin = ""

        permitted, why = origin_allowed(origin)
        if not permitted:
            self._record("connect", False, f"origin refused: {why}", origin)
            logger.warning("companion: refused a connection from %r (%s)",
                           origin[:80], why)
            await socket.close(code=4403, reason="not the Friday extension")
            return

        # The token arrives as the first message, not in the URL: query
        # strings end up in logs and history.
        try:
            greeting = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
        except Exception:
            await socket.close(code=4400, reason="no greeting")
            return

        if not secrets.compare_digest(str(greeting.get("token", "")), self.token):
            self._record("connect", False, "bad token", origin)
            logger.warning("companion: refused a connection with a bad token")
            await socket.close(code=4401, reason="pair Friday first")
            return

        self._socket = socket
        self.connected_at = time.time()
        self._record("connect", True, "paired", origin)
        logger.info("companion: browser attached (%s)", origin[:60])
        await socket.send(json.dumps({"ok": True, "friday": "paired"}))

        try:
            async for raw in socket:
                self._deliver(raw)
        except Exception:
            pass
        finally:
            if self._socket is socket:
                self._socket = None
                self.connected_at = None
            logger.info("companion: browser detached")

    def _deliver(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            return
        if message.get("heartbeat"):
            # Keeps the service worker alive; carries nothing and answers
            # nothing.
            self.last_heartbeat = time.time()
            return
        future = self._pending.pop(str(message.get("id", "")), None)
        if future is not None and not future.done():
            future.set_result(message)

    # -- asking the browser to do something --------------------------------

    async def call(self, name: str, **params) -> dict:
        """
        Run one command in the browser and wait for its answer.

        Refuses by name rather than silently: an unknown command means the
        extension and Friday disagree about what exists, and guessing is how
        that becomes a security problem.
        """
        if name not in COMMANDS:
            self._record(name, False, "not an allowed command")
            return {"ok": False, "error": f"{name!r} is not a companion command"}
        if self.read_only and name in MUTATING:
            self._record(name, False, "read-only session")
            return {"ok": False, "error": f"{name} changes the page and this "
                                          f"session is read-only"}
        if self._socket is None:
            self._record(name, False, "no browser attached")
            return {"ok": False, "error": "no browser is attached - is the "
                                          "Friday companion extension running "
                                          "and paired?"}

        self._counter += 1
        command = Command(name=name, params=params, id=str(self._counter))
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[command.id] = future

        self._record(name, True, origin="friday")
        try:
            await self._socket.send(command.as_json())
            reply = await asyncio.wait_for(future, timeout=COMMAND_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending.pop(command.id, None)
            return {"ok": False, "error": f"{name} timed out after "
                                          f"{COMMAND_TIMEOUT:.0f}s"}
        except Exception as exc:
            self._pending.pop(command.id, None)
            return {"ok": False, "error": f"{name} failed: {type(exc).__name__}: {exc}"}
        return reply

    async def begin_session(self, run_id: str, *,
                            lease_ms: int = LEASE_MS) -> dict:
        """
        Take a lease on the browser's attention for one run.

        A lease rather than a flag: if Friday dies without ending it, the
        extension lets it lapse and goes back to sleep. A boolean would have
        pinned the worker awake for the rest of the browser's life over a run
        that no longer exists.
        """
        result = await self.call("session.begin", run_id=run_id,
                                 lease_ms=lease_ms)
        self.session_open = bool(result.get("ok"))
        self.session_run = run_id if self.session_open else ""
        return result

    async def renew_session(self, run_id: str, *,
                            lease_ms: int = LEASE_MS) -> dict:
        return await self.call("session.renew", run_id=run_id,
                               lease_ms=lease_ms)

    async def end_session(self, run_id: str = "") -> dict:
        result = await self.call("session.end",
                                 run_id=run_id or self.session_run)
        self.session_open = False
        self.session_run = ""
        return result

    async def hold(self, run_id: str, *, still_running) -> None:
        """
        Renew the lease while the run manager says the run is alive.

        `still_running()` is Friday's own authority, asked each time rather
        than assumed - the browser is a participant in the run, never the
        record of whether one exists.
        """
        await self.begin_session(run_id)
        try:
            while True:
                await asyncio.sleep(RENEW_EVERY_SECONDS)
                if not still_running():
                    break
                renewed = await self.renew_session(run_id)
                if not renewed.get("ok"):
                    logger.info("companion: lease not renewed (%s)",
                                renewed.get("error"))
                    break
        finally:
            # Including on cancellation and on error. A lease left held is the
            # exact failure this design exists to avoid.
            await self.end_session(run_id)

    def describe(self) -> dict:
        return {
            "listening": self.running,
            "session_open": self.session_open,
            "session_run": self.session_run,
            "seconds_since_heartbeat": (round(time.time() - self.last_heartbeat)
                                        if self.last_heartbeat else None),
            "port": self.port,
            "browser_attached": self.attached,
            "attached_for_seconds": (round(time.time() - self.connected_at)
                                     if self.connected_at else None),
            "read_only": self.read_only,
            "commands": sorted(COMMANDS),
            "recent_audit": [entry.as_dict() for entry in self.audit[-10:]],
        }


#: One per process, started lazily by whatever needs the browser.
_companion: Companion | None = None


def companion() -> Companion:
    global _companion
    if _companion is None:
        _companion = Companion()
    return _companion


async def ensure_started() -> Companion:
    instance = companion()
    if not instance.running:
        await instance.start()
    return instance
