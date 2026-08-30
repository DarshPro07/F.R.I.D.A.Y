#!/usr/bin/env python3
"""
Run every capability TWICE and report what actually works.

Once proves it can work. Twice separates "works" from "worked that time" -
and flaky is the failure mode that has bitten this project hardest, so it gets
its own verdict rather than being averaged away.

    python scripts/verify_all.py              # read-only + reversible
    python scripts/verify_all.py --full       # also camera, music, reminders
    python scripts/verify_all.py --runs 3     # more repetitions

Verdicts:
    PASS   both runs verified
    FLAKY  one run verified, one did not   <- the dangerous one
    FAIL   neither run verified
    SKIP   unavailable here (no hardware, no credential, app not running)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import contracts as c  # noqa: E402


@dataclass
class Check:
    name: str
    group: str
    fn: object
    heavy: bool = False          # side effects the user will notice
    note: str = ""


@dataclass
class Result:
    check: Check
    outcomes: list = field(default_factory=list)   # True / False / "skip"
    errors: list = field(default_factory=list)
    timings: list = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(o == "skip" for o in self.outcomes):
            return "SKIP"
        good = sum(1 for o in self.outcomes if o is True)
        if good == len(self.outcomes):
            return "PASS"
        return "FAIL" if good == 0 else "FLAKY"

    @property
    def mean_ms(self) -> int:
        return int(1000 * sum(self.timings) / len(self.timings)) if self.timings else 0


class Skip(Exception):
    """This check cannot run here."""


def ok(result) -> bool:
    """A capability passes only if its ActionResult may claim completion."""
    return bool(getattr(result, "may_claim_completion", False))


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def build_checks() -> list[Check]:
    from friday.toolsets import files as F
    from friday.toolsets import media as MD
    from friday.toolsets import memory as MEM
    from friday.toolsets import music as MU
    from friday.toolsets import reminders as R
    from friday.toolsets import system as S
    from friday.toolsets import vision as V
    from friday.toolsets import web as W

    def run() -> c.Run:
        return c.Run.create("verify", capability="verify")

    def sync(fn):
        return lambda: ok(fn(run()))

    def aio(coro_fn):
        import asyncio

        def call():
            return ok(asyncio.run(coro_fn(run())))

        return call

    checks: list[Check] = []
    add = checks.append

    # -- system --------------------------------------------------------------
    add(Check("system.get_info", "system", sync(S.system_get_info)))
    add(Check("system.list_processes", "system",
              lambda: ok(S.system_list_processes(run(), top=5))))
    add(Check("system.resource_usage", "system", sync(S.system_resource_usage)))
    add(Check("system.wifi_status", "system", sync(S.system_wifi_status)))
    add(Check("apps.list_known", "system", sync(S.apps_list_known)))
    add(Check("volume.get", "system", sync(S.volume_get)))
    add(Check("clipboard.read", "system", sync(S.clipboard_read)))
    add(Check("clipboard.write", "system",
              lambda: ok(S.clipboard_write(run(), "ada-verify"))))

    def apps_open_calc():
        result = S.apps_open(run(), "calculator")
        if ok(result):
            engine_run = run()
            S.apps_close(engine_run, "calculator")
        return ok(result)

    add(Check("apps.open + close", "system", apps_open_calc, heavy=True,
              note="opens and closes Calculator"))

    def apps_open_missing():
        # A negative check: a nonexistent app must FAIL, never claim success.
        result = S.apps_open(run(), "flurbomatic 9000")
        return result.status == "failed" and not ok(result)

    add(Check("apps.open (unknown -> fails)", "system", apps_open_missing))

    # -- web -----------------------------------------------------------------
    add(Check("web.search", "web",
              aio(lambda r: W.web_search(r, "livekit agents python", limit=5))))
    add(Check("web.fetch", "web",
              aio(lambda r: W.web_fetch(r, "https://example.com"))))
    add(Check("web.news", "web", aio(lambda r: W.web_news(r, "world", limit=5))))

    def browser_cycle():
        import asyncio

        async def journey():
            r = run()
            opened = await W.browser_open(r, "https://example.com", headless=True)
            if not ok(opened):
                await W.browser_close(r)
                return False
            inspected = await W.browser_inspect(r)
            await W.browser_close(r)
            return ok(inspected)

        return asyncio.run(journey())

    add(Check("browser open/inspect/close", "web", browser_cycle, heavy=True))

    # -- files ---------------------------------------------------------------
    add(Check("files.roots", "files", sync(F.files_roots)))

    def file_cycle():
        with tempfile.TemporaryDirectory() as tmp:
            from friday.fsjail import FileJail

            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            F.reset_jail(FileJail(roots=(workspace,)))
            try:
                r = run()
                created = F.files_create(r, str(workspace / "n.txt"), "hello")
                read = F.files_read(r, str(workspace / "n.txt"))
                edited = F.files_edit(r, str(workspace / "n.txt"), "hello", "world")
                listed = F.files_list(r, str(workspace))
                return all(ok(x) for x in (created, read, edited, listed))
            finally:
                F.reset_jail(None)

    add(Check("files create/read/edit/list", "files", file_cycle))

    def jail_escape():
        with tempfile.TemporaryDirectory() as tmp:
            from friday.fsjail import FileJail

            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            (Path(tmp) / "secret.txt").write_text("x")
            F.reset_jail(FileJail(roots=(workspace,)))
            try:
                escaped = F.files_read(run(), str(Path(tmp) / "secret.txt"))
                return escaped.status == "failed" and not ok(escaped)
            finally:
                F.reset_jail(None)

    add(Check("files jail blocks escape", "files", jail_escape))

    # -- memory --------------------------------------------------------------
    def memory_cycle():
        from friday.store import FACT, Store

        with tempfile.TemporaryDirectory() as tmp:
            MEM.reset_store(Store(Path(tmp) / "m.db"))
            try:
                r = run()
                stored = MEM.memory_remember(r, "verify.key", "verify-value",
                                             kind=FACT, source="verify run")
                recalled = MEM.memory_recall(r, "verify.key")
                fuzzy = MEM.memory_recall(r, "verify key")
                searched = MEM.memory_search(r, "verify")
                return all(ok(x) for x in (stored, recalled, fuzzy, searched))
            finally:
                MEM.reset_store(None)

    add(Check("memory remember/recall/search", "memory", memory_cycle))

    def memory_persists():
        """Across a fresh Store on the same file - the durability claim."""
        from friday.store import FACT, Store

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.db"
            MEM.reset_store(Store(path))
            MEM.memory_remember(run(), "persist.key", "persist-value",
                                kind=FACT, source="verify")
            MEM.reset_store(None)
            MEM.reset_store(Store(path))
            try:
                return ok(MEM.memory_recall(run(), "persist.key"))
            finally:
                MEM.reset_store(None)

    add(Check("memory survives reopen", "memory", memory_persists))

    # -- vision --------------------------------------------------------------
    add(Check("vision.screen_capture", "vision", sync(V.screen_capture)))
    add(Check("vision.inspect_screen", "vision",
              lambda: ok(V.inspect_screen(run(), "What is on this screen?")),
              heavy=True, note="calls the vision model"))

    def camera():
        result = V.camera_frame(run())
        if result.status == "failed" and "camera" in (result.error or "").lower():
            raise Skip("no usable camera")
        return ok(result)

    add(Check("vision.camera_frame", "vision", camera, heavy=True,
              note="takes a webcam photo"))

    # -- music ---------------------------------------------------------------
    add(Check("music.search", "music",
              lambda: ok(MU.music_search(run(), "hans zimmer time", limit=3))))

    def music_cycle():
        r = run()
        played = MU.music_play(r, "hans zimmer time")
        if not ok(played):
            MU.player.stop()
            return False
        time.sleep(2)
        paused = MU.music_pause(r)
        resumed = MU.music_resume(r)
        MU.music_stop(r)
        return ok(paused) and ok(resumed)

    add(Check("music play/pause/resume/stop", "music", music_cycle, heavy=True,
              note="plays audio out loud"))

    # -- spotify -------------------------------------------------------------
    def spotify_current():
        if MD.spotify_window() is None:
            raise Skip("Spotify is not running")
        return ok(MD.spotify_current(run()))

    add(Check("spotify.current", "spotify", spotify_current))

    # -- reminders -----------------------------------------------------------
    def reminder_cycle():
        from friday.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            R.reset_store(Store(Path(tmp) / "r.db"))
            try:
                r = run()
                created = R.reminders_create(r, "ada verify", "in 40 minutes")
                if not ok(created):
                    return False
                listed = R.reminders_list(r)
                cancelled = R.reminders_cancel(r, created.output["id"])
                return ok(listed) and ok(cancelled)
            finally:
                R.prune_stale()
                R.reset_store(None)

    add(Check("reminders create/list/cancel", "reminders", reminder_cycle,
              heavy=True, note="registers and removes a scheduled task"))

    # -- profile -------------------------------------------------------------
    def profile_cycle():
        from friday import profile as P
        from friday.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "p.db")
            try:
                outcomes = P.learn(store, [P.Candidate(
                    dimension=P.PREFERENCES, subject="verify.pref",
                    value="verified", kind="PREFERENCE", confidence=0.9,
                    evidence="stated during verification")])
                return outcomes[0].action == "stored" and bool(P.brief(store))
            finally:
                store.close()

    add(Check("profile learn/reconcile", "profile", profile_cycle))

    def profile_extract():
        from friday import profile as P

        try:
            found = P.extract_candidates(
                "I build on a Windows laptop and prefer local-first tools.")
        except P.ExtractionError as exc:
            raise Skip(str(exc)) from exc
        return bool(found)

    add(Check("profile extraction (model)", "profile", profile_extract,
              heavy=True, note="calls the model"))

    return checks


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true",
                        help="include checks with visible side effects")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--group", help="only this group")
    args = parser.parse_args()

    checks = build_checks()
    if args.group:
        checks = [c_ for c_ in checks if c_.group == args.group]
    if not args.full:
        checks = [c_ for c_ in checks if not c_.heavy]

    print("=" * 74)
    print(f"VERIFYING {len(checks)} capabilities, {args.runs} run(s) each"
          + ("" if args.full else "  (read-only; --full for the rest)"))
    print("=" * 74)

    results: list[Result] = []
    for check in checks:
        result = Result(check=check)
        for attempt in range(args.runs):
            start = time.monotonic()
            try:
                passed = check.fn()
                result.outcomes.append(bool(passed))
            except Skip as exc:
                result.outcomes.append("skip")
                result.errors.append(str(exc))
            except Exception as exc:
                result.outcomes.append(False)
                result.errors.append(f"{type(exc).__name__}: {exc}")
                if attempt == 0:
                    result.errors.append(traceback.format_exc(limit=3))
            result.timings.append(time.monotonic() - start)
        results.append(result)

        marks = "".join({True: ".", False: "x"}.get(o, "-") for o in result.outcomes)
        print(f"  [{result.verdict:<5}] {check.name:<34} {marks}  "
              f"{result.mean_ms:>6}ms"
              + (f"   {result.errors[0][:60]}" if result.errors else ""))

    print("\n" + "=" * 74)
    by_verdict: dict[str, list[str]] = {}
    for result in results:
        by_verdict.setdefault(result.verdict, []).append(result.check.name)

    for verdict in ("PASS", "FLAKY", "FAIL", "SKIP"):
        names = by_verdict.get(verdict, [])
        print(f"{verdict:<6} {len(names):>3}"
              + (f"   {', '.join(names)}" if verdict != "PASS" and names else ""))

    flaky = by_verdict.get("FLAKY", [])
    failed = by_verdict.get("FAIL", [])
    if flaky:
        print("\nFLAKY is the one that matters - it works often enough to be")
        print("trusted and fails often enough to hurt:")
        for result in results:
            if result.verdict == "FLAKY":
                print(f"  {result.check.name}: {(result.errors or ['no error recorded'])[0][:110]}")
    if failed:
        print("\nFAILED:")
        for result in results:
            if result.verdict == "FAIL":
                print(f"  {result.check.name}: {(result.errors or ['no error recorded'])[0][:110]}")

    print("=" * 74)
    return 0 if not (flaky or failed) else 1


if __name__ == "__main__":
    sys.exit(main())
