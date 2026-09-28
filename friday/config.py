"""
Configuration - environment loading, plus an explicit classification of every
variable this project recognises.

The classification exists because the .env had accumulated keys nothing read
(Supabase for a ticketing tool that does not exist, Deepgram and Google Cloud
for STT providers that were never implemented). Unexplained credentials are a
security liability, not harmless clutter, so every name is now labelled.

  USED      - read by code on the default path
  RESERVED  - read by an implemented provider that is not currently selected
  DEAD      - nothing reads it; remove it from your .env

Values are never stored, logged or returned by anything in this module - only
whether a name is set.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Runtime roots
# ---------------------------------------------------------------------------
#
# A BACKGROUND PROCESS MUST NOT DEPEND ON THE CALLER'S WORKING DIRECTORY.
#
# This rule is here because breaking it does not raise. A scheduled automation
# fired on time, ran its whole step graph, and wrote its result to
# `C:\Windows\System32\data\ada.sqlite3` - because Task Scheduler starts a
# process in System32 and the database path was relative. Nothing failed. The
# run was simply not in the database anyone reads, so a working automation was
# indistinguishable from one that never fired.
#
# The same defect was waiting in four other places, and two of them were
# security relevant rather than merely confusing:
#
#   fsjail.DEFAULT_WORKSPACE    the filesystem jail's root. A jail anchored to
#                               the working directory is a jail whose walls
#                               move when you launch from somewhere else.
#   companion token / key       the paired browser extension's secret. Read
#                               from a different directory it is simply absent,
#                               and the failure looks like a pairing problem.
#
# Anything a detached process reads or writes resolves from here, never from
# `Path("data")`. `Path.resolve()` on a relative path does not help: it
# resolves against the working directory, which is the thing in question.
#
# Two roots, because they have different lifetimes:
#
#   PROJECT_ROOT   the application: `ui/`, `third_party/`, `docs/golden/`,
#                  `scripts/`. Replaced wholesale by an upgrade. In a
#                  PyInstaller onedir build this is `dist\FRIDAY\_internal`.
#   STATE_ROOT     the user's state: `data/` (database, artifacts, captures),
#                  `logs/`, `vault/`, `config/`, `skills/`. Must survive an
#                  upgrade, so it never lives inside the bundle:
#
#       FRIDAY_HOME set        that directory, everywhere (tests, portable,
#                              a second install side by side)
#       frozen + portable      beside the executable (`PORTABLE_MODE=true`
#                              in the environment, or a PORTABLE_MODE file
#                              beside FRIDAY.exe - a double-clicked
#                              executable has no environment to speak of)
#       frozen                 %LOCALAPPDATA%\FRIDAY, which is what Windows
#                              reserves for per-user, machine-specific
#                              application state (FOLDERID_LocalAppData)
#       source checkout        the repository root, as it always was
#
# Nothing here reads the working directory: `__file__`, `sys.executable`
# and the environment are the only inputs, so every detached process
# resolves the same answer (tests/test_runtime_roots.py launches four).

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PORTABLE_MARKER = "PORTABLE_MODE"


def frozen() -> bool:
    """True inside a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def executable_dir() -> Path:
    """The directory holding FRIDAY.exe (or the interpreter, from source)."""
    return Path(sys.executable).resolve().parent


def portable_mode() -> bool:
    """
    Whether state lives beside the executable instead of under the profile.

    `PORTABLE_MODE=true|1|yes|on` in the environment decides; absent that, a
    file named PORTABLE_MODE beside a frozen executable does. Never true from
    a source checkout, where the repository already is the portable layout.
    """
    flag = os.getenv("PORTABLE_MODE", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return frozen() and (executable_dir() / PORTABLE_MARKER).exists()


def default_state_root() -> Path:
    """Where mutable state lives; see the module comment for the order."""
    explicit = os.getenv("FRIDAY_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if frozen():
        if portable_mode():
            return executable_dir()
        local = os.getenv("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "FRIDAY"
    return PROJECT_ROOT


STATE_ROOT = default_state_root()
DATA_DIR = STATE_ROOT / "data"
LOGS_DIR = STATE_ROOT / "logs"
VAULT_DIR = STATE_ROOT / "vault"
CONFIG_DIR = STATE_ROOT / "config"
SKILLS_DIR = STATE_ROOT / "skills"
CACHE_DIR = STATE_ROOT / "cache"
RUNTIME_DIR = STATE_ROOT / "runtime"
ARTIFACTS_DIR = DATA_DIR / "artifacts"

#: Every directory the runtime writes into. The launcher creates them before
#: any role starts; from source they already exist in the checkout.
STATE_DIRS = (DATA_DIR, DATA_DIR / "logs", ARTIFACTS_DIR, LOGS_DIR, VAULT_DIR,
              CONFIG_DIR, SKILLS_DIR, CACHE_DIR, RUNTIME_DIR)


def env_file_candidates() -> list[Path]:
    """
    Where `.env` may be, in the order it is looked for. Only the first file
    that exists is loaded, and a variable already in the environment always
    wins over any file (`override=False`), so the effective order is:

        1. the OS environment
        2. STATE_ROOT/config/.env   (%LOCALAPPDATA%\\FRIDAY\\config\\.env for a
                                     packaged build; FRIDAY_HOME/config/.env
                                     when FRIDAY_HOME is set)
        3. beside the executable    only in portable mode
        4. the repository .env      only from a source checkout

    A packaged build never reads the repository, and a non-portable build
    never reads a file lying beside the executable.
    """
    found: list[Path] = []
    if frozen() or os.getenv("FRIDAY_HOME", "").strip():
        found.append(CONFIG_DIR / ".env")
    if frozen() and portable_mode():
        found.append(executable_dir() / ".env")
    if not frozen():
        found.append(PROJECT_ROOT / ".env")
    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def adopt_legacy_env_file() -> Path | None:
    """
    One-time move of a `.env` an earlier packaged build kept beside the
    executable into STATE_ROOT/config/. Only in a frozen, non-portable
    build, only when the new location is still empty, so it can never
    overwrite a newer file. Returns the new path when something moved.
    """
    if not frozen() or portable_mode():
        return None
    legacy = executable_dir() / ".env"
    target = CONFIG_DIR / ".env"
    if not legacy.is_file() or target.exists():
        return None
    try:
        import shutil

        target.parent.mkdir(parents=True, exist_ok=True)
        # shutil.move, not Path.replace: the bundle and the profile are
        # usually on different drives (F:\dist -> C:\Users), and os.replace
        # cannot cross a drive boundary on Windows (WinError 17).
        shutil.move(str(legacy), str(target))
    except OSError:
        return None
    return target


def load_env_files() -> Path | None:
    """Load the first `.env` from `env_file_candidates()`; None if there is none."""
    adopt_legacy_env_file()
    for path in env_file_candidates():
        if path.is_file():
            load_dotenv(path, override=False)
            return path
    return None


#: The file this process loaded at import, or None. Modules that used to call
#: a bare `load_dotenv()` get the same answer by importing this module.
ENV_FILE = load_env_files()


def runtime_paths() -> dict[str, str]:
    """
    Where this process actually resolved everything, for the record.

    Recorded on every automation run so the next instance of this class of bug
    is visible in the run itself rather than needing to be re-derived: a `cwd`
    of `C:\\Windows\\System32` next to a `database` under the project root is
    the fixed version, and the same `cwd` next to a database beside it is the
    broken one.
    """
    from friday.store import DEFAULT_DB

    return {
        "cwd": str(Path.cwd()),
        "project_root": str(PROJECT_ROOT),
        "state_root": str(STATE_ROOT),
        "data_dir": str(DATA_DIR),
        "database": str(os.getenv("ADA_DB") or DEFAULT_DB),
        "logs_dir": str(LOGS_DIR),
        "vault_dir": str(VAULT_DIR),
        "config_dir": str(CONFIG_DIR),
        "env_file": str(ENV_FILE) if ENV_FILE else "",
        "portable": "true" if portable_mode() else "false",
        "frozen": "true" if frozen() else "false",
    }

USED = "USED"
RESERVED = "RESERVED_FOR_IMPLEMENTED_PROVIDER"
DEAD = "DEAD"

# name -> (classification, why)
VARIABLES: dict[str, tuple[str, str]] = {
    # --- runtime identity -------------------------------------------------
    "SERVER_NAME": (USED, "MCP server name"),
    "DEBUG": (USED, "debug flag"),
    # --- LiveKit ----------------------------------------------------------
    "LIVEKIT_URL": (USED, "LiveKit worker connection"),
    "LIVEKIT_API_KEY": (USED, "LiveKit worker auth"),
    "LIVEKIT_API_SECRET": (USED, "LiveKit worker auth"),
    # --- provider selection ----------------------------------------------
    "STT_PROVIDER": (USED, "friday.providers.build_stt"),
    "LLM_BACKEND": (USED, "friday.providers.build_llm"),
    "LLM_ROLE": (USED, "role -> model resolution"),
    "TTS_PROVIDER": (USED, "friday.providers.build_tts"),
    "TTS_SPEED": (USED, "TTS pace"),
    "MCP_URL": (USED, "agent_friday.mcp_sse_url"),
    # --- provider credentials --------------------------------------------
    "GOOGLE_API_KEY": (USED, "default LLM backend (google)"),
    "SARVAM_API_KEY": (USED, "default STT (sarvam)"),
    "OPENAI_API_KEY": (USED, "default TTS (openai), and whisper STT"),
    "GROQ_API_KEY": (RESERVED, "groq STT provider, implemented but not default"),
    # --- dead -------------------------------------------------------------
    "SEARCH_API_KEY": (DEAD, "no search provider implemented; search_web returns NOT_CONFIGURED"),
    "DEEPGRAM_API_KEY": (DEAD, "no deepgram provider implemented"),
    "GOOGLE_APPLICATION_CREDENTIALS": (DEAD, "no google-cloud STT provider implemented"),
    "SUPABASE_URL": (DEAD, "no ticketing tool exists"),
    "SUPABASE_API_KEY": (DEAD, "no ticketing tool exists"),
    "ELEVENLABS_API_KEY": (DEAD, "no elevenlabs provider implemented"),
    "CARTESIA_API_KEY": (DEAD, "no cartesia provider implemented"),
}


def is_set(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def by_classification(classification: str) -> tuple[str, ...]:
    return tuple(n for n, (c, _) in VARIABLES.items() if c == classification)


def dead_but_present() -> tuple[str, ...]:
    """DEAD variables that are still populated in the environment."""
    return tuple(n for n in by_classification(DEAD) if is_set(n))


def unknown_env_names(environ: dict | None = None) -> tuple[str, ...]:
    """
    Names present in a parsed .env that this project does not recognise.

    Takes a mapping rather than reading os.environ, because os.environ holds
    the whole machine's environment and would report thousands of unrelated
    names.
    """
    if environ is None:
        return ()
    return tuple(sorted(set(environ) - set(VARIABLES)))


class Config:
    # Server identity
    SERVER_NAME: str = os.getenv("SERVER_NAME", "Friday")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"


config = Config()
