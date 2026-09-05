"""
Hermes specialist profiles + kanban routing for durable multi-role work.

ADR-001: Friday owns the objective, Hermes owns durable execution. This
module adds no second objective engine - it is a thin translation from
`roles.compile_team` (already-decided staffing) to native Hermes primitives
(`hermes profile`, `hermes kanban`) reached through the CLI, the same way an
operator would drive them by hand. Every call goes through `_hermes()`,
which never raises: a kanban/profile hiccup degrades to the caller falling
back to a plain `delegate()`, it does not take Friday down.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from friday import hermes_bridge as hb
from friday import roles

logger = logging.getLogger("friday-agent")

#: The four specialist profiles, cloned from `friday` on first use. Order
#: doubles as team execution order when more than one is staffed.
PROFILES = ("friday-research", "friday-engineering", "friday-qa",
            "friday-review")

#: roles.Role.id -> specialist profile. Every id in roles.CATALOGUE must
#: land somewhere; unmapped ids fall back to "friday-engineering" (the
#: implementer is always on a real team, so this is the safe default).
_ROLE_PROFILE = {
    "architect": "friday-research", "ux": "friday-research",
    "data": "friday-research",
    "implementer": "friday-engineering", "minimal": "friday-engineering",
    "tooling": "friday-engineering", "prompt": "friday-engineering",
    "voice": "friday-engineering",
    "tests": "friday-qa", "security": "friday-qa",
    "reviewer": "friday-review",
}

#: Sizes small enough that a single `delegate()` call beats staffing a
#: kanban board - matches `roles.TEAM_SIZE` for TRIVIAL/SMALL.
_SINGLE_SIZES = (roles.TRIVIAL, roles.SMALL)

#: Present in a goal that itself came from a kanban worker's task body.
#: `submit()` refuses it - a specialist task must never be able to spawn
#: another Friday objective and loop the board back on itself.
CYCLE_MARKER = "[hermes-kanban-worker]"

#: At most this many specialist gateways alive at once (RAM-starved host).
#: `friday`'s own gateway is managed by the caller's HermesSupervisor and
#: is never touched here.
MAX_LIVE_GATEWAYS = 2
IDLE_STOP_SECONDS = 600.0

CLI_TIMEOUT = 30.0

#: profile -> (HermesSupervisor, last_used monotonic time).
_gateways: dict[str, tuple["hb.HermesSupervisor", float]] = {}

_kanban_initialised = False


def reset_state() -> None:
    """Test-only: forget cached init/gateway state between cases."""
    global _kanban_initialised
    _kanban_initialised = False
    _gateways.clear()


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------

def _hermes_exe() -> str:
    """Path to the `hermes` console script, or "" if not found."""
    override = os.getenv("HERMES_EXE", "").strip()
    if override:
        return override
    found = hb.locate()
    if found:
        candidate = Path(found["root"]) / "venv" / "Scripts" / "hermes.exe"
        if candidate.exists():
            return str(candidate)
        candidate = Path(found["root"]) / "venv" / "bin" / "hermes"
        if candidate.exists():
            return str(candidate)
    return shutil.which("hermes") or ""


def _hermes(argv: list[str], *, timeout: float = CLI_TIMEOUT) -> dict:
    """
    Run one `hermes <argv>` call. Never raises - every failure mode
    (missing exe, timeout, non-zero exit, non-JSON stdout) comes back as
    `{"error": "..."}` so callers can degrade instead of crashing Friday.
    """
    exe = _hermes_exe()
    if not exe:
        return {"error": "hermes CLI not found"}
    # ponytail: tests point HERMES_EXE_PREFIX at a fake CLI script run by
    # the interpreter in HERMES_EXE (e.g. "python fake_hermes_cli.py");
    # unset in production, so real calls are exactly `<exe> <argv>`.
    raw_prefix = os.getenv("HERMES_EXE_PREFIX", "")
    prefix = raw_prefix.split("|") if raw_prefix else []
    try:
        proc = subprocess.run([exe, *prefix, *argv], capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return {"error": (proc.stderr or out or
                          f"hermes exited {proc.returncode}").strip()}
    if not out:
        return {}
    try:
        return json.loads(out)
    except ValueError:
        return {"raw": out}


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

def profiles() -> list[str]:
    """Profile names `hermes profile list` currently knows about."""
    res = _hermes(["profile", "list"])
    if "error" in res:
        return []
    names = res.get("profiles") if isinstance(res, dict) else None
    if isinstance(names, list):
        return [n.get("name", n) if isinstance(n, dict) else n
                for n in names]
    # Text fallback: one profile per line, name is the first token.
    text = res.get("raw", "") if isinstance(res, dict) else ""
    return [line.split()[0] for line in text.splitlines() if line.strip()]


def ensure_profile(name: str) -> bool:
    """Profile `name` exists afterward (cloned from `friday`) or False."""
    if name in profiles():
        return True
    res = _hermes(["profile", "create", name, "--clone-from", "friday"])
    return "error" not in res


# ---------------------------------------------------------------------------
# Staffing -> profile order
# ---------------------------------------------------------------------------

def plan_team(goal: str, *, files: int = 0) -> tuple[str, ...]:
    """
    Ordered specialist profiles for this goal, or () meaning "too small
    for a board - use plain `delegate()`".
    """
    team = roles.compile_team(goal, files=files)
    if team.size in _SINGLE_SIZES or len(team.roles) < 2:
        return ()
    wanted = {_ROLE_PROFILE.get(role.id, "friday-engineering")
              for role in team.roles}
    return tuple(p for p in PROFILES if p in wanted)


# ---------------------------------------------------------------------------
# Kanban board
# ---------------------------------------------------------------------------

def submit(objective_task_id: str, goal: str, bundle_text: str,
          team: tuple[str, ...], model_by_profile: dict) -> dict:
    """
    Stage `team` as linked kanban tasks under `objective_task_id`.

    Returns `{"tasks": {profile: task_id}, "order": team}` on success, or
    `{"error": "..."}` - the caller falls back to `delegate()` on error,
    it does not retry blindly.
    """
    if CYCLE_MARKER in (bundle_text or ""):
        return {"error": "refused: goal originated from a kanban worker"}
    if not team:
        return {"error": "empty team"}

    global _kanban_initialised
    if not _kanban_initialised:
        res = _hermes(["kanban", "init"])
        if "error" in res:
            return res
        _kanban_initialised = True

    task_ids: dict[str, str] = {}
    for profile in team:
        if not ensure_profile(profile):
            return {"error": f"could not create profile {profile}"}
        model, provider = model_by_profile.get(profile, ("", ""))
        argv = ["kanban", "create", "--assignee", profile,
                "--parent", objective_task_id, "--goal", bundle_text,
                "--idempotency-key", f"{objective_task_id}:{profile}",
                "--json"]
        if model:
            argv += ["--model", model]
        if provider:
            argv += ["--provider", provider]
        res = _hermes(argv)
        if "error" in res:
            return res
        task_id = res.get("id") or res.get("task_id")
        if not task_id:
            return {"error": f"no task id for {profile}: {res}"}
        task_ids[profile] = task_id

    ordered_ids = [task_ids[p] for p in team]
    for earlier, later in zip(ordered_ids, ordered_ids[1:]):
        res = _hermes(["kanban", "link", earlier, later])
        if "error" in res:
            return res

    return {"objective_task_id": objective_task_id, "tasks": task_ids,
            "order": list(team), "goal": goal}


def poll(board_ref: dict) -> dict:
    """{profile: {"status": ..., "result": ...}} for every staged task."""
    out = {}
    for profile, task_id in (board_ref.get("tasks") or {}).items():
        res = _hermes(["kanban", "show", task_id, "--json"])
        if "error" in res:
            out[profile] = {"status": "error", "result": res["error"]}
            continue
        out[profile] = {"status": res.get("status", ""),
                        "result": res.get("result") or res.get("output")}
    return out


# ---------------------------------------------------------------------------
# Gateways (dispatch is per-profile, so a specialist needs its own)
# ---------------------------------------------------------------------------

def _sweep_idle(now: float) -> None:
    for profile, (sup, last) in list(_gateways.items()):
        if now - last > IDLE_STOP_SECONDS:
            sup.stop()
            del _gateways[profile]


def gateway_for(profile: str) -> "hb.HermesSupervisor":
    """
    A running gateway for `profile`, starting or reusing one.

    Caps live specialist gateways at `MAX_LIVE_GATEWAYS`: this host runs
    hot on RAM, and a fifth gateway process is how Friday gets OOM-killed
    mid-objective. The least-recently-used one is stopped to make room.
    """
    now = time.monotonic()
    _sweep_idle(now)
    if profile in _gateways:
        sup, _ = _gateways[profile]
        _gateways[profile] = (sup, now)
        return sup
    if len(_gateways) >= MAX_LIVE_GATEWAYS:
        lru_profile = min(_gateways, key=lambda p: _gateways[p][1])
        _gateways.pop(lru_profile)[0].stop()
    home = hb.profile_home(profile)
    sup = hb.HermesSupervisor(profile=profile, cwd=home or None)
    sup.start()
    _gateways[profile] = (sup, now)
    return sup
