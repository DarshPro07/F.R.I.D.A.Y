"""
Agent-Reach, narrowed to the one verb it is good for: transcribe.

MIT, pinned. The lock's REFERENCE_ONLY note on this upstream said what was
wrong with it - its routing is what the fabric already is, `skill` rewrites
the host agent's config, and most channels need per-site cookies - and then
said what was right: "Worth revisiting narrowly for `transcribe` (Whisper via
Groq/OpenAI), which is a clean verb and a capability Friday genuinely lacks."
This is that revisit. Nothing else from the CLI is reachable.

`transcribe <source>` downloads with yt-dlp, chunks with ffmpeg, and posts to
Groq's Whisper endpoint. The key is resolved from the secret broker inside
`call()` for that one operation, handed to the subprocess as environment,
never as argv, so it cannot appear in the redacted evidence line. `doctor
--json` is the open probe and needs no key - which is why the key is NOT on
the descriptor's `secrets`: that gate is provider-wide and would close the
probe too.

## The interpreter path is absolute, deliberately

The first version named `.venv/Scripts/python.exe` relative to the clone.
Windows resolves a relative executable against the PARENT's cwd, not the
child's, so it silently ran Friday's own venv - which, with the clone as cwd,
could import the package and printed `ok`. READY on a venv that did not exist
is the presence-vs-function bug this fabric keeps finding, so the path is
built absolute and health says UNAVAILABLE with the install command until
the clone's venv is real.
"""
from __future__ import annotations

import pathlib

from friday import contracts as c
from friday import fabric, fabric_cli
from friday.fabric_adapters import _cli_adapter

UPSTREAM = "agent-reach"

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
CLONE = ROOT / "third_party" / "upstream" / UPSTREAM
PY = CLONE / ".venv" / "Scripts" / "python.exe"

SECRET_ALIAS = "groq_api_key"
ENV_NAME = "GROQ_API_KEY"


def _ffmpeg_dir() -> str:
    """Where ffmpeg/ffprobe live, or "". PATH first; then FFMPEG_DIR; then
    the Windows convention of an extracted `ffmpeg-*` build on a drive root.
    ffprobe is what the upstream actually needs for a local file, so the
    directory must contain both."""
    import os
    import shutil
    found = shutil.which("ffprobe")
    if found:
        return str(pathlib.Path(found).parent)
    candidates = []
    hint = os.environ.get("FFMPEG_DIR", "").strip()
    if hint:
        candidates.append(pathlib.Path(hint))
        candidates.append(pathlib.Path(hint) / "bin")
    for drive in ("D:/", "C:/", "E:/"):
        root = pathlib.Path(drive)
        if root.is_dir():
            candidates.extend(sorted(root.glob("ffmpeg*/bin"), reverse=True))
    for cand in candidates:
        if (cand / "ffprobe.exe").exists() or (cand / "ffprobe").exists():
            return str(cand)
    return ""


def _child_path() -> str:
    """PATH for the child: the clone's venv Scripts (yt-dlp.exe) first, then
    the ffmpeg directory, then whatever the parent has. The supervisor passes
    PATH through unchanged, so a venv-local yt-dlp that is not on the
    operator's PATH was invisible - `yt-dlp not found in PATH` on a clone
    that had just installed it - and ffprobe was on the shell's PATH but not
    the service's."""
    import os
    parts = [str(PY.parent), _ffmpeg_dir(), os.environ.get("PATH", "")]
    return os.pathsep.join(p for p in parts if p)

DESCRIPTOR = fabric.Provider(
    id="agent_reach_transcribe",
    family="media",
    upstream=UPSTREAM,
    operations=("doctor", "transcribe"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.CLI,
    open_operations=("doctor", "transcribe"),
    cost_class="cheap",
    model_required=True,
    commit="06c202b03400a7d31886bf4399213706da1a0324",
    notes=("MIT. Only `transcribe` and `doctor` are exposed; setup/install/"
           "skill/configure rewrite the operator's agent config and are "
           "never reachable. transcribe resolves broker alias groq_api_key "
           "into GROQ_API_KEY for the child; doctor is open."),
)

BOOTSTRAP = fabric_cli.Bootstrap(
    check=(str(PY), "-c", "import agent_reach.transcribe; print('ok')"),
    install=("uv", "sync"),
)

COMMANDS = {
    "doctor": fabric_cli.Command(
        argv=(str(PY), "-m", "agent_reach.cli", "doctor", "--json"),
        timeout=60.0, output=fabric_cli.JSON_STDOUT),
    "transcribe": fabric_cli.Command(
        argv=(str(PY), "-m", "agent_reach.cli", "transcribe", "{source}",
              "--provider", "groq"),
        timeout=900.0),
}

_start, _stop, _health, _call = _cli_adapter.make(DESCRIPTOR, BOOTSTRAP, COMMANDS)


def start():
    return _start()


def stop(handle=None):
    return _stop(handle)


def health(handle=None) -> dict:
    if not PY.exists():
        return {"state": fabric.UNAVAILABLE,
                "detail": f"clone has no venv at {PY}; run `uv sync` in {CLONE}"}
    probe = _health(handle)
    if probe.get("state") == fabric.READY and not _ffmpeg_dir():
        # The package imports without ffmpeg; a transcription does not run
        # without it. Present-but-cannot-transcribe is DEGRADED, not READY.
        return {"state": fabric.DEGRADED,
                "detail": "package OK but no ffprobe found; set FFMPEG_DIR "
                          "or put an ffmpeg-*/bin on a drive root"}
    return probe


def _groq_key() -> str:
    from friday.secret_broker import SecretBroker
    try:
        return SecretBroker().resolve_for_process(SECRET_ALIAS)
    except Exception:  # noqa: BLE001 - absent alias, unreadable store
        return ""


def call(operation: str, handle=None, *, run_id: str = "", **arguments):
    arguments.pop("secrets", None)
    if operation == "transcribe":
        key = _groq_key()
        if not key:
            result = c.started(run_id or c.new_run_id(),
                               f"fabric.{DESCRIPTOR.id}.{operation}")
            return c.failed(result, f"transcribe needs the secret "
                                    f"{SECRET_ALIAS!r} in the broker")
        # Env, never argv: the redacted evidence line can only see argv.
        arguments["secrets"] = {ENV_NAME: key, "PATH": _child_path()}
    return _call(operation, handle, run_id=run_id, **arguments)
