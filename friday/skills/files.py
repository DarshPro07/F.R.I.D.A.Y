"""
SKILL.md in Hermes's format: read, write, hash, and the directories skills
live in.

A skill is a directory whose `SKILL.md` starts with YAML frontmatter.
Everything FRIDAY adds goes under `metadata.friday`, which Hermes ignores,
so a FRIDAY skill dropped into `HERMES_HOME/skills/` or a tap works
unchanged. Files are written atomically (tmp + replace), the way Hermes's
`skill_manage` does it.

Tiers are directories, all in the same `<dir>/<name>/SKILL.md` layout:

    BUILT_IN         friday/skills/builtin/          shipped, read-only
    LOCAL_USER       STATE_ROOT/skills/user/         the owner's private procedures
    PROJECT          <repo>/skills/                  git-tracked with the code; a tap root
    SHARED_PRIVATE   $FRIDAY_SKILLS_PRIVATE_DIR      a checkout of the private tap's skills/
    PUBLISHED_PUBLIC $FRIDAY_SKILLS_PUBLIC_DIR       a checkout of the public tap's skills/
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from friday.skills import contracts as K

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)

SKILL_FILE = "SKILL.md"

#: Hermes skips these when discovering skills in a tap or a directory.
_SKIPPED_PREFIXES = (".", "_")
_SKIPPED_NAMES = {"__pycache__", "node_modules"}


# ---------------------------------------------------------------------------
# SKILL.md
# ---------------------------------------------------------------------------

def parse_skill_md(text: str) -> tuple[dict, str]:
    """(frontmatter, body). No frontmatter -> ({}, text). Invalid YAML raises."""
    match = FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, text or ""
    try:
        loaded = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise K.SkillError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise K.SkillError("frontmatter must be a mapping")
    return loaded, match.group(2)


def render_skill_md(frontmatter: dict, body: str) -> str:
    head = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True,
                          default_flow_style=None, width=100).rstrip("\n")
    return f"---\n{head}\n---\n\n{(body or '').strip(chr(10))}\n"


def friday_metadata(frontmatter: dict) -> dict:
    metadata = frontmatter.get("metadata") if isinstance(frontmatter.get("metadata"), dict) else {}
    found = metadata.get("friday") if isinstance(metadata, dict) else None
    return dict(found) if isinstance(found, dict) else {}


def hermes_metadata(frontmatter: dict) -> dict:
    metadata = frontmatter.get("metadata") if isinstance(frontmatter.get("metadata"), dict) else {}
    found = metadata.get("hermes") if isinstance(metadata, dict) else None
    return dict(found) if isinstance(found, dict) else {}


def with_friday_metadata(frontmatter: dict, **updates) -> dict:
    """A copy of the frontmatter with `metadata.friday` keys set/replaced."""
    import copy

    result = copy.deepcopy(frontmatter)
    metadata = result.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = result["metadata"] = {}
    friday = metadata.setdefault("friday", {})
    if not isinstance(friday, dict):
        friday = metadata["friday"] = {}
    for key, value in updates.items():
        if value is None:
            friday.pop(key, None)
        else:
            friday[key] = value
    return result


def as_list(value) -> list[str]:
    """Frontmatter lists arrive as lists, strings or nothing."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


# ---------------------------------------------------------------------------
# A skill on disk
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillFiles:
    dir: Path

    @property
    def name(self) -> str:
        return self.dir.name

    @property
    def skill_md(self) -> Path:
        return self.dir / SKILL_FILE

    def exists(self) -> bool:
        return self.skill_md.is_file()

    def text(self) -> str:
        return self.skill_md.read_text(encoding="utf-8", errors="replace")

    def read(self) -> tuple[dict, str]:
        return parse_skill_md(self.text())

    def write(self, frontmatter: dict, body: str) -> Path:
        return self.write_text(render_skill_md(frontmatter, body))

    def write_text(self, text: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.skill_md.with_name(SKILL_FILE + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.skill_md)
        return self.skill_md

    def write_file(self, relative: str, content: str | bytes) -> Path:
        """A supporting file under one of Hermes's allowed subdirectories."""
        parts = Path(relative).parts
        if not parts or parts[0] not in K.ALLOWED_SUBDIRS or ".." in parts:
            raise K.SkillError(f"supporting files live under {', '.join(K.ALLOWED_SUBDIRS)}: {relative!r}")
        target = self.dir.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def files(self) -> list[Path]:
        """Every file of the skill, SKILL.md first, sorted, hidden ones skipped."""
        found: list[Path] = []
        if not self.dir.is_dir():
            return found
        for root, dirs, names in os.walk(self.dir):
            dirs[:] = sorted(d for d in dirs if not d.startswith(_SKIPPED_PREFIXES) and d not in _SKIPPED_NAMES)
            for name in sorted(names):
                if name.startswith(".") or name.endswith(".tmp"):
                    continue
                found.append(Path(root) / name)
        found.sort(key=lambda p: (p != self.skill_md, p.relative_to(self.dir).as_posix()))
        return found

    def relative_files(self) -> list[str]:
        return [p.relative_to(self.dir).as_posix() for p in self.files()]

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.files())

    def content_hash(self) -> str:
        """sha256 over every file's relative path and bytes, in order."""
        digest = hashlib.sha256()
        for path in self.files():
            digest.update(path.relative_to(self.dir).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()


def discover(root: Path) -> list[SkillFiles]:
    """
    Every `<dir>/SKILL.md` under `root`, the way Hermes and its taps do it:
    directories starting with `.` or `_` are skipped, a directory that holds
    a SKILL.md is a skill and is not searched further (its `references/`
    are not skills), anything else is a category and is searched.
    """
    found: list[SkillFiles] = []
    root = Path(root)
    if not root.is_dir():
        return found
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith(_SKIPPED_PREFIXES) or entry.name in _SKIPPED_NAMES:
                continue
            if (entry / SKILL_FILE).is_file():
                found.append(SkillFiles(entry))
            else:
                stack.append(entry)
    found.sort(key=lambda s: s.dir.relative_to(root).as_posix())
    return found


def snapshot(skill: SkillFiles, into: Path) -> Path:
    """Copy the whole skill directory to `into` (replacing it)."""
    into = Path(into)
    if into.exists():
        shutil.rmtree(into)
    shutil.copytree(skill.dir, into, ignore=shutil.ignore_patterns("__pycache__", "*.tmp"))
    return into


def restore(snapshot_dir: Path, skill_dir: Path) -> Path:
    """Put a snapshot back as the live skill directory."""
    snapshot_dir, skill_dir = Path(snapshot_dir), Path(skill_dir)
    if not (snapshot_dir / SKILL_FILE).is_file():
        raise K.SkillError(f"not a skill snapshot: {snapshot_dir}")
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    shutil.copytree(snapshot_dir, skill_dir)
    return skill_dir


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"


def tier_roots(overrides: dict[str, Path] | None = None,
               workspace_id: str | None = None) -> dict[str, Path]:
    """The configured tiers, tier -> directory. Unconfigured tiers are absent.

    The LOCAL_USER tier is per workspace (FR-PRV-002: "private skills are
    never globally reused without explicit sharing"). Discovery scans tier
    roots, so giving each workspace its own root is what stops one tenant's
    procedures being found - and then routed - for another. BUILT_IN,
    PROJECT and PUBLISHED_PUBLIC stay shared: those are the verified generic
    layer FR-SKL-002 describes.

    The `default` workspace keeps the original `user` directory rather than
    moving under `workspaces/default`. Renaming it would invalidate the
    absolute `path` recorded on every row of the skills registry, and an
    install's working skills becoming unfindable is a worse failure than the
    asymmetry. A second workspace gets `workspaces/<id>`, which never
    existed before and so has nothing to break.
    """
    from friday.config import DATA_DIR, PROJECT_ROOT, SKILLS_DIR
    from friday.workspace import DEFAULT_ID, resolve

    owner = resolve(workspace_id)
    project = PROJECT_ROOT / "skills"
    base = SKILLS_DIR
    if SKILLS_DIR.resolve() == project.resolve():
        # A source checkout: the state root is the repository, so the user
        # tier would sit inside the project tier and be found twice. Keep
        # it under data/ (runtime state, ignored by git) instead.
        base = DATA_DIR / "skills"
    user = base / "user" if owner == DEFAULT_ID else base / "workspaces" / owner
    roots: dict[str, Path] = {
        K.BUILT_IN: BUILTIN_DIR,
        K.LOCAL_USER: user,
        K.PROJECT_TIER: project,
    }
    private = os.getenv("FRIDAY_SKILLS_PRIVATE_DIR", "").strip()
    public = os.getenv("FRIDAY_SKILLS_PUBLIC_DIR", "").strip()
    if private:
        roots[K.SHARED_PRIVATE] = Path(private).expanduser()
    if public:
        roots[K.PUBLISHED_PUBLIC] = Path(public).expanduser()
    if overrides:
        roots.update({tier: Path(path) for tier, path in overrides.items()})
    return roots
