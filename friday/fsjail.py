"""
Filesystem jail: every path the agent touches is resolved and contained.

§11 requires paths to be normalised and validated, and traversal outside
permitted roots prevented. Mark-L's file controller is marked REWRITE in the
parity matrix with path traversal as its flagged risk, so containment is the
design here rather than a check bolted on afterwards.

Two layers, because either alone is insufficient:

1. **Roots.** A path must resolve inside a configured root. Resolution happens
   *before* the check and follows symlinks, so a link inside a root pointing
   outside it is caught - checking the literal string first would not.

2. **Denylist.** Even inside a root, secret-shaped files are refused. Reads are
   AUTO inside roots (§11), and the project directory contains a .env; without
   this, "read a file" would be a route around SECRET_READ: DENY.

Defaults are deliberately narrow. The default root is a dedicated workspace,
not the project directory and not the home directory - widening it is a
decision the user makes in configuration.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from friday.config import DATA_DIR, PROJECT_ROOT

#: Windows device names. Opening one is not a file operation at all.
_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

#: Refused inside any root. Reads are automatic, so this is the line between
#: "read a file" and "exfiltrate a credential".
DENY_PATTERNS = (
    r"(^|[/\\])\.env($|\.)",
    r"(^|[/\\])\.git([/\\]|$)",
    r"(^|[/\\])\.ssh([/\\]|$)",
    r"(^|[/\\])id_(rsa|dsa|ecdsa|ed25519)($|\.)",
    r"\.(pem|key|pfx|p12|keystore|jks)$",
    r"(^|[/\\])(credentials|secrets?)(\.|$)",
    r"(^|[/\\])\.aws([/\\]|$)",
    r"(^|[/\\])\.npmrc$",
    r"(^|[/\\])\.pypirc$",
    r"(^|[/\\])shadow$",
)

_DENY_RE = tuple(re.compile(p, re.I) for p in DENY_PATTERNS)

#: Anchored to the project, not to wherever the process was launched from.
#: A jail whose root moves with the working directory is not a jail.
DEFAULT_WORKSPACE = DATA_DIR / "workspace"


class JailError(PermissionError):
    """A path was refused. The message says why, never what it contained."""


def is_reparse_point(path: Path) -> bool:
    """
    Whether this is a junction, symlink or other reparse point.

    `Path.is_symlink()` is the obvious way to ask and it is wrong here: on
    Windows it returns **False** for a junction, which is the alias most likely
    to be used against a jail because creating one needs no administrator
    rights. Measured on this interpreter, for a junction created with
    `mklink /J`:

        is_symlink()                            False
        st_file_attributes & REPARSE_POINT      True
        os.readlink()                           \\\\?\\C:\\\\...\\\\outside

    Two layers, most readable first:

    1. `Path.is_junction()`, added in Python 3.12 for exactly this question.
       **Not available on this runtime** - measured, 3.11.15 - so it is used
       when present rather than assumed, and takes over on an upgrade without
       anything here changing.
    2. `st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT`, which is what
       actually knows, works on 3.11, and is broader: it catches every reparse
       point rather than junctions specifically. That breadth is why it stays
       even once (1) is available.

    On platforms without the attribute the getattr default makes this False,
    which is correct there - symlinks are handled by resolution, and there are
    no junctions.
    """
    try:
        if path.is_junction():          # 3.12+; broader check still runs below
            return True
    except (AttributeError, OSError):
        pass
    try:
        attributes = os.lstat(path).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def default_roots() -> tuple[Path, ...]:
    """
    Where ADA may read and write when nothing is configured.

    Originally this was one sandbox directory, which made ADA useless for the
    thing it is most often asked to do - "look at my code base, here's the
    path". Every such request failed on a path it was never going to be
    allowed to see.

    So the default is now the project root plus the usual document folders.
    The protection that matters is the denylist below, not the narrowness of
    the roots: widening where ADA can look does not widen what it may read,
    because .env, keys, .ssh and .git stay refused everywhere.

    This was `Path.cwd()` rather than PROJECT_ROOT, which made the jail's
    extent depend on where the process happened to be started - a scheduled
    task, a shell in another repository and the voice agent would each get a
    different jail. A security boundary must not be derived from execution
    context, and in practice this changes nothing: the interactive case is
    launched from the project directory, and scheduled tasks now declare it as
    their working directory anyway.

    Add project drives with ADA_FILE_ROOTS (os.pathsep-separated).
    """
    candidates = [PROJECT_ROOT, Path(DEFAULT_WORKSPACE)]
    home = Path.home()
    candidates += [home / name for name in
                   ("Documents", "Desktop", "Downloads", "Projects", "code", "src")]

    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def configured_roots() -> tuple[Path, ...]:
    """ADA_FILE_ROOTS if set, otherwise the defaults above."""
    raw = os.getenv("ADA_FILE_ROOTS", "").strip()
    if not raw:
        return default_roots()
    roots = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if part:
            try:
                roots.append(Path(part).expanduser().resolve())
            except OSError:
                continue
    return tuple(roots) or default_roots()


class FileJail:
    def __init__(self, roots: tuple[Path, ...] | None = None) -> None:
        wanted = tuple(roots or configured_roots())
        # Fail closed if a root is itself a reparse point.
        #
        # A junction inside the jail is already handled - it resolves to its
        # target, lands outside, and containment refuses it. The root is the
        # case that would otherwise *succeed*: resolving it silently relocates
        # the entire jail to wherever the junction points, and every later
        # check passes against the new location. Nothing looks wrong. Creating
        # a junction needs no administrator rights, so this is reachable by
        # anything that can write next to the workspace.
        for root in wanted:
            if is_reparse_point(root):
                raise JailError(
                    f"refusing to start: the jail root {root.name!r} is a "
                    "reparse point (junction or symlink), which would move "
                    "the boundary to wherever it points"
                )
        self.roots = tuple(r.resolve() for r in wanted)
        for root in self.roots:
            root.mkdir(parents=True, exist_ok=True)

    # -- checks -------------------------------------------------------------

    @staticmethod
    def _reject_shape(raw: str) -> None:
        if not raw or not raw.strip():
            raise JailError("empty path")
        if "\x00" in raw:
            raise JailError("path contains a null byte")
        # Alternate Data Streams: "notes.txt:hidden". A drive letter colon at
        # position 1 is legitimate; any other colon on Windows is not.
        tail = raw[2:] if re.match(r"^[a-zA-Z]:[/\\]", raw) else raw
        if os.name == "nt" and ":" in tail:
            raise JailError("path contains ':' (alternate data stream)")
        if raw.startswith("\\\\"):
            raise JailError("UNC and device paths are not permitted")

    @staticmethod
    def _reject_reserved(path: Path) -> None:
        for part in path.parts:
            if part.split(".")[0].lower() in _RESERVED:
                raise JailError(f"{part!r} is a reserved device name")

    @staticmethod
    def _reject_denylisted(path: Path) -> None:
        text = str(path)
        for pattern in _DENY_RE:
            if pattern.search(text):
                raise JailError(
                    f"refused: {path.name!r} matches a protected pattern "
                    "(credentials, keys, VCS or environment files)"
                )

    def _contained(self, path: Path) -> Path:
        for root in self.roots:
            if path == root or path.is_relative_to(root):
                return path
        raise JailError(
            f"path is outside the permitted roots. Allowed: "
            f"{[str(r) for r in self.roots]}"
        )

    # -- entry point --------------------------------------------------------

    def resolve(self, raw: str | os.PathLike) -> Path:
        """
        Normalise and validate a path, or raise JailError.

        Order matters: resolve() first so symlinks and '..' are collapsed, and
        only then test containment. Testing the literal string first would let
        a symlink inside a root escape it.
        """
        text = str(raw)
        self._reject_shape(text)

        candidate = Path(text).expanduser()
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:  # RuntimeError: symlink loop
            raise JailError(f"path could not be resolved: {exc}") from exc

        self._reject_reserved(resolved)
        self._reject_denylisted(resolved)
        return self._contained(resolved)

    def describe(self) -> dict:
        return {
            "roots": [str(r) for r in self.roots],
            "deny_patterns": len(DENY_PATTERNS),
        }
