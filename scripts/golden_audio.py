#!/usr/bin/env python3
"""
Audio session control, on a session Friday created.

The ownership invariant, applied: this never touches an application the boss
started. It spawns a process that renders a tone, controls *that* session,
puts it back, and kills it. Nothing else on the machine is a valid target for
a test.

The master volume is the exception - there is only one, and it belongs to the
machine rather than to anybody. So it is changed by a few percent, checked,
and restored through the same conflict-aware transaction as everything else,
which is the whole reason that transaction exists.

    python scripts/golden_audio.py
"""

from __future__ import annotations

import math
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import contracts as c  # noqa: E402
from friday import reversible as R  # noqa: E402
from friday.toolsets import audio as A  # noqa: E402

TONE = Path(tempfile.gettempdir()) / "friday_audio_gate.wav"
SECONDS = 25


def check(passed: bool, message: str, detail: str = "") -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    if detail:
        print(f"         {detail}")
    return bool(passed)


def run_for(label: str) -> c.Run:
    return c.Run.create(label, capability="system")


def write_tone() -> Path:
    """A quiet sine wave. Quiet because somebody is sitting there."""
    with wave.open(str(TONE), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"".join(
            struct.pack("<h", int(1200 * math.sin(2 * math.pi * 440 * t / 22050)))
            for t in range(22050 * SECONDS)))
    return TONE


def start_tone() -> subprocess.Popen:
    source = f"import winsound; winsound.PlaySound({str(TONE)!r}, winsound.SND_FILENAME)"
    return subprocess.Popen([sys.executable, "-c", source])


def our_session(pid: int):
    for session in A.sessions():
        if session.ProcessId == pid:
            return session
    return None


def main() -> int:
    results: list[bool] = []
    print("=" * 70)
    print("An audio session Friday created")
    print("=" * 70)

    write_tone()
    player = start_tone()
    try:
        session = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and session is None:
            session = our_session(player.pid)
            if session is None:
                time.sleep(0.25)

        results.append(check(session is not None,
                             "a session appeared for the process Friday started",
                             f"pid={player.pid}"))
        if session is None:
            return 1
        volume = A._volume_of(session)
        pattern = str(player.pid)

        def read_percent() -> int:
            return A.to_percent(volume.GetMasterVolume())

        def write_percent(percent: int) -> None:
            volume.SetMasterVolume(A.to_level(percent), None)

        # --- the ordinary transaction ---------------------------------------
        record = R.attempt("our session volume", read_percent, write_percent,
                           30, matches=A.close_enough,
                           gone=lambda: our_session(player.pid) is None)
        results.append(check(record.clean,
                             "session volume changed and was put back",
                             record.summary()))

        # --- somebody else moves it afterwards ------------------------------
        def somebody_else_changes_it(_record):
            # Not through Friday: exactly what a person reaching for the
            # mixer looks like from here.
            volume.SetMasterVolume(A.to_level(85), None)

        conflict = R.attempt("our session volume", read_percent, write_percent,
                             40, matches=A.close_enough,
                             while_changed=somebody_else_changes_it)
        results.append(check(
            conflict.outcome == R.RESTORE_CONFLICT,
            "a change by somebody else is detected and NOT overwritten",
            conflict.summary()))
        results.append(check(
            A.close_enough(85, read_percent()),
            "the other change is still in place",
            f"reads {read_percent()}%"))
        write_percent(record.before)          # tidy up after the conflict test

        # --- mute, through the tool ------------------------------------------
        muted = A.audio_session_mute(run_for("mute"), pattern, True)
        results.append(check(
            muted.status == c.SUCCEEDED
            and muted.output["observed_muted"] is True,
            "muted, and the session agrees",
            muted.verification.evidence if muted.verification else muted.error))
        unmuted = A.audio_session_mute(run_for("unmute"), pattern, False)
        results.append(check(
            unmuted.status == c.SUCCEEDED
            and unmuted.output["observed_muted"] is False,
            "unmuted again"))
        results.append(check(
            unmuted.output["previous_muted"] is True,
            "the result carries what it was before, so a task can undo it"))

        # --- and the session going away --------------------------------------
        def the_player_exits(_record):
            player.terminate()
            player.wait(timeout=5)
            time.sleep(1.0)

        gone = R.attempt("our session volume", read_percent, write_percent, 50,
                         matches=A.close_enough,
                         while_changed=the_player_exits,
                         gone=lambda: our_session(player.pid) is None)
        results.append(check(
            gone.outcome in (R.TARGET_GONE, R.RESTORE_CONFLICT),
            "a session that disappears is not reported as restored",
            gone.summary()))
    finally:
        if player.poll() is None:
            player.terminate()
            try:
                player.wait(timeout=5)
            except subprocess.TimeoutExpired:
                player.kill()
        TONE.unlink(missing_ok=True)
        print("\n  (the tone Friday started has been stopped)")

    # --- the master volume, which belongs to the machine --------------------
    print("\n" + "=" * 70)
    print("The master volume, changed by a little and put back")
    print("=" * 70)

    control = A.endpoint_volume()

    def read_master() -> int:
        return A.to_percent(control.GetMasterVolumeLevelScalar())

    def write_master(percent: int) -> None:
        control.SetMasterVolumeLevelScalar(A.to_level(percent), None)

    original = read_master()
    target = max(0, min(100, original - 5)) or 5
    master = R.attempt("master volume", read_master, write_master, target,
                       matches=A.close_enough)
    results.append(check(master.clean,
                         "master volume changed and was put back",
                         master.summary()))
    results.append(check(A.close_enough(original, read_master()),
                         "the machine is as it was found",
                         f"was {original}%, reads {read_master()}%"))

    passed = sum(1 for r in results if r)
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{len(results)} behaved correctly")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
