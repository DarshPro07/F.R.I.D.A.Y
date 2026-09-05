"""
Shared test fixtures.

The event-loop fixture exists because of a real, confusing failure:
test_agent_session_accepts_turn_handling_without_deprecated_args passed when
run alone and failed when run after the capability-router tests, with

    RuntimeError: There is no current event loop in thread 'MainThread'

`asyncio.run()` creates a loop, runs, then closes it and leaves the policy
with `_set_called = True` and no loop. Anything that later calls
`get_event_loop()` - AgentSession construction does - then raises. The tests
were fine; the loop they inherited was not.

Rather than banning asyncio.run in tests, every test gets a fresh loop.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

#: Test modules that exercise Windows-native surfaces (COM audio via
#: pycaw/comtypes, Win32 window enumeration, power/session APIs). They
#: cannot even be collected elsewhere - `ctypes.WINFUNCTYPE` does not exist
#: on Linux and pygetwindow raises NotImplementedError at import - which
#: took the whole ubuntu CI gate down with "14 errors during collection"
#: (first remote run of verify.yml, 2026-09-05). Windows is the product's
#: platform and runs them; other platforms skip exactly these, by name, so
#: a new Windows-only module fails loudly there instead of being hidden.
WINDOWS_ONLY_MODULES = (
    "test_audio.py", "test_execution_bridge.py", "test_file_control.py",
    "test_live_pass_fixes.py", "test_objective_mcp.py", "test_product_mcp.py",
    "test_run_control.py", "test_transport_parity.py", "test_windows.py",
    "test_processes.py", "test_power.py", "test_platform_power.py",
)

collect_ignore = list(WINDOWS_ONLY_MODULES) if sys.platform != "win32" else []


@pytest.hookimpl(tryfirst=True)
def pytest_asyncio_loop_factories(config, item):
    """Reuse the conftest loop for pytest-asyncio's Runner.

    pytest-asyncio 1.4 creates its own loop per test (Runner with
    loop_factory=None -> asyncio.new_event_loop()). Sync fixtures (the
    executor) run on the conftest loop instead, so background tasks
    created during fixture setup - the continuous driver - land on a
    loop that never runs. This public hook hands the Runner a factory
    that returns the conftest-installed loop, so the test body runs on
    the same loop the fixtures saw. Uses the public hook API, not the
    private _asyncio_loop_factory fixture.
    """

    def _factory() -> asyncio.AbstractEventLoop:
        return asyncio.get_event_loop_policy().get_event_loop()

    return {"conftest": _factory}


@pytest.fixture(autouse=True)
def fresh_event_loop():
    """Give each test a usable loop, and leave one behind for the next."""
    previous = None
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        try:
            if not loop.is_closed():
                loop.close()
        except Exception:
            pass
        # Always leave a live loop installed; a closed-or-absent loop is what
        # broke the next test in the first place.
        replacement = (previous if previous is not None and not previous.is_closed()
                       else asyncio.new_event_loop())
        asyncio.set_event_loop(replacement)


@pytest.fixture(autouse=True)
def _reflex_off_unless_asked(monkeypatch):
    """
    The suite runs with the local router off, whatever `.env` says.

    Shadow mode is enabled in `.env` so real turns get observed. `dotenv`
    loads that during collection, and the suite then ran with a live reflex
    path: seven tests failed that pass alone, `test_turning_it_on` could not
    turn it on because the mode variable outranked the old flag, and every
    turn-shaped test started writing telemetry into whatever store it had.

    A test that behaves differently because of a developer's `.env` is not a
    test. Anything that wants a mode sets it itself - `test_shadow.py` and
    `test_reflex.py` both do.
    """
    monkeypatch.delenv("FRIDAY_REFLEX_MODE", raising=False)
    monkeypatch.delenv("FRIDAY_REFLEX", raising=False)
    monkeypatch.delenv("FRIDAY_SHADOW", raising=False)
    monkeypatch.delenv("FRIDAY_PRIVACY_MODE", raising=False)
    yield


@pytest.fixture(autouse=True)
def _never_the_real_database(tmp_path, monkeypatch):
    """
    A test may not write to the boss's actual store.

    This was theoretical until the turn path started recording projects. A
    test whose text happens to say "I want to build X" now creates a project
    in `data/ada.sqlite3`, and the only reason the suite has not already done
    so is that no test sentence happens to match - which is luck, not a
    guarantee, and it fails silently by leaving junk behind rather than by
    going red.

    The cached module-level store is cleared too, because it is built once
    and would otherwise hold a handle to the real file from whichever test
    touched it first.
    """
    import friday.toolsets.memory as memory

    monkeypatch.setenv("ADA_DB", str(tmp_path / "test-store.sqlite3"))
    monkeypatch.setattr(memory, "_store", None, raising=False)
    yield
    monkeypatch.setattr(memory, "_store", None, raising=False)


@pytest.fixture(autouse=True)
def _governor_sees_a_healthy_machine():
    """
    The resource governor gates every worker dispatch on the LIVE machine.

    That is the product behaviour (FR-056), and it made the suite depend
    on the host: with the developer's RAM at 88% every Hermes/Claude
    dispatch test was refused with "HIGH pressure: concurrency reduced to
    1". A test that wants pressure injects its own sampler
    (tests/test_governor.py); everyone else gets a fresh governor reading
    a healthy synthetic sample, so admissions and leases are per-test and
    deterministic.
    """
    import time
    from friday import governor as G

    healthy = G.Sample(at=time.time(), cpu_percent=5.0, ram_percent=30.0,
                       ram_available_gb=16.0, disk_free_gb=100.0,
                       browser_processes=0, friday_rss_mb=100.0)
    G.configure(G.Governor(sampler=lambda: healthy, sample_ttl_s=60.0))
    yield
    G.configure(None)


@pytest.fixture(autouse=True)
def _audit_log_is_per_test(tmp_path):
    """
    `PolicyEngine.decide` writes every R1+/non-AUTO verdict to the audit
    log (FR-065). Left alone, the suite would append thousands of rows to
    the boss's live `data/audit.sqlite3` and contend on its lock with the
    running agent. Each test gets its own log; a test that wants to read
    the audit uses `friday.trust.audit()` and sees only its own rows.
    """
    from friday import trust
    log = trust.AuditLog(tmp_path / "audit.sqlite3")
    trust.configure_audit(log)
    yield
    trust.configure_audit(None)
    log.close()
