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
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
ARTIFACTS_DIR = DATA_DIR / "artifacts"


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
        "data_dir": str(DATA_DIR),
        "database": str(os.getenv("ADA_DB") or DEFAULT_DB),
        "logs_dir": str(LOGS_DIR),
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
