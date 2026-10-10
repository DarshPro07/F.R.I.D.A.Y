"""CAP-MKT-001: where capabilities come from, behind one interface.

skills.rest, GitHub, NVIDIA skills, MCP registries, plugins, local skills and
URLs from the person are all a `CapabilitySource`: it reports its health,
lists catalogue metadata (`CatalogEntry`, metadata only), and fetches ONE
entry into a directory it is given. Nothing here activates anything.

`harvest` is the only path from a source to a candidate. It copies each entry
into a quarantine directory, pins it by content hash, and runs the CAP-MKT-002
SkillSpector gate (`forge.scan_import`) on the quarantined copy. A candidate is
`admitted` only while a stored clean scan still matches the bytes on disk
(`forge.scan_admits`); a blocked scan quarantines it, a scan that did not
happen holds it. The network sources are stubs that report UNAVAILABLE until a
transport is attached, so an absent upstream never breaks anything.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from friday import forge

logger = logging.getLogger(__name__)

AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"

#: Candidate states. Only SCANNED_CLEAN can ever be admitted.
SCANNED_CLEAN = "SCANNED_CLEAN"
QUARANTINED = "QUARANTINED"
HELD = "HELD"

#: A catalogue name becomes a directory name, so it is data until it matches.
_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9_-])?$")

#: Names Windows reserves as devices, with or without an extension.
_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                       *(f"lpt{i}" for i in range(1, 10))})

NO_NETWORK = "no network transport is attached in this slice"


def _unsafe_name(name: str) -> str:
    """Why `name` cannot be a directory name, or "" when it can."""
    if not _NAME_RE.match(name):
        return "the name is not a safe directory name"
    if name.split(".", 1)[0].lower() in _RESERVED:
        return "the name is a reserved device name"
    return ""


def is_link(path: Path) -> bool:
    """A symlink, or on Windows any reparse point (a junction is one, and
    `Path.is_symlink` is False for it). Never follows the link."""
    try:
        st = os.lstat(path)
    except OSError:
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def links_in(directory: Path) -> list[str]:
    """Relative paths of every link, reparse point or special file under
    `directory`. A link is never descended into."""
    found: list[str] = []
    root = Path(directory)
    stack = [root]
    while stack:
        current = stack.pop()
        with os.scandir(current) as it:
            for item in it:
                path = Path(item.path)
                if is_link(path):
                    found.append(path.relative_to(root).as_posix())
                elif item.is_dir(follow_symlinks=False):
                    stack.append(path)
                elif not item.is_file(follow_symlinks=False):
                    found.append(path.relative_to(root).as_posix())
    return sorted(found)


def _copy_tree_without_links(origin: Path, destination: Path) -> None:
    """Copy regular files and directories only. A link anywhere in the tree is
    refused, never followed: what it points at is not part of the candidate."""
    destination.mkdir(parents=True)
    with os.scandir(origin) as it:
        for item in sorted(it, key=lambda i: i.name):
            path = Path(item.path)
            if is_link(path):
                raise OSError(f"{path} is a link; links are never copied into quarantine")
            if item.is_dir(follow_symlinks=False):
                _copy_tree_without_links(path, destination / item.name)
            elif item.is_file(follow_symlinks=False):
                shutil.copy2(path, destination / item.name)
            else:
                raise OSError(f"{path} is not a regular file or directory")


class SourceUnavailable(RuntimeError):
    """The source cannot be read right now; nothing was listed or fetched."""


@dataclass(frozen=True)
class SourceHealth:
    state: str
    detail: str = ""


@dataclass(frozen=True)
class CatalogEntry:
    """Catalogue metadata only. Everything in it is untrusted."""
    name: str
    source: str
    category: str = ""
    popularity: int = 0
    ref: str = ""


class CapabilitySource:
    """One place capabilities can be discovered. Subclasses set `kind`."""

    kind = ""

    def health(self) -> SourceHealth:
        raise NotImplementedError

    def catalog(self) -> list[CatalogEntry]:
        """Metadata for every entry. Raises SourceUnavailable when unreadable."""
        raise NotImplementedError

    def fetch(self, entry: CatalogEntry, destination: Path) -> None:
        """Materialise one entry at `destination` (which must not exist yet)."""
        raise NotImplementedError


class LocalDirectorySource(CapabilitySource):
    """A directory whose sub-directories are skills (the person's own folder)."""

    kind = "local"

    def __init__(self, root: Path):
        self.root = Path(root)

    def health(self) -> SourceHealth:
        if self.root.is_dir():
            return SourceHealth(AVAILABLE, str(self.root))
        return SourceHealth(UNAVAILABLE, f"{self.root} is not a directory")

    def catalog(self) -> list[CatalogEntry]:
        if not self.root.is_dir():
            raise SourceUnavailable(f"{self.root} is not a directory")
        return [CatalogEntry(name=p.name, source=self.kind, category="local",
                             ref=str(p))
                for p in sorted(self.root.iterdir())
                if p.is_dir() and not is_link(p) and not p.name.startswith(".")]

    def fetch(self, entry: CatalogEntry, destination: Path) -> None:
        origin = self.root / entry.name
        if is_link(origin) or not origin.is_dir():
            raise OSError(f"{origin} is not a skill directory")
        # Links (symlinks and Windows junctions) are refused, never followed.
        _copy_tree_without_links(origin, destination)


class _NetworkSource(CapabilitySource):
    """A source that needs a transport this slice does not have."""

    def health(self) -> SourceHealth:
        return SourceHealth(UNAVAILABLE, f"{self.kind}: {NO_NETWORK}")

    def catalog(self) -> list[CatalogEntry]:
        raise SourceUnavailable(self.health().detail)

    def fetch(self, entry: CatalogEntry, destination: Path) -> None:
        raise SourceUnavailable(self.health().detail)


class SkillsRestSource(_NetworkSource):
    kind = "skills.rest"


class GitHubSource(_NetworkSource):
    kind = "github"


class NvidiaSkillsSource(_NetworkSource):
    kind = "nvidia-skills"


class McpRegistrySource(_NetworkSource):
    kind = "mcp-registry"


class PluginSource(_NetworkSource):
    kind = "plugin"


class UrlSource(_NetworkSource):
    """A URL the person supplied."""

    kind = "url"


@dataclass
class Candidate:
    entry: CatalogEntry
    directory: Path
    state: str
    scan: dict
    #: Digest of the quarantined bytes before they were scanned.
    source_sha256: str

    @property
    def admitted(self) -> bool:
        """True only while a clean scan still matches the bytes on disk."""
        return self.state == SCANNED_CLEAN and forge.scan_admits(self.scan, self.directory)


@dataclass
class HarvestResult:
    source: str
    health: SourceHealth
    candidates: list[Candidate] = field(default_factory=list)
    #: (entry name, reason) for each entry that never reached a scan.
    refused: list[tuple[str, str]] = field(default_factory=list)

    def admitted(self) -> list[Candidate]:
        return [c for c in self.candidates if c.admitted]


def harvest(source: CapabilitySource, quarantine_root: Path, *,
            scanner: Callable[[Path], forge.ScanReport] = forge.scan_import) -> HarvestResult:
    """Fetch every entry of `source` into quarantine and scan it. Activates nothing."""
    health = source.health()
    result = HarvestResult(source=source.kind, health=health)
    if health.state != AVAILABLE:
        return result
    try:
        entries = source.catalog()
    except SourceUnavailable as exc:
        result.health = SourceHealth(UNAVAILABLE, str(exc))
        return result

    base = Path(quarantine_root) / source.kind
    taken: set[str] = set()
    for entry in entries:
        why = _unsafe_name(entry.name)
        if why:
            result.refused.append((entry.name, why))
            continue
        if entry.name.lower() in taken:
            # Case-insensitive file systems would map both onto one directory.
            result.refused.append((entry.name, "the name collides with another entry"))
            continue
        taken.add(entry.name.lower())
        destination = base / entry.name
        if destination.exists():
            shutil.rmtree(destination)
        base.mkdir(parents=True, exist_ok=True)
        try:
            source.fetch(entry, destination)
        except (OSError, SourceUnavailable) as exc:
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            logger.warning("capability source %s: fetching %s failed: %s",
                           source.kind, entry.name, exc)
            result.refused.append((entry.name, f"fetch failed: {exc}"))
            continue
        linked = links_in(destination)
        if linked:
            # Whatever a link points at is outside the pinned bytes.
            shutil.rmtree(destination, ignore_errors=True)
            logger.warning("capability source %s: %s holds links, refused",
                           source.kind, entry.name)
            result.refused.append((entry.name, f"the tree holds links: {', '.join(linked[:3])}"))
            continue
        pinned = forge.tree_digest(destination)
        report = scanner(destination)
        scan = report.to_dict()
        if report.status == forge.SCAN_BLOCKED:
            state = QUARANTINED
        elif report.clean and scan.get("target_sha256") == pinned:
            state = SCANNED_CLEAN
        else:
            # Not scanned, scan failed, or the bytes moved during the scan.
            state = HELD
        result.candidates.append(Candidate(entry=entry, directory=destination,
                                           state=state, scan=scan,
                                           source_sha256=pinned))
    return result
