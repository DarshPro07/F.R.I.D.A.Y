"""
The channel into the browser he is signed into.

This is the most dangerous thing in the codebase: it operates live,
authenticated sessions. So the tests here are almost entirely about who is
allowed to speak to it and what they may say.

WebSocket has no CORS. Any local process, and any web page he visits, can
*open* a connection to 127.0.0.1:8791. Two things make that useless:

  * the Origin header - a page's Origin is its own site and cannot be forged
    into chrome-extension://
  * a paired token, sent as the first message rather than in the URL, because
    query strings end up in logs

Both are tested against a real server on a real socket. A mocked handshake
would prove nothing about the thing that actually accepts connections.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from friday.companion import bridge as B


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def companion(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TOKEN_PATH", tmp_path / "token.txt")
    return B.Companion(port=free_port(), token="test-token-abc")


async def connect(companion, *, origin: str, token: str):
    """A raw client, so the handshake itself is what is under test."""
    import websockets

    url = f"ws://{B.HOST}:{companion.port}"
    socket = await websockets.connect(
        url, additional_headers={"Origin": origin}, open_timeout=5)
    await socket.send(json.dumps({"token": token}))
    return socket


# ---------------------------------------------------------------------------
# Who may connect
# ---------------------------------------------------------------------------


def test_a_web_page_cannot_reach_the_bridge(companion):
    """
    The attack this exists to stop: a site he visits opening a socket to
    127.0.0.1 and driving his logged-in browser. Its Origin is its own site.
    """
    async def go():
        await companion.start()
        try:
            # The close arrives during the handshake, so the failure can land
            # on connect, on send or on recv depending on timing. What matters
            # is that it never attaches - assert the outcome, not the throw.
            try:
                socket = await connect(companion,
                                       origin="https://evil.example",
                                       token="test-token-abc")
                await asyncio.wait_for(socket.recv(), timeout=3)
            except Exception:
                pass
            return companion.attached
        finally:
            await companion.stop()

    assert asyncio.run(go()) is False


def test_a_local_process_without_the_token_cannot_reach_it(companion):
    """Right origin, wrong secret. Something else on the machine, guessing."""
    async def go():
        await companion.start()
        try:
            try:
                socket = await connect(companion,
                                       origin="chrome-extension://abcdefgh",
                                       token="not-the-token")
                await asyncio.wait_for(socket.recv(), timeout=3)
            except Exception:
                pass
            return companion.attached
        finally:
            await companion.stop()

    assert asyncio.run(go()) is False


def test_the_real_extension_pairs(companion, monkeypatch):
    ours = "chrome-extension://abcdefgh"
    monkeypatch.setattr("friday.companion.pairing.allowed_origin", lambda: ours)

    async def go():
        await companion.start()
        try:
            socket = await connect(companion,
                                   origin="chrome-extension://abcdefgh",
                                   token="test-token-abc")
            greeting = json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
            attached = companion.attached
            await socket.close()
            return greeting, attached
        finally:
            await companion.stop()

    greeting, attached = asyncio.run(go())
    assert greeting["friday"] == "paired"
    assert attached is True


@pytest.mark.parametrize("origin", [
    "https://evil.example",
    "http://127.0.0.1:3000",
    "file://",
    "",
    "chrome-extension",              # not a scheme
    "https://chrome-extension://x",  # nested, not a real extension origin
])
def test_only_an_extension_origin_is_accepted(origin):
    assert not B.origin_is_extension(origin)


def test_the_browsers_that_have_extensions_are_accepted():
    for origin in ("chrome-extension://abc", "moz-extension://abc",
                   "edge-extension://abc"):
        assert B.origin_is_extension(origin)


# ---------------------------------------------------------------------------
# What may be said
# ---------------------------------------------------------------------------


def test_an_unknown_command_is_refused_by_name(companion):
    result = asyncio.run(companion.call("page.eval_javascript", code="alert(1)"))
    assert result["ok"] is False
    assert "not a companion command" in result["error"]


def test_a_command_with_no_browser_says_so_rather_than_hanging(companion):
    result = asyncio.run(companion.call("tabs.list"))
    assert result["ok"] is False
    assert "no browser is attached" in result["error"]


def test_a_read_only_session_refuses_to_change_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TOKEN_PATH", tmp_path / "token.txt")
    quiet = B.Companion(port=free_port(), token="t", read_only=True)
    for command in ("page.click", "page.type", "nav.open", "tabs.focus"):
        result = asyncio.run(quiet.call(command))
        assert result["ok"] is False
        assert "read-only" in result["error"], command


def test_a_read_only_session_still_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TOKEN_PATH", tmp_path / "token.txt")
    quiet = B.Companion(port=free_port(), token="t", read_only=True)
    result = asyncio.run(quiet.call("page.read"))
    # Refused for want of a browser, not for being read-only.
    assert "read-only" not in result["error"]


def test_every_mutating_command_is_a_real_command():
    assert B.MUTATING <= B.COMMANDS


def test_there_is_no_command_that_runs_arbitrary_script():
    """
    The bridge exposes named actions, not a scripting hole. `eval`, `exec` or
    a generic "run this in the page" would make every other check here
    decorative.
    """
    for command in B.COMMANDS:
        assert not any(word in command
                       for word in ("eval", "exec", "script", "inject"))


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------


def test_refused_connections_are_recorded(companion):
    async def go():
        await companion.start()
        try:
            try:
                socket = await connect(companion, origin="https://evil.example",
                                       token="test-token-abc")
                await asyncio.wait_for(socket.recv(), timeout=2)
            except Exception:
                pass
            return list(companion.audit)
        finally:
            await companion.stop()

    audit = asyncio.run(go())
    assert any(not entry.allowed and "origin" in entry.reason for entry in audit)


def test_refused_commands_are_recorded(companion):
    asyncio.run(companion.call("page.eval_javascript"))
    assert companion.audit[-1].allowed is False
    assert companion.audit[-1].command == "page.eval_javascript"


def test_the_audit_does_not_grow_without_bound(companion):
    for _ in range(700):
        companion._record("tabs.list", True)
    assert len(companion.audit) <= 500


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------


def test_a_token_is_created_once_and_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TOKEN_PATH", tmp_path / "token.txt")
    first = B.load_token()
    assert len(first) > 20
    assert B.load_token() == first


def test_a_missing_token_can_be_reported_rather_than_created(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TOKEN_PATH", tmp_path / "nothing.txt")
    assert B.load_token(create=False) == ""


def test_nothing_listens_until_it_is_needed(companion):
    """Idle Friday must not hold a socket open into his browser."""
    assert companion.running is False
    assert companion.attached is False


def test_stopping_leaves_nothing_listening(companion):
    async def go():
        await companion.start()
        assert companion.running
        await companion.stop()
        return companion.running

    assert asyncio.run(go()) is False


# ---------------------------------------------------------------------------
# The extension, checked as a file rather than assumed
# ---------------------------------------------------------------------------


def extension_dir():
    from pathlib import Path

    return Path(B.__file__).parent / "extension"


def test_the_extension_asks_for_no_host_permissions_up_front():
    """
    A browser extension that can read every site from the moment it is
    installed is not a companion, it is a keylogger with good manners.
    """
    import json as jsonlib

    manifest = jsonlib.loads((extension_dir() / "manifest.json")
                             .read_text(encoding="utf-8"))
    assert manifest["host_permissions"] == []
    assert manifest["optional_host_permissions"], \
        "unattended work needs origins the user grants explicitly"


def test_the_extension_declares_activetab_for_the_attended_mode():
    import json as jsonlib

    manifest = jsonlib.loads((extension_dir() / "manifest.json")
                             .read_text(encoding="utf-8"))
    assert "activeTab" in manifest["permissions"]


def test_the_extension_refuses_password_fields_and_browser_pages():
    source = (extension_dir() / "background.js").read_text(encoding="utf-8")
    assert 'match.type === "password"' in source
    assert 'chrome://' in source and 'edge://' in source


def test_the_extension_talks_only_to_loopback():
    source = (extension_dir() / "background.js").read_text(encoding="utf-8")
    assert 'ws://127.0.0.1' in source
    for elsewhere in ("http://", "https://"):
        assert f'"{elsewhere}' not in source, "the extension should call nothing out"


def test_the_options_page_builds_no_html_from_strings():
    """
    The screen that decides where Friday may act unattended is the wrong place
    to turn strings into markup.

    Comments are stripped first. The previous version of this test failed on
    the comment that says "not innerHTML" - the same grep-instead-of-parse
    mistake the credential test made. Twice is a habit; strip and then look.
    """
    import re

    source = (extension_dir() / "options.js").read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", source)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    assert "innerHTML" not in code
    assert "createElement" in source, "it should build nodes instead"


# ---------------------------------------------------------------------------
# Which extension, exactly
#
# "Origin starts with chrome-extension://" was not enough: EVERY extension has
# such an origin. A second extension the user installs for something unrelated
# would have satisfied it and been handed a channel into their logged-in
# browser.
# ---------------------------------------------------------------------------


def test_chromes_id_derivation_is_reproduced_exactly():
    """
    Pinned against a known vector. Reimplementing Chrome's rule by eye is easy
    to get subtly wrong, and a wrong id produces an allow-list nothing ever
    matches - which fails closed, but silently and confusingly.
    """
    from friday.companion import pairing

    # Worked out by hand rather than copied from the implementation, which
    # would only prove the code equals itself:
    #   sha256(b"")[:16] = e3 b0 c4 42 98 fc 1c 14 9a fb f4 c8 99 6f b9 24
    #   e->o 3->d | b->l 0->a | c->m 4->e | 4->e 2->c | ...
    #   = o d l a m e e c ...
    assert pairing.extension_id_from_key(b"") == "odlameecjipmbmbejkplpemijjgpljce"
    assert len(pairing.extension_id_from_key(b"anything")) == 32
    assert all("a" <= ch <= "p"
               for ch in pairing.extension_id_from_key(b"anything"))


def test_the_manifest_pins_a_key_so_the_id_is_stable():
    """
    Without `key`, an unpacked extension's id comes from its FOLDER PATH - it
    changes if the directory moves and differs on every machine, which is
    useless for an allow-list.
    """
    import json as jsonlib

    from friday.companion import pairing

    manifest = jsonlib.loads(pairing.MANIFEST.read_text(encoding="utf-8"))
    assert manifest.get("key"), "no pinned key: the id would not be stable"


def test_the_pinned_key_and_the_recorded_id_agree():
    from friday.companion import pairing

    origin = pairing.allowed_origin()
    assert origin.startswith("chrome-extension://")
    assert len(origin.split("//")[1]) == 32


def test_a_different_extension_is_refused(monkeypatch):
    """The whole point of pinning."""
    monkeypatch.setattr("friday.companion.pairing.allowed_origin",
                        lambda: "chrome-extension://" + "a" * 32)
    allowed, why = B.origin_allowed("chrome-extension://" + "b" * 32)
    assert not allowed
    assert "a different extension" in why
    # Names both, because the useful question when this fires is "did Chrome
    # give my extension the id I pinned?" and a bare refusal does not answer it.
    assert "b" * 32 in why and "a" * 32 in why


def test_the_friday_extension_is_accepted(monkeypatch):
    ours = "chrome-extension://" + "a" * 32
    monkeypatch.setattr("friday.companion.pairing.allowed_origin", lambda: ours)
    allowed, why = B.origin_allowed(ours)
    assert allowed and "Friday" in why


def test_an_unprovisioned_companion_accepts_nothing(monkeypatch):
    """A security check that is not configured must fail closed."""
    monkeypatch.setattr("friday.companion.pairing.allowed_origin", lambda: "")
    allowed, why = B.origin_allowed("chrome-extension://" + "a" * 32)
    assert not allowed
    assert "not provisioned" in why


def test_a_web_page_is_still_refused_before_the_id_is_even_considered(monkeypatch):
    monkeypatch.setattr("friday.companion.pairing.allowed_origin",
                        lambda: "chrome-extension://" + "a" * 32)
    allowed, why = B.origin_allowed("https://evil.example")
    assert not allowed and why == "not an extension origin"


def test_a_pinned_connection_from_the_wrong_extension_is_refused(companion,
                                                                 monkeypatch):
    """End to end on a real socket, not just the predicate."""
    monkeypatch.setattr("friday.companion.pairing.allowed_origin",
                        lambda: "chrome-extension://" + "a" * 32)

    async def go():
        await companion.start()
        try:
            try:
                socket = await connect(companion,
                                       origin="chrome-extension://" + "b" * 32,
                                       token="test-token-abc")
                await asyncio.wait_for(socket.recv(), timeout=3)
            except Exception:
                pass
            return companion.attached
        finally:
            await companion.stop()

    assert asyncio.run(go()) is False


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


def test_the_token_can_be_rotated(tmp_path, monkeypatch):
    """The only recovery from a leaked secret is a different secret."""
    monkeypatch.setattr(B, "TOKEN_PATH", tmp_path / "token.txt")
    first = B.load_token()
    second = B.rotate_token()
    assert second != first
    assert B.load_token() == second


def test_the_token_is_long_enough_to_not_be_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TOKEN_PATH", tmp_path / "token.txt")
    assert len(B.load_token()) >= 40


def test_the_token_value_is_never_logged(caplog, tmp_path, monkeypatch):
    """A secret in a log is a secret in a backup, a bundle and a screen share."""
    monkeypatch.setattr(B, "TOKEN_PATH", tmp_path / "token.txt")
    with caplog.at_level("DEBUG"):
        token = B.rotate_token()
    assert token not in caplog.text
    assert "token.txt" in caplog.text, "the path is useful; the value is not"


# ---------------------------------------------------------------------------
# Staying awake only while it matters
#
# An MV3 service worker is ephemeral, and setTimeout dies with it - so
# "reconnect on close" reconnects once and then, when Chrome eventually kills
# the worker, nothing brings it back. Friday cannot dial in either: a loopback
# server cannot open a socket into a sleeping extension. Unattended work would
# stop silently.
# ---------------------------------------------------------------------------


def test_the_session_commands_exist():
    assert "session.begin" in B.COMMANDS
    assert "session.end" in B.COMMANDS


def test_a_heartbeat_is_not_mistaken_for_a_reply(companion):
    """
    It keeps the worker alive and carries nothing. Resolving a pending command
    with it would hand a caller an empty answer for real work.
    """
    import json as jsonlib

    future = asyncio.get_event_loop_policy().new_event_loop().create_future()
    companion._pending["1"] = future
    companion._deliver(jsonlib.dumps({"heartbeat": True}))
    assert not future.done()
    assert "1" in companion._pending
    assert companion.last_heartbeat is not None


def test_a_real_reply_still_resolves(companion):
    import json as jsonlib

    async def go():
        future = asyncio.get_running_loop().create_future()
        companion._pending["7"] = future
        companion._deliver(jsonlib.dumps({"id": "7", "ok": True}))
        return await asyncio.wait_for(future, timeout=1)

    assert asyncio.run(go())["ok"] is True


def test_nothing_is_held_awake_before_a_run_starts(companion):
    assert companion.session_open is False


def test_the_extension_uses_an_alarm_rather_than_a_timer_to_come_back():
    """
    setTimeout does not survive worker termination. chrome.alarms does, and it
    is the only thing that can wake a sleeping worker to reconnect.
    """
    source = (extension_dir() / "background.js").read_text(encoding="utf-8")
    assert "chrome.alarms.create" in source
    assert "chrome.alarms.onAlarm" in source
    assert "setTimeout(connect" not in source,         "a timer cannot bring back a terminated worker"


def test_the_extension_heartbeats_inside_chromes_idle_window():
    source = (extension_dir() / "background.js").read_text(encoding="utf-8")
    import re

    match = re.search(r"HEARTBEAT_MS\s*=\s*(\d+)", source)
    assert match, "no heartbeat interval declared"
    assert int(match.group(1)) < 30000, "Chrome idles a worker out at 30s"


def test_the_extension_only_heartbeats_during_a_run():
    """
    Keeping a worker alive forever "just in case" is what Chrome asks
    extensions not to do, and it costs battery for nothing.
    """
    source = (extension_dir() / "background.js").read_text(encoding="utf-8")
    assert "stopHeartbeat" in source
    assert "session.end" in source


def test_the_manifest_declares_alarms_and_the_chrome_version_it_needs():
    import json as jsonlib

    manifest = jsonlib.loads((extension_dir() / "manifest.json")
                             .read_text(encoding="utf-8"))
    assert "alarms" in manifest["permissions"]
    # WebSocket activity keeping a worker alive is Chrome 116+.
    assert int(manifest["minimum_chrome_version"]) >= 116


def test_hello_reports_chromes_own_view_of_the_id():
    """
    So a mismatch with the pinned id is visible as data, rather than as a
    connection that silently never happens.
    """
    source = (extension_dir() / "background.js").read_text(encoding="utf-8")
    assert "chrome.runtime.id" in source


# ---------------------------------------------------------------------------
# The lease
#
# A plain boolean "task running" has a silent failure: Friday sends
# session.begin, dies before session.end, and the extension stays awake for
# the rest of the browser's life over a run that no longer exists.
# ---------------------------------------------------------------------------


def test_the_session_lease_commands_exist():
    for command in ("session.begin", "session.renew", "session.end",
                    "session.status"):
        assert command in B.COMMANDS


def test_a_lease_is_short_enough_that_a_dead_friday_is_forgotten():
    assert B.LEASE_MS <= 120000, "a crashed Friday should not hold the worker long"
    # And renewed comfortably inside it, so an ordinary hiccup does not drop it.
    assert B.RENEW_EVERY_SECONDS * 1000 * 2 < B.LEASE_MS


def test_beginning_a_session_carries_a_run_id_and_a_lease(companion, monkeypatch):
    sent = {}

    async def fake_call(name, **params):
        sent[name] = params
        return {"ok": True}

    monkeypatch.setattr(companion, "call", fake_call)
    asyncio.run(companion.begin_session("RUN-A"))
    assert sent["session.begin"]["run_id"] == "RUN-A"
    assert sent["session.begin"]["lease_ms"] == B.LEASE_MS
    assert companion.session_run == "RUN-A"


def test_the_lease_is_released_even_when_the_run_raises(companion, monkeypatch):
    """A lease left held is the exact failure this design exists to avoid."""
    calls = []

    async def fake_call(name, **params):
        calls.append(name)
        return {"ok": True}

    monkeypatch.setattr(companion, "call", fake_call)
    monkeypatch.setattr(B, "RENEW_EVERY_SECONDS", 0.01)

    def blows_up():
        raise RuntimeError("the run died")

    async def go():
        with pytest.raises(RuntimeError):
            await companion.hold("RUN-A", still_running=blows_up)

    asyncio.run(go())
    assert "session.end" in calls
    assert companion.session_open is False


def test_the_lease_is_released_when_the_run_finishes(companion, monkeypatch):
    calls = []
    ticks = {"n": 0}

    async def fake_call(name, **params):
        calls.append(name)
        return {"ok": True}

    def still_running():
        ticks["n"] += 1
        return ticks["n"] < 3

    monkeypatch.setattr(companion, "call", fake_call)
    monkeypatch.setattr(B, "RENEW_EVERY_SECONDS", 0.01)
    asyncio.run(companion.hold("RUN-A", still_running=still_running))

    assert calls[0] == "session.begin"
    assert calls[-1] == "session.end"
    assert "session.renew" in calls


def test_friday_asks_its_own_run_manager_rather_than_assuming(companion,
                                                              monkeypatch):
    """
    The browser is a participant in a run, never the record of whether one
    exists. Same rule as the Claude executor: the subordinate runtime does not
    own durable task truth.
    """
    asked = {"n": 0}

    async def fake_call(name, **params):
        return {"ok": True}

    def still_running():
        asked["n"] += 1
        return asked["n"] < 2

    monkeypatch.setattr(companion, "call", fake_call)
    monkeypatch.setattr(B, "RENEW_EVERY_SECONDS", 0.01)
    asyncio.run(companion.hold("RUN-A", still_running=still_running))
    assert asked["n"] >= 1, "the run manager was never consulted"


# ---------------------------------------------------------------------------
# The extension half of the lease, read from the source
# ---------------------------------------------------------------------------


def background_source() -> str:
    return (extension_dir() / "background.js").read_text(encoding="utf-8")


def test_the_lease_survives_worker_termination():
    """
    A lease in a global dies with the worker, and a lease that vanishes when
    the worker naps is not a lease. storage.session survives termination and
    clears on browser restart - which is right: after a restart the extension
    must assume nothing about running work.
    """
    source = background_source()
    assert "chrome.storage.session" in source
    assert "let taskActive" not in source, "the flag should be gone entirely"


def test_the_heartbeat_cannot_renew_the_lease():
    """
    If it could, the extension would certify its own task forever - the
    keepalive would become its own excuse for existing.
    """
    import re

    source = background_source()
    match = re.search(r"heartbeat = setInterval\((.*?)\n  \}, HEARTBEAT_MS\);",
                      source, re.S)
    assert match, "could not find the heartbeat body"
    body = match.group(1)
    assert "writeLease" not in body, "the heartbeat writes the lease"
    assert "expires_at:" not in body


def test_the_heartbeat_carries_no_run_id():
    import re

    source = background_source()
    match = re.search(r'socket\.send\(JSON\.stringify\(\{ heartbeat: true \}\)\)',
                      source)
    assert match, "the heartbeat should carry nothing but the flag"


def test_an_expired_lease_stops_the_keepalive_without_being_told():
    source = background_source()
    assert "liveLease" in source
    assert "expires_at" in source


def test_a_stale_run_cannot_renew_over_the_one_that_replaced_it():
    source = background_source()
    assert "that run is not the active one" in source


def test_the_wake_alarm_is_recreated_rather_than_assumed():
    """
    persistAcrossSessions is Chrome 150+; this browser is older, so an alarm
    cannot be assumed to survive a browser restart.
    """
    source = background_source()
    assert "ensureWakeAlarm" in source
    assert "chrome.alarms.get" in source
    assert source.count("ensureWakeAlarm()") >= 2, \
        "it should run on startup AND when an alarm fires"


def test_a_terminated_worker_resumes_the_keepalive_when_woken():
    """Woken mid-run with a live lease, it must go back to holding the worker."""
    import re

    source = background_source()
    match = re.search(r"chrome\.alarms\.onAlarm\.addListener\((.*?)\n\}\);",
                      source, re.S)
    assert match
    assert "liveLease" in match.group(1)
    assert "startHeartbeat" in match.group(1)


# ---------------------------------------------------------------------------
# The trace
#
# Diagnostics, not a feature. "Test 14 failed" is not a finding; a sequence you
# can read is.
# ---------------------------------------------------------------------------


def test_the_trace_identifies_which_worker_instance_wrote_it():
    """
    The field that does most of the work. A NEW id in the log is direct
    evidence Chrome terminated the worker and started another - without it,
    worker death has to be inferred from silence.
    """
    source = background_source()
    assert "workerInstance" in source
    assert 'trace("worker.started"' in source


# The property "the trace never records a secret" is asserted by
# test_no_declared_field_is_a_secret_or_page_content, against the TRACE_FIELDS
# schema.
#
# There was a regex version here that scanned trace() call sites. It matched
# across function boundaries and failed on the string "password fields are
# yours to fill" in an unrelated handler - the fourth source-matching mistake
# in this file. The schema check is both stronger and exact: it reads the list
# of fields the emitter will actually copy, so it cannot be fooled by where a
# word happens to appear.


def test_the_token_send_is_marked_as_never_traced():
    source = background_source()
    assert "the value is never traced" in source


def test_the_trace_is_bounded():
    source = background_source()
    assert "TRACE_LIMIT" in source
    assert "slice(-TRACE_LIMIT)" in source


def test_the_lifecycle_moments_are_all_traced():
    source = background_source()
    for moment in ("ws.open", "ws.closed", "ws.paired", "ws.unreachable",
                   "lease.begin", "lease.renew", "lease.end", "lease.expired",
                   "heartbeat", "alarm.fired", "permission.check"):
        assert f'"{moment}"' in source, f"{moment} is not traced"


def test_the_permission_answer_comes_from_chrome_every_time():
    """
    A cached answer would go on saying yes after the user revoked the origin,
    which is the one thing a permission check must never do.

    Asserted structurally rather than on the comment that says so. The first
    version of this test searched for a mixed-case phrase inside a lowercased
    source and could never have matched - the third source-matching mistake in
    this file, and the reason the rule is now: check the behaviour, not the
    prose about the behaviour.
    """
    import re

    source = background_source()
    # The call must live inside mayTouch, which runs per command - not at
    # module scope, where its answer would be computed once and reused.
    body = source.split("async function mayTouch", 1)
    assert len(body) == 2, "mayTouch not found"
    before, after = body
    inside = after.split("\nasync function", 1)[0]

    assert "chrome.permissions.contains" in inside
    assert "chrome.permissions.contains" not in before, \
        "the permission answer is computed once and cached"


def test_dumping_the_trace_is_a_declared_command():
    assert "trace.dump" in B.COMMANDS
    assert "trace.dump" not in B.MUTATING, "reading diagnostics changes nothing"


def test_the_dump_reports_what_the_live_tests_need():
    source = background_source()
    for field in ("worker", "extension_id", "socket_open", "heartbeat_running",
                  "lease", "alarm_exists", "granted_origins"):
        assert f"{field}:" in source, f"the dump omits {field}"


# ---------------------------------------------------------------------------
# The trace is allow-listed at emit time, not merely inspected
# ---------------------------------------------------------------------------


def test_every_traced_event_has_a_declared_field_list():
    """
    Static inspection proves nobody WROTE a secret into a trace call. It cannot
    prove a variable named `reason` will never hold one at runtime. So the
    emitter drops anything not named for that event.
    """
    import re

    source = background_source()
    schema = re.search(r"const TRACE_FIELDS = \{(.*?)\n\};", source, re.S)
    assert schema, "no TRACE_FIELDS schema"
    declared = set(re.findall(r'"([\w.]+)":', schema.group(1)))

    emitted = set(re.findall(r'trace\(\s*"([\w.]+)"', source))
    missing = emitted - declared
    assert not missing, f"traced without a declared field list: {missing}"


def test_the_emitter_copies_only_declared_fields():
    import re

    source = background_source()
    body = re.search(r"async function trace\(event, detail\) \{(.*?)\n\}",
                     source, re.S)
    assert body, "trace() not found"
    # It must iterate the allow-list, never spread the caller's object.
    assert "for (const field of allowed)" in body.group(1)
    assert "Object.assign" not in body.group(1), \
        "assigning the caller's object copies whatever it holds"
    assert "...detail" not in body.group(1)


def test_an_unknown_event_carries_nothing():
    """
    Silence would hide a bug, so it still gets a line - but nothing has been
    vetted for it, so it carries no fields.
    """
    import re

    source = background_source()
    body = re.search(r"async function trace\(event, detail\) \{(.*?)\n\}",
                     source, re.S)
    assert 'TRACE_FIELDS[event] ? event : "unknown"' in body.group(1)


def test_no_declared_field_is_a_secret_or_page_content():
    import re

    source = background_source()
    schema = re.search(r"const TRACE_FIELDS = \{(.*?)\n\};", source, re.S)
    fields = set(re.findall(r'"([\w.]+)"', schema.group(1)))
    for forbidden in ("token", "secret", "password", "cookie", "text", "value",
                      "title", "html", "reason"):
        assert forbidden not in fields, f"the schema allows {forbidden!r}"


def test_a_close_reason_is_classified_rather_than_copied():
    """
    The close reason is free text from the other end - the useful part is which
    refusal it was, and a raw string is somewhere unvetted data can ride along.
    """
    source = background_source()
    assert "reason_class" in source
    assert "origin-refused" in source and "token-refused" in source
    assert "reason: event" not in source, "the raw reason is still copied"
