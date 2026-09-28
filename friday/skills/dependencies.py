"""
What a skill depends on, and which code change invalidates it.

A skill declares its dependencies in `metadata.friday.depends_on`:

    friday/launcher.py          a file (relative to the repository)
    tests/test_launcher*.py     a glob
    pkg:pyinstaller             an installed package (its version is the fingerprint)
    skill:friday-windows-exe    another skill (its content hash)
    api:livekit-agents          something we cannot fingerprint; recorded, never judged

Each dependency gets a fingerprint when the skill is verified. A later
change to a fingerprinted file marks the skill NEEDS_REVALIDATION - not
BROKEN, because a change is not a contradiction until a test says so. A
README typo elsewhere in the repository never touches it: revalidation
scales with the skill's declared surface, not the repository.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Iterable

from friday.skills import contracts as K
from friday.skills import files as F

FILE, GLOB, PACKAGE, SKILL, API = "file", "glob", "package", "skill", "api"
KINDS = (FILE, GLOB, PACKAGE, SKILL, API)

MISSING = "missing"


def classify(target: str) -> tuple[str, str]:
    """(kind, target) for one `depends_on` entry."""
    text = str(target).strip()
    for prefix, kind in (("pkg:", PACKAGE), ("package:", PACKAGE), ("skill:", SKILL), ("api:", API)):
        if text.lower().startswith(prefix):
            return kind, text[len(prefix):].strip()
    if any(ch in text for ch in "*?["):
        return GLOB, text.replace("\\", "/")
    return FILE, text.replace("\\", "/")


def declared(frontmatter: dict) -> list[tuple[str, str]]:
    return [classify(entry) for entry in F.as_list(F.friday_metadata(frontmatter).get("depends_on"))]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _glob_to_regex(pattern: str) -> re.Pattern:
    """fnmatch with `**` meaning any depth, anchored to the whole path."""
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(kind: str, target: str, changed_path: str) -> bool:
    path = changed_path.replace("\\", "/").lstrip("./")
    if kind == FILE:
        return path == target.lstrip("./")
    if kind == GLOB:
        # The translation, not fnmatch: fnmatch lets `*` cross a `/`, so
        # `friday/*.py` would claim `friday/skills/router.py`.
        return bool(_glob_to_regex(target.lstrip("./")).match(path))
    return False


def git_identity(repo_root: Path, timeout: float = 10.0) -> tuple[str, str]:
    """(codebase id, short HEAD): the origin URL when there is one, else the
    repository's top-level path; and the commit. Empty without git."""
    def ask(*args: str) -> str:
        try:
            done = subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout,
                                  cwd=str(repo_root))
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    commit = ask("rev-parse", "--short", "HEAD")
    if not commit:
        return "", ""
    identity = ask("config", "--get", "remote.origin.url") or ask("rev-parse", "--show-toplevel")
    return identity, commit


class SkillDependencyGraph:
    """Fingerprints per skill, and the change -> skill mapping."""

    def __init__(self, registry, repo_root: Path | None = None,
                 skill_hash: "callable | None" = None) -> None:
        if repo_root is None:
            from friday.config import PROJECT_ROOT

            repo_root = PROJECT_ROOT
        self.registry = registry
        self.repo_root = Path(repo_root)
        #: name -> content hash of another skill, for skill: dependencies.
        self._skill_hash = skill_hash or (lambda name: (getattr(registry.get(name), "content_hash", "") or "unknown"))

    # -- fingerprints ----------------------------------------------------------

    def fingerprint(self, kind: str, target: str) -> str:
        if kind == FILE:
            path = self.repo_root / target
            return _sha256_file(path) if path.is_file() else MISSING
        if kind == GLOB:
            digest = hashlib.sha256()
            found = sorted(p for p in self.repo_root.glob(target) if p.is_file())
            if not found:
                return MISSING
            for path in found:
                digest.update(path.relative_to(self.repo_root).as_posix().encode("utf-8"))
                digest.update(b"\0")
                digest.update(_sha256_file(path).encode("ascii"))
            return digest.hexdigest()
        if kind == PACKAGE:
            try:
                from importlib.metadata import version

                return version(target)
            except Exception:                               # noqa: BLE001
                return MISSING
        if kind == SKILL:
            return self._skill_hash(target)
        return ""                                            # api: recorded, not judged

    def index(self, skill: F.SkillFiles, frontmatter: dict | None = None) -> list[tuple[str, str, str]]:
        """Fingerprint every declared dependency now and store the rows."""
        if frontmatter is None:
            frontmatter, _ = skill.read()
        entries = [(kind, target, self.fingerprint(kind, target)) for kind, target in declared(frontmatter)]
        self.registry.set_dependencies(skill.name, entries)
        return entries

    def check(self, name: str) -> dict:
        """Recompute fingerprints against the stored ones."""
        changed, missing = [], []
        for row in self.registry.dependencies(name):
            now = self.fingerprint(row["kind"], row["target"])
            if row["kind"] == API:
                continue
            if now == MISSING and row["fingerprint"] != MISSING:
                missing.append(row["target"])
            elif now != row["fingerprint"]:
                changed.append(row["target"])
        return {"skill": name, "fresh": not changed and not missing, "changed": changed, "missing": missing}

    def refresh(self, name: str) -> None:
        """After a successful revalidation: the current fingerprints are the baseline."""
        for row in self.registry.dependencies(name):
            self.registry.update_fingerprint(name, row["kind"], row["target"],
                                             self.fingerprint(row["kind"], row["target"]))

    # -- changes ---------------------------------------------------------------

    def affected_by(self, changed_paths: Iterable[str]) -> dict[str, list[str]]:
        """skill -> the changed paths that touch its declared surface."""
        paths = [str(p) for p in changed_paths if str(p).strip()]
        hits: dict[str, list[str]] = {}
        for row in self.registry.all_dependencies():
            if row["kind"] not in (FILE, GLOB):
                continue
            for path in paths:
                if matches(row["kind"], row["target"], path):
                    hits.setdefault(row["name"], []).append(path)
        return {name: sorted(set(found)) for name, found in hits.items()}

    def revalidate_after_change(self, changed_paths: Iterable[str], *, actor: str = "git") -> list[dict]:
        """Mark every ACTIVE skill whose surface changed NEEDS_REVALIDATION."""
        marked = []
        for name, paths in self.affected_by(changed_paths).items():
            record = self.registry.get(name)
            if record is None or record.state != K.ACTIVE:
                continue
            self.registry.transition(name, K.NEEDS_REVALIDATION, actor=actor,
                                     reason="dependency changed: " + ", ".join(paths[:5]))
            marked.append({"skill": name, "changed": paths})
        return marked

    def identity(self, timeout: float = 10.0) -> tuple[str, str]:
        """(codebase id, short HEAD) for the fingerprint: the origin URL or
        the top-level path, and the commit. Empty strings without git."""
        return git_identity(self.repo_root, timeout=timeout)

    def changed_paths_since(self, commit: str = "HEAD", *, include_uncommitted: bool = True,
                            timeout: float = 20.0) -> list[str]:
        """Paths git says changed since `commit` (plus the working tree)."""
        found: set[str] = set()
        try:
            diff = subprocess.run(["git", "diff", "--name-only", commit, "--"], capture_output=True,
                                  text=True, timeout=timeout, cwd=str(self.repo_root))
            found.update(line.strip() for line in diff.stdout.splitlines() if line.strip())
            if include_uncommitted:
                status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                                        capture_output=True, text=True, timeout=timeout,
                                        cwd=str(self.repo_root))
                for line in status.stdout.splitlines():
                    if len(line) > 3:
                        found.add(line[3:].strip().split(" -> ")[-1])
        except (OSError, subprocess.SubprocessError):
            return sorted(found)
        return sorted(found)
