"""
Build FRIDAY.exe - the one reproducible command for the Windows package.

    .venv-build\\Scripts\\python.exe scripts\\build_windows.py
    .venv-build\\Scripts\\python.exe scripts\\build_windows.py --clean --skip-smoke

Never from the live `.venv` (the running agent imports from it, and
PyInstaller is deliberately not installed there). Create the build venv
once with:

    set UV_PROJECT_ENVIRONMENT=.venv-build
    uv sync --extra dev --extra documents --extra web --python 3.11
    uv pip install --python .venv-build\\Scripts\\python.exe pyinstaller

(`uv sync` makes the environment match the lock exactly, so a re-sync
after a dependency change removes PyInstaller; repeat the last line.)

What it does, in order:

  1. refuses to run from the live venv or off Windows;
  2. records what is being built (version from pyproject, commit, dirty)
     into build/staging/build_manifest.json - the packaged
     `friday.build_identity` reads it, since a bundle has no git;
  3. sets aside the user's state from a previous build
     (dist/FRIDAY/.env, _internal/data, _internal/logs, _internal/vault)
     because PyInstaller replaces dist/FRIDAY wholesale;
  4. runs PyInstaller on packaging/FRIDAY.spec (onedir);
  5. restores the preserved state, copies .env.example beside the exe,
     signs when a certificate is configured (and says NOT_CONFIGURED
     otherwise - never pretends), writes SHA256SUMS.txt;
  6. smoke-tests the artifact itself: `FRIDAY.exe --version` must answer
     and `FRIDAY.exe --doctor` must not FAIL.

Exit code is non-zero when any step fails; the artifact path is the last
line printed on success.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "FRIDAY.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
STAGING = BUILD / "staging"
WORK = BUILD / "pyinstaller"
APP = DIST / "FRIDAY"
EXE = APP / "FRIDAY.exe"

#: State a rebuild must not destroy (EXE-11). Relative to dist/FRIDAY.
#: A packaged build keeps its state under %LOCALAPPDATA%\FRIDAY, which a
#: rebuild never touches; these are the portable-mode layout (state beside
#: the executable) and the layout of builds before 2026-09-15, which the
#: launcher migrates on first start.
PRESERVE = (".env", "PORTABLE_MODE", "config", "data", "logs", "vault", "skills",
            "_internal/data", "_internal/logs", "_internal/vault")


def say(message: str) -> None:
    print(f"[build] {message}", flush=True)


def fail(message: str, code: int = 1) -> int:
    print(f"[build] ERROR: {message}", file=sys.stderr, flush=True)
    return code


def git(*arguments: str) -> str:
    try:
        return subprocess.run(["git", *arguments], capture_output=True, text=True,
                              timeout=10, cwd=str(ROOT)).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preflight() -> str | None:
    if sys.platform != "win32":
        return "FRIDAY.exe is a Windows artifact; build it on Windows"
    live = (ROOT / ".venv").resolve()
    if Path(sys.executable).resolve().is_relative_to(live):
        return ("this is the live .venv; build from .venv-build "
                "(see the module docstring)")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        return "PyInstaller is not installed in this interpreter"
    if not SPEC.is_file():
        return f"spec missing: {SPEC}"
    for required in ("ui/index.html", "server.py", "agent_friday.py",
                     "scripts/run_ui.py", "friday_exe.py", ".env.example"):
        if not (ROOT / required).exists():
            return f"required input missing: {required}"
    return None


def write_manifest() -> dict:
    with open(ROOT / "pyproject.toml", "rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    import PyInstaller

    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain"))
    manifest = {
        "product": "FRIDAY",
        "version": version,
        "commit": commit or "unknown",
        "commit_short": (commit[:7] if commit else "unknown"),
        "dirty": dirty,
        "branch": git("branch", "--show-current") or "unknown",
        "channel": "dev" if dirty or not commit else "stable",
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "pyinstaller": PyInstaller.__version__,
        "layout": "onedir",
        "builder": "scripts/build_windows.py",
        "signing_status": "NOT_CONFIGURED",
    }
    STAGING.mkdir(parents=True, exist_ok=True)
    (STAGING / "build_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def set_aside_state() -> Path | None:
    """Move the previous build's user state out of PyInstaller's way."""
    if not APP.is_dir():
        return None
    keep = BUILD / f"preserved-{time.strftime('%Y%m%d-%H%M%S')}"
    moved = 0
    for relative in PRESERVE:
        source = APP / relative
        if not source.exists():
            continue
        target = keep / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved += 1
    if moved:
        say(f"set aside {moved} state path(s) in {keep}")
        return keep
    return None


def restore_state(keep: Path | None) -> None:
    """Put the preserved state back; preserved files win over shipped ones."""
    if keep is None:
        return
    for relative in PRESERVE:
        source = keep / relative
        if not source.exists():
            continue
        target = APP / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    shutil.rmtree(keep, ignore_errors=True)
    say("restored the previous build's state (.env, data, logs, vault)")


def run_pyinstaller(clean: bool) -> int:
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm",
               "--distpath", str(DIST), "--workpath", str(WORK),
               "--log-level", "WARN", str(SPEC)]
    if clean:
        command.insert(3, "--clean")
    env = dict(os.environ, FRIDAY_BUILD_STAGING=str(STAGING), PYTHONUTF8="1")
    say("running PyInstaller (several minutes)")
    started = time.monotonic()
    result = subprocess.run(command, cwd=str(ROOT), env=env)
    say(f"PyInstaller exit {result.returncode} after {time.monotonic() - started:.0f}s")
    return result.returncode


def sign(manifest: dict) -> str:
    """
    Sign when a certificate is configured; report NOT_CONFIGURED otherwise.

    FRIDAY_SIGN_THUMBPRINT selects a certificate in the user's store
    (signtool /sha1), FRIDAY_SIGN_PFX (+ FRIDAY_SIGN_PFX_PASSWORD) a file.
    SHA-256 digest, RFC 3161 timestamp. Verified afterwards with
    `signtool verify /pa`; a failed verification is reported, not hidden.
    """
    thumbprint = os.getenv("FRIDAY_SIGN_THUMBPRINT")
    pfx = os.getenv("FRIDAY_SIGN_PFX")
    if not thumbprint and not pfx:
        return "NOT_CONFIGURED"
    signtool = shutil.which("signtool")
    if not signtool:
        return "FAILED: signtool not on PATH"
    timestamp = os.getenv("FRIDAY_SIGN_TIMESTAMP_URL", "http://timestamp.digicert.com")
    command = [signtool, "sign", "/fd", "SHA256", "/td", "SHA256", "/tr", timestamp]
    if thumbprint:
        command += ["/sha1", thumbprint]
    else:
        command += ["/f", pfx]
        if os.getenv("FRIDAY_SIGN_PFX_PASSWORD"):
            command += ["/p", os.environ["FRIDAY_SIGN_PFX_PASSWORD"]]
    command.append(str(EXE))
    signed = subprocess.run(command, capture_output=True, text=True)
    if signed.returncode != 0:
        return f"FAILED: {signed.stderr.strip()[:200] or signed.stdout.strip()[:200]}"
    verified = subprocess.run([signtool, "verify", "/pa", str(EXE)],
                              capture_output=True, text=True)
    if verified.returncode != 0:
        return f"FAILED: signature did not verify: {verified.stderr.strip()[:200]}"
    return "SIGNED"


def finish(manifest: dict) -> None:
    shutil.copy2(ROOT / ".env.example", APP / ".env.example")
    manifest["signing_status"] = sign(manifest)
    say(f"SIGNING_STATUS={manifest['signing_status']}")
    (APP / "_internal" / "build_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    sums = []
    for path in (EXE, APP / "_internal" / "build_manifest.json"):
        sums.append(f"{sha256(path)}  {path.relative_to(APP).as_posix()}")
    (APP / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")


def smoke() -> int:
    """Test the artifact, not the source tree it came from."""
    say("smoke: FRIDAY.exe --version")
    version = subprocess.run([str(EXE), "--version"], capture_output=True, text=True,
                             timeout=120, cwd=str(APP))
    print(version.stdout.rstrip(), flush=True)
    if version.returncode != 0 or "FRIDAY" not in version.stdout:
        print(version.stderr, file=sys.stderr)
        return fail("--version did not answer", 3)
    say("smoke: FRIDAY.exe --doctor")
    doctor = subprocess.run([str(EXE), "--doctor"], capture_output=True, text=True,
                            timeout=300, cwd=str(APP))
    print(doctor.stdout.rstrip(), flush=True)
    if doctor.returncode == 2:
        print(doctor.stderr, file=sys.stderr)
        return fail("--doctor reports FAIL", 3)
    return 0


def folder_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clean", action="store_true",
                        help="also clear PyInstaller's cache (slower, fully fresh)")
    parser.add_argument("--skip-smoke", action="store_true",
                        help="do not run the built executable afterwards")
    parser.add_argument("--no-zip", action="store_true",
                        help="do not produce dist/FRIDAY-<version>-win64.zip")
    options = parser.parse_args()

    problem = preflight()
    if problem:
        return fail(problem, 2)

    manifest = write_manifest()
    say(f"building FRIDAY {manifest['version']} commit {manifest['commit_short']}"
        f"{'+dirty' if manifest['dirty'] else ''} with Python {manifest['python']}, "
        f"PyInstaller {manifest['pyinstaller']}")

    keep = set_aside_state()
    code = 1
    try:
        code = run_pyinstaller(options.clean)
    finally:
        # Even a failed build must not eat the user's data.
        APP.mkdir(parents=True, exist_ok=True)
        restore_state(keep)
    if code != 0:
        return fail(f"PyInstaller failed with exit {code}", code)
    if not EXE.is_file():
        return fail(f"PyInstaller finished but {EXE} does not exist", 1)

    finish(manifest)
    if not options.skip_smoke:
        smoke_code = smoke()
        if smoke_code:
            return smoke_code

    say(f"size {folder_size(APP) / (1 << 20):.0f} MB in {APP}")
    say(f"checksums {APP / 'SHA256SUMS.txt'}")
    if not options.no_zip:
        archive = make_distribution(manifest["version"])
        say(f"distribution {archive} ({archive.stat().st_size / (1 << 20):.0f} MB), "
            f"digest beside it")
    print(str(EXE), flush=True)
    return 0


def make_distribution(version: str) -> Path:
    """
    The shippable artifact: a zip of dist/FRIDAY that unpacks to the same
    folder on another machine (no installer framework is available on this
    host; the onedir layout needs none). User state is never inside it - it
    lives under %LOCALAPPDATA%\\FRIDAY on the target - and the zip carries
    its own digest so a download can be checked before it is unpacked.
    """
    stem = DIST / f"FRIDAY-{version}-win64"
    archive = Path(shutil.make_archive(str(stem), "zip", root_dir=str(DIST), base_dir="FRIDAY"))
    digest = sha256(archive)
    (archive.with_suffix(".zip.sha256")).write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


if __name__ == "__main__":
    raise SystemExit(main())
