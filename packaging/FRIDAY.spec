# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for FRIDAY.exe - a onedir build.

Run through scripts/build_windows.py, which writes build/staging/
build_manifest.json (version, commit, build time) before invoking this and
preserves the user's state across rebuilds. Invoking PyInstaller on this
file directly works too, but produces an unstamped build.

Why onedir: every friday module anchors its state on
Path(__file__).resolve().parent.parent, which here is dist/FRIDAY/_internal -
a real, writable directory that survives restarts. A onefile build would put
the database, logs and vault in a temp directory deleted at exit.
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import (collect_all, collect_data_files,
                                     collect_dynamic_libs, collect_submodules)

ROOT = Path(SPECPATH).resolve().parent
STAGING = Path(os.environ.get("FRIDAY_BUILD_STAGING", ROOT / "build" / "staging"))

datas: list = []
binaries: list = []
hiddenimports: list = []


def take(package: str) -> None:
    """Everything of a package: modules, data files, native libraries."""
    try:
        d, b, h = collect_all(package)
    except Exception:  # noqa: BLE001 - namespace packages have no metadata
        d = collect_data_files(package)
        b = collect_dynamic_libs(package)
        h = collect_submodules(package)
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)


# The runtime: LiveKit (namespace package: agents, rtc ffi dll, every plugin
# and the silero onnx model), inference and media natives, the MCP SDK, the
# web-extraction data tables, COM/audio bindings, and Friday itself (its
# adapters and toolsets are discovered with pkgutil, which static analysis
# cannot see).
for package in ("livekit", "onnxruntime", "av", "mcp", "trafilatura",
                "comtypes", "pycaw", "tokenizers", "huggingface_hub",
                "friday", "scripts"):
    take(package)

hiddenimports += [
    "server",            # the MCP server module at the repository root
    "agent_friday",      # the LiveKit worker module at the repository root
    "scripts.run_ui",    # the control-room launcher
    "dotenv",
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on", "uvicorn.lifespan.off",
]

# Files Friday's processes read from disk (not through the import system).
datas += [
    (str(ROOT / "ui"), "ui"),                                # the control room
    (str(ROOT / "third_party"), "third_party"),              # upstream lock + clones
    (str(ROOT / "data" / "corpus"), "data/corpus"),          # evaluation corpora
    (str(ROOT / "data" / "gate"), "data/gate"),              # extraction fixtures
    # The vault is the owner's state (STATE_ROOT/vault), never shipped: a
    # copy inside _internal would be reported as legacy state on every build.
    # Read by Hermes's own Python as a script path, so it must exist on disk.
    (str(ROOT / "friday" / "hermes_model_gateway_worker.py"), "friday"),
    # Launched as `sys.executable scripts/jarvis_overlay.py`.
    (str(ROOT / "scripts" / "jarvis_overlay.py"), "scripts"),
]
if (STAGING / "build_manifest.json").is_file():
    datas.append((str(STAGING / "build_manifest.json"), "."))

a = Analysis(
    [str(ROOT / "friday_exe.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "notebook",
              "pytest", "PyInstaller", "setuptools._distutils"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FRIDAY",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
# The same analysis, a second bootloader with its own diagnostics on: the
# bootloader traces imports and extraction, and the launcher (which sees
# its own file name) turns on DEBUG logging and fault handlers. A start
# that dies before the first log line is inspectable with this one.
exe_debug = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FRIDAY-Debug",
    debug=True,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    exe_debug,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FRIDAY",
)
