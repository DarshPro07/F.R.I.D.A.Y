"""
System toolset (Phase 1A): machine state, applications, volume, clipboard.

Everything here runs on the user's actual computer, so every result carries
``execution_scope = "local_machine"`` (§9). When the agent later runs in a
container this module is what the Edge Controller hosts; the scope constant is
what stops container facts being reported as the user's machine.

The important function is `apps_open`. Mark-L's equivalent returns
"Opened Spotify." whenever `subprocess.Popen` did not raise - and its final
fallback types into the Start Menu and returns True unconditionally. Here a
launch is only ever `succeeded` when a matching process is observed, and the
observation (name + pid) is the Verification evidence. If something started
but does not match, the result is PARTIAL and the agent must not claim it
opened.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import psutil

from friday import apps
from friday import contracts as c
from friday.policy import PolicyEngine, default_engine


#: These results describe the user's real machine, not an agent container.
EXECUTION_SCOPE = "local_machine"

LAUNCH_TIMEOUT = 10.0
LAUNCH_POLL = 0.25

#: Keep helper subprocesses from flashing a console window.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

APPROVAL_PREFIX = "APPROVAL_REQUIRED"


def needs_approval(result: c.ActionResult) -> bool:
    return result.status == c.CANCELLED and (result.error or "").startswith(APPROVAL_PREFIX)


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    """Return a CANCELLED result if policy blocks the call, else None."""
    # Where the objective came from, before what it asks for. A run whose
    # objective was lifted out of a page cannot reach anything that stops a
    # program, and no answer would change that - so it is refused rather than
    # asked about.
    from friday import policy as policy_module

    refusal = policy_module.provenance_verdict(tool_id, run.provenance)
    if refusal is not None:
        return run.record(c.started(run.run_id, tool_id).finish(
            status=c.FAILED, error=f"BLOCKED: {refusal.reason}"))

    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    started = c.started(run.run_id, tool_id)
    return run.record(started.finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


# ---------------------------------------------------------------------------
# Machine state
# ---------------------------------------------------------------------------


def system_get_info(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    tool_id = "system.get_info"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    boot = psutil.boot_time()
    info = _scoped({
        "describes": "the user's own computer",
        "hostname": platform.node(),
        "os": platform.system(),
        "os_version": platform.version(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "cpu_logical_cores": psutil.cpu_count(logical=True),
        "total_memory_gb": round(psutil.virtual_memory().total / 1024**3, 1),
        "python_version": platform.python_version(),
        "boot_time_epoch": boot,
        "uptime_hours": round((time.time() - boot) / 3600, 1),
    })
    return run.record(c.succeeded(
        started, output=info,
        verification=c.Verification(
            method="psutil_query",
            evidence=f"{info['hostname']} / {info['os']} {info['release']}, "
                     f"{info['cpu_logical_cores']} cores, {info['total_memory_gb']} GB",
        ),
    ))


def system_list_processes(
    run: c.Run, *, top: int = 10, sort_by: str = "memory",
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Real process table. sort_by: 'memory' | 'cpu' | 'name'."""
    tool_id = "system.list_processes"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if sort_by not in ("memory", "cpu", "name"):
        return run.record(c.failed(started, f"unknown sort_by {sort_by!r}"))

    rows = []
    for proc in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
        try:
            mem = proc.info["memory_info"]
            rows.append({
                "pid": proc.info["pid"],
                "name": proc.info["name"] or "?",
                "memory_mb": round((mem.rss if mem else 0) / 1024**2, 1),
                "cpu_percent": proc.info["cpu_percent"] or 0.0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    key = {"memory": lambda r: -r["memory_mb"],
           "cpu": lambda r: -r["cpu_percent"],
           "name": lambda r: r["name"].lower()}[sort_by]
    rows.sort(key=key)
    top_rows = rows[:top]

    if not top_rows:
        return run.record(c.failed(started, "no processes readable"))

    return run.record(c.succeeded(
        started,
        output=_scoped({"sorted_by": sort_by, "total_processes": len(rows),
                        "processes": top_rows}),
        verification=c.Verification(
            method="psutil_process_iter",
            evidence=f"{len(rows)} processes read; top by {sort_by} is "
                     f"{top_rows[0]['name']} ({top_rows[0]['memory_mb']} MB, "
                     f"pid {top_rows[0]['pid']})",
        ),
    ))


def system_resource_usage(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    tool_id = "system.resource_usage"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    mem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.3)
    try:
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        disk_info = {"disk_percent": disk.percent,
                     "disk_free_gb": round(disk.free / 1024**3, 1)}
    except OSError:
        disk_info = {}

    usage = _scoped({
        "cpu_percent": cpu,
        "memory_percent": mem.percent,
        "memory_used_gb": round(mem.used / 1024**3, 1),
        "memory_total_gb": round(mem.total / 1024**3, 1),
        **disk_info,
    })
    return run.record(c.succeeded(
        started, output=usage,
        verification=c.Verification(
            method="psutil_sample",
            evidence=f"cpu {cpu}%, memory {mem.percent}% "
                     f"({usage['memory_used_gb']}/{usage['memory_total_gb']} GB)",
        ),
    ))


def system_wifi_status(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Windows: parse `netsh wlan show interfaces`. Native, no dependency."""
    tool_id = "system.wifi_status"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if sys.platform != "win32":
        return run.record(c.failed(started, f"wifi status not implemented for {sys.platform}"))

    try:
        proc = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return run.record(c.failed(started, f"netsh failed: {exc}"))

    if proc.returncode != 0:
        return run.record(c.failed(
            started, f"netsh exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
        ))

    fields: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            if key in ("name", "state", "ssid", "signal", "radio type", "receive rate (mbps)"):
                fields[key] = value.strip()

    if not fields:
        return run.record(c.partial(
            started, "netsh returned no interface fields (no wireless adapter?)",
            output=_scoped({"raw_lines": len((proc.stdout or '').splitlines())}),
        ))

    return run.record(c.succeeded(
        started, output=_scoped(fields),
        verification=c.Verification(
            method="netsh_wlan_show_interfaces",
            evidence=f"state={fields.get('state', '?')} ssid={fields.get('ssid', '-')}",
        ),
    ))


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def _snapshot() -> dict[int, str]:
    out: dict[int, str] = {}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            out[proc.info["pid"]] = (proc.info["name"] or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _matching(snapshot: dict[int, str], expected: tuple[str, ...]) -> list[tuple[int, str]]:
    wanted = {e.lower() for e in expected}
    stems = {Path(e).stem.lower() for e in expected}
    hits = []
    for pid, name in snapshot.items():
        if name in wanted or Path(name).stem.lower() in stems:
            hits.append((pid, name))
    return hits


def _launch(target: apps.AppTarget) -> None:
    """Start the app. Raises OSError if the launch itself fails."""
    command = target.command
    if command.lower().endswith(".lnk"):
        os.startfile(command)  # noqa: S606 - Windows shortcut activation
        return
    subprocess.Popen(
        [command],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def apps_open(
    run: c.Run, name: str, *, engine: PolicyEngine = default_engine,
    timeout: float = LAUNCH_TIMEOUT,
) -> c.ActionResult:
    """
    Open an application and PROVE it opened.

    Success requires observing a process that matches the resolved app. A
    launch command that returned without error is not evidence.
    """
    tool_id = "apps.open"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    target = apps.resolve(name)
    if target is None:
        return run.record(c.failed(
            started,
            f"could not find an application matching {name!r} on this machine "
            f"(checked App Paths registry, PATH, and Start Menu)",
        ))

    before = _snapshot()
    already = _matching(before, target.expected_processes)
    if already:
        pid, proc_name = already[0]
        return run.record(c.succeeded(
            started,
            output=_scoped({"app": target.display_name, "already_running": True,
                            "pid": pid, "process": proc_name,
                            "resolved_via": target.source}),
            verification=c.Verification(
                method="process_already_running",
                evidence=f"{proc_name} pid={pid} was already running",
            ),
        ))

    try:
        _launch(target)
    except OSError as exc:
        return run.record(c.failed(started, f"failed to launch {target.command!r}: {exc}"))

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(LAUNCH_POLL)
        now = _snapshot()
        hits = [(pid, n) for pid, n in _matching(now, target.expected_processes)
                if pid not in before]
        if hits:
            pid, proc_name = hits[0]
            return run.record(c.succeeded(
                started,
                output=_scoped({"app": target.display_name, "pid": pid,
                                "process": proc_name, "resolved_via": target.source,
                                "command": target.command}),
                side_effects=(f"started process {proc_name} (pid {pid})",),
                verification=c.Verification(
                    method="process_started",
                    evidence=f"{proc_name} pid={pid} appeared within "
                             f"{round(timeout - (deadline - time.monotonic()), 1)}s of launch",
                ),
            ))

    # Something may have started, but nothing we can attribute to this app.
    after = _snapshot()
    new = [n for pid, n in after.items() if pid not in before]
    return run.record(c.partial(
        started,
        f"launched {target.command!r} but no process matching "
        f"{list(target.expected_processes)} appeared within {timeout}s - "
        f"cannot confirm {target.display_name} opened",
        output=_scoped({"app": target.display_name, "resolved_via": target.source,
                        "new_processes_observed": sorted(set(new))[:10]}),
    ))


def apps_close(
    run: c.Run, name: str, *, engine: PolicyEngine = default_engine,
    timeout: float = 8.0,
) -> c.ActionResult:
    """Close an application. ASK-gated - closing can lose unsaved work."""
    tool_id = "apps.close"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    target = apps.resolve(name)
    if target is None:
        return run.record(c.failed(started, f"could not resolve {name!r}"))

    running = _matching(_snapshot(), target.expected_processes)
    if not running:
        return run.record(c.failed(
            started, f"{target.display_name} is not running - nothing to close"
        ))

    # WM_CLOSE to the windows those processes own, and nothing else.
    #
    # This used to call `psutil.Process.terminate()` in a loop, which on
    # Windows is an alias for kill() - TerminateProcess. So "close Chrome"
    # force-killed every matching process, discarded whatever was unsaved, and
    # reported SUCCEEDED with evidence reading "process_absent_after_terminate".
    # The evidence was even true. What was false was the word the person used:
    # they asked for it to be *closed*.
    #
    # Under the default autonomy setting nobody was asked first, because the
    # category was ASK and FULL turns ASK into a yes. Ending an application is
    # not a question an autonomy setting gets to answer, which is why it now
    # lives behind CONFIRM in a category of its own, and why this function no
    # longer ends anything at all.
    # Imported here rather than at module scope: `processes` imports
    # APPROVAL_PREFIX from this module, and the two would deadlock at import.
    from friday.toolsets import processes as P

    windows: list[dict] = []
    for pid, _name in running:
        windows.extend(P.windows_of(pid))
    asked = P.ask_windows_to_close(windows)

    if not windows:
        return run.record(c.partial(
            started,
            f"{target.display_name} is running but has no window to close - "
            f"ending it means terminating it, which needs saying so "
            f"explicitly.",
            output=_scoped({"app": target.display_name, "closed": False,
                            "windows": 0, "running": running,
                            "force_would_need_confirmation": True})))

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _matching(_snapshot(), target.expected_processes):
            return run.record(c.succeeded(
                started,
                output=_scoped({"app": target.display_name, "closed": True,
                                "windows_asked": asked, "method": "WM_CLOSE"}),
                side_effects=(f"closed {target.display_name}",),
                verification=c.Verification(
                    method="process_absent_after_wm_close",
                    evidence=f"{target.display_name} received WM_CLOSE on "
                             f"{asked} window(s) and no "
                             f"{target.expected_processes[0]} remains"),
            ))
        time.sleep(0.2)

    still = _matching(_snapshot(), target.expected_processes)
    return run.record(c.partial(
        started,
        f"{target.display_name} was asked to close and {len(still)} "
        f"process(es) are still running - it may be asking whether to save. "
        f"Forcing it would discard whatever is unsaved, and needs saying so "
        f"explicitly.",
        output=_scoped({"app": target.display_name, "closed": False,
                        "windows_asked": asked, "remaining": still,
                        "force_would_need_confirmation": True})))


def apps_focus(
    run: c.Run, name: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Bring an existing window to the foreground."""
    tool_id = "apps.focus"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        import pygetwindow
    except ImportError as exc:
        return run.record(c.failed(started, f"pygetwindow unavailable: {exc}"))

    needle = (name or "").strip().lower()
    if not needle:
        return run.record(c.failed(started, "no window name given"))

    try:
        windows = [w for w in pygetwindow.getAllWindows() if w.title]
    except Exception as exc:  # pygetwindow raises bare Exception on some shells
        return run.record(c.failed(started, f"could not enumerate windows: {exc}"))

    matches = [w for w in windows if needle in w.title.lower()]
    if not matches:
        return run.record(c.failed(
            started,
            f"no open window matching {name!r} "
            f"({len(windows)} windows checked)",
        ))

    window = matches[0]
    try:
        if window.isMinimized:
            window.restore()
        window.activate()
    except Exception as exc:
        return run.record(c.partial(
            started, f"found window {window.title!r} but could not activate it: {exc}",
            output=_scoped({"window": window.title}),
        ))

    time.sleep(0.3)
    try:
        active = pygetwindow.getActiveWindow()
        active_title = active.title if active else ""
    except Exception:
        active_title = ""

    if active_title and active_title == window.title:
        return run.record(c.succeeded(
            started,
            output=_scoped({"window": window.title}),
            verification=c.Verification(
                method="foreground_window_matches",
                evidence=f"active window is now {active_title!r}",
            ),
        ))

    return run.record(c.partial(
        started,
        f"activate() called on {window.title!r} but the foreground window is "
        f"{active_title!r} - cannot confirm focus",
        output=_scoped({"window": window.title, "active": active_title}),
    ))


def apps_list_known(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    tool_id = "apps.list_known"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    entries = apps.known_apps()
    if not entries:
        return run.record(c.partial(started, "no applications discovered"))
    sources = {}
    for entry in entries:
        sources[entry["source"]] = sources.get(entry["source"], 0) + 1
    return run.record(c.succeeded(
        started, output=_scoped({"count": len(entries), "by_source": sources,
                                 "apps": entries}),
        verification=c.Verification(
            method="registry_and_start_menu_scan",
            evidence=f"{len(entries)} entries discovered ({sources})",
        ),
    ))


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


class NoAudioEndpoint(RuntimeError):
    """The machine has no default audio render device.

    `IMMDeviceEnumerator::GetDefaultAudioEndpoint` fails (E_NOTFOUND,
    0x80070490) on a machine with no sound output at all - a headless
    build agent, a server, a VM with audio removed. That is not the device
    refusing a request; there is no device. The tools report it as
    UNSUPPORTED, the same word the power toolset uses for "this machine
    cannot sleep", rather than FAILED, which would read as something broke.
    """


def _endpoint_volume():
    """
    Windows Core Audio endpoint. COM must be initialised per calling thread,
    and the LiveKit job runner calls tools off the main thread.

    Finding the device and talking to it are kept apart on purpose: a COM
    failure while *finding* it means there is nothing to talk to
    (`NoAudioEndpoint`); a failure *talking* to it propagates as the fault
    it is. The two were one bare `except` before, and a runner with no
    sound card reported the volume read as "failed" (2026-09-05).
    """
    import comtypes
    from pycaw.utils import AudioUtilities

    comtypes.CoInitialize()
    try:
        speakers = AudioUtilities.GetSpeakers()
    except comtypes.COMError as exc:
        code = (exc.hresult or 0) & 0xFFFFFFFF
        raise NoAudioEndpoint(
            f"no default audio output device on this machine (HRESULT {code:#010x})") from exc
    return speakers.EndpointVolume


def _no_audio(run: c.Run, started: c.ActionResult, exc: NoAudioEndpoint) -> c.ActionResult:
    return run.record(started.finish(status=c.UNSUPPORTED, error=str(exc)))


def volume_get(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    tool_id = "volume.get"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if sys.platform != "win32":
        return run.record(c.failed(started, f"volume not implemented for {sys.platform}"))
    try:
        endpoint = _endpoint_volume()
        level = round(endpoint.GetMasterVolumeLevelScalar() * 100)
        muted = bool(endpoint.GetMute())
    except NoAudioEndpoint as exc:
        return _no_audio(run, started, exc)
    except Exception as exc:
        return run.record(c.failed(started, f"could not read volume: {exc}"))

    return run.record(c.succeeded(
        started, output=_scoped({"volume_percent": level, "muted": muted}),
        verification=c.Verification(
            method="core_audio_read",
            evidence=f"master volume {level}%, muted={muted}",
        ),
    ))


def volume_set(
    run: c.Run, level: int, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Set master volume 0-100 and read it back to confirm."""
    tool_id = "volume.set"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not isinstance(level, int) or not 0 <= level <= 100:
        return run.record(c.failed(started, f"level must be an int 0-100, got {level!r}"))
    if sys.platform != "win32":
        return run.record(c.failed(started, f"volume not implemented for {sys.platform}"))

    try:
        endpoint = _endpoint_volume()
        before = round(endpoint.GetMasterVolumeLevelScalar() * 100)
        endpoint.SetMasterVolumeLevelScalar(level / 100.0, None)
        after = round(endpoint.GetMasterVolumeLevelScalar() * 100)
    except NoAudioEndpoint as exc:
        return _no_audio(run, started, exc)
    except Exception as exc:
        return run.record(c.failed(started, f"could not set volume: {exc}"))

    if abs(after - level) > 1:
        return run.record(c.partial(
            started, f"set {level}% but device reports {after}%",
            output=_scoped({"requested": level, "actual": after}),
        ))

    return run.record(c.succeeded(
        started,
        output=_scoped({"volume_percent": after, "previous_percent": before}),
        side_effects=(f"master volume {before}% -> {after}%",),
        verification=c.Verification(
            method="core_audio_readback",
            evidence=f"volume read back as {after}% after setting {level}%",
        ),
    ))


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------


#: The Windows clipboard is a single lockable resource shared by every running
#: application. If a browser or editor holds it for a few milliseconds, the
#: call fails - sometimes with the memorable "[WinError 0] The operation
#: completed successfully". That is contention, not breakage, so it is
#: retried. Caught by running the clipboard checks twice.
CLIPBOARD_ATTEMPTS = 4
CLIPBOARD_BACKOFF = 0.12


def _with_clipboard_retry(operation, attempts: int = CLIPBOARD_ATTEMPTS):
    """Run a clipboard operation, retrying transient lock contention."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:  # pyperclip raises bare Exception subclasses
            last = exc
            if attempt < attempts - 1:
                time.sleep(CLIPBOARD_BACKOFF * (attempt + 1))
    raise last if last else RuntimeError("clipboard operation failed")


def _powershell_clipboard_read() -> str:
    """
    Independent fallback path.

    Retrying pyperclip does not help when another process holds the clipboard
    open indefinitely - OpenClipboard then fails every time with the splendid
    "[WinError 0] The operation completed successfully". PowerShell's
    Get-Clipboard goes through .NET on its own STA thread with its own
    retry, and succeeds where the direct Win32 call does not.
    """
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
         "Get-Clipboard -Raw"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=20, **_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "Get-Clipboard failed").strip()[:200])
    # Get-Clipboard -Raw appends a trailing newline that was not in the data.
    return (result.stdout or "").rstrip("\r\n")


def _powershell_clipboard_write(text: str) -> None:
    """Write via PowerShell, passing the text on stdin so nothing is quoted."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "$input | Set-Clipboard"],
        input=text, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=20, **_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "Set-Clipboard failed").strip()[:200])


def read_clipboard() -> str:
    """pyperclip first, PowerShell if the Win32 clipboard is held by someone else."""
    import pyperclip

    try:
        return _with_clipboard_retry(pyperclip.paste)
    except Exception:
        if sys.platform != "win32":
            raise
        return _powershell_clipboard_read()


def write_clipboard(text: str) -> str:
    """Write and read back, falling back to PowerShell. Returns the read-back."""
    import pyperclip

    def direct():
        pyperclip.copy(text)
        return pyperclip.paste()

    try:
        return _with_clipboard_retry(direct)
    except Exception:
        if sys.platform != "win32":
            raise
        _powershell_clipboard_write(text)
        return _powershell_clipboard_read()


def clipboard_read(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    tool_id = "clipboard.read"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        text = read_clipboard()
    except Exception as exc:
        return run.record(c.failed(
            started, f"could not read clipboard: {exc}"))

    if text is None:
        return run.record(c.partial(started, "clipboard returned nothing readable"))

    return run.record(c.succeeded(
        started,
        output=_scoped({"text": text, "length": len(text), "empty": not text}),
        verification=c.Verification(
            method="clipboard_read",
            evidence=f"read {len(text)} character(s) from the clipboard",
        ),
    ))


def clipboard_write(
    run: c.Run, text: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Write to the clipboard and read it back to confirm. ASK-gated."""
    tool_id = "clipboard.write"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not isinstance(text, str):
        return run.record(c.failed(started, f"text must be a string, got {type(text).__name__}"))

    try:
        readback = write_clipboard(text)
    except Exception as exc:
        return run.record(c.failed(
            started, f"could not write clipboard: {exc}"))

    if readback != text:
        return run.record(c.partial(
            started,
            "clipboard write did not read back identically",
            output=_scoped({"written_length": len(text), "readback_length": len(readback or "")}),
        ))

    return run.record(c.succeeded(
        started,
        output=_scoped({"length": len(text)}),
        side_effects=("clipboard contents replaced",),
        verification=c.Verification(
            method="clipboard_readback",
            evidence=f"{len(text)} character(s) written and read back identically",
        ),
    ))


# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
AGENT_RUNTIME_SCOPE = 'agent_runtime'


def get_current_time(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """The agent runtime's date and time, ISO 8601, with its timezone."""
    import datetime

    tool_id = "get_current_time"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    now = datetime.datetime.now().astimezone()
    return run.record(c.succeeded(
        started,
        output={
            "execution_scope": AGENT_RUNTIME_SCOPE,
            "iso8601": now.isoformat(),
            "timezone": str(now.tzinfo),
        },
        verification=c.Verification(
            method="clock_read",
            evidence=f"system clock read as {now.isoformat()} ("
                     f"{now.tzinfo}) on the agent runtime",
        ),
    ))


def get_system_info(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """
    What the agent is running on. Not the boss's PC when deployed.

    `system_get_info` is the one that describes their machine. Two
    capabilities that sound alike and answer different questions, which is
    exactly why each result names its own scope.
    """
    import platform

    tool_id = "get_system_info"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    observed = {
        "os": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }
    return run.record(c.succeeded(
        started,
        output={
            "execution_scope": AGENT_RUNTIME_SCOPE,
            "describes": "the machine running the agent, not the user's computer",
            **observed,
        },
        verification=c.Verification(
            method="platform_read",
            evidence=f"platform reports {observed['os']} "
                     f"{observed['os_version']} on {observed['machine']}"
                     f", python {observed['python_version']}",
        ),
    ))
