"""
Let Hermes see FRIDAY's skills: `skills.external_dirs` in its config.yaml.

Hermes scans every directory listed there next to its own skills dir, so
a FRIDAY-managed skill appears in `skills_list`, `skill_view` and as a
slash command with nothing copied. This module computes the change as a
plan by default and applies it only when asked, editing the file
textually (a YAML round-trip would drop the owner's comments) and
keeping a dated backup. The result is verified by parsing the file again;
a file that no longer parses is restored from the backup.

The Hermes home is `HERMES_HOME` when set, else `%LOCALAPPDATA%\\hermes`
when it holds a config.yaml (this machine's layout), else `~/.hermes`.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

import yaml

from friday.skills import contracts as K


def hermes_home() -> Path | None:
    explicit = os.getenv("HERMES_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    local = os.getenv("LOCALAPPDATA", "").strip()
    if local and (Path(local) / "hermes" / "config.yaml").is_file():
        return Path(local) / "hermes"
    home = Path.home() / ".hermes"
    return home if home.is_dir() else None


def desired_dirs(roots: dict[str, Path]) -> list[str]:
    """FRIDAY's writable tiers that exist, as absolute forward-slash paths."""
    found: list[str] = []
    for tier in (K.LOCAL_USER, K.PROJECT_TIER, K.SHARED_PRIVATE):
        path = roots.get(tier)
        if path is not None and Path(path).is_dir():
            found.append(Path(path).resolve().as_posix())
    return found


def current_dirs(config_path: Path) -> list[str]:
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    skills = loaded.get("skills") if isinstance(loaded, dict) else None
    dirs = skills.get("external_dirs") if isinstance(skills, dict) else None
    return [str(d) for d in dirs] if isinstance(dirs, list) else []


def plan(config_path: Path, dirs: list[str]) -> dict:
    present = {Path(d).expanduser().resolve().as_posix() for d in current_dirs(config_path) if d}
    add = [d for d in dirs if d not in present]
    return {"config": str(config_path), "current": sorted(present), "add": add, "changed": bool(add)}


def _rewrite(text: str, add: list[str]) -> str:
    """Insert `add` under skills.external_dirs, creating what is missing."""
    lines = text.splitlines()
    entries = [f"    - {d}" for d in add]
    skills_at = next((i for i, line in enumerate(lines) if re.match(r"^skills:\s*$", line)), None)
    if skills_at is None:
        return text.rstrip("\n") + "\nskills:\n  external_dirs:\n" + "\n".join(entries) + "\n"
    end = len(lines)
    for j in range(skills_at + 1, len(lines)):
        if lines[j] and not lines[j].startswith((" ", "\t", "#")):
            end = j
            break
    for j in range(skills_at + 1, end):
        match = re.match(r"^(\s+)external_dirs:\s*(\[\s*\])?\s*$", lines[j])
        if match:
            if match.group(2):                                  # external_dirs: []
                lines[j] = f"{match.group(1)}external_dirs:"
                lines[j + 1:j + 1] = entries
            else:                                               # an existing list
                k = j + 1
                while k < end and re.match(r"^\s+-\s", lines[k]):
                    k += 1
                lines[k:k] = entries
            return "\n".join(lines) + "\n"
    lines[skills_at + 1:skills_at + 1] = ["  external_dirs:"] + entries
    return "\n".join(lines) + "\n"


def apply(config_path: Path, dirs: list[str], *, backup: bool = True) -> dict:
    """Write the plan. Returns the plan plus `backup` and `applied`."""
    config_path = Path(config_path)
    result = plan(config_path, dirs)
    if not result["changed"]:
        result.update({"applied": False, "backup": ""})
        return result
    original = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    backup_path = ""
    if backup and original:
        backup_path = str(config_path.with_name(f"{config_path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
        shutil.copy2(config_path, backup_path)
    config_path.write_text(_rewrite(original, result["add"]), encoding="utf-8")
    after = set(current_dirs(config_path))                  # as written, no resolving
    if not all(d in after for d in result["add"]):
        config_path.write_text(original, encoding="utf-8")
        raise K.SkillError("the rewritten config did not parse back with the new directories; restored")
    result.update({"applied": True, "backup": backup_path})
    return result
