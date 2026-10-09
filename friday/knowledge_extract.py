"""
BIN-001 slice 1: extract knowledge, not code, from a pinned reverse-engineering
course (mytechnotalent/Reverse-Engineering, Apache-2.0).

`extract` turns an explicit allowlist of tutorial files into three attributed
skills. It reads only what the caller selected (plus the LICENSE), never
executes, imports or copies anything else, and writes the same bytes for the
same commit. The skills it returns are QUARANTINED: admission is a separate
step that needs a completed, passing SkillSpector scan (forge.scan_admits), so
no scan means not admitted.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from friday import repo_classifier
from friday.skills import contracts as K
from friday.skills import files as F

UPSTREAM_URL = "https://github.com/mytechnotalent/Reverse-Engineering"
LICENSE_ID = "Apache-2.0"
MAX_FILE_BYTES = 64 * 1024           # per selected tutorial
LICENSE_READ_CHARS = 4000            # what repo_classifier reads to identify one
ALLOWED_SUFFIX = ".md"               # tutorials only: no .asm, .exe or other example is ever read
LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt")

SKILLS = {
    "binary_analysis_skill": "Reading and reasoning about compiled binaries and disassembly.",
    "windows_internals_skill": "How Windows processes, modules and system structures work.",
    "firmware_debugging_skill": "Debugging embedded firmware and its hardware interfaces.",
}

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SOURCE_RE = re.compile(r"^Source: (\S+) @ ([0-9a-f]{40})$", re.MULTILINE)
_SECTION = "## Section: "


class ExtractRefused(Exception):
    """The inputs do not meet the extraction bar; nothing was written."""


@dataclass
class ExtractedSkill:
    name: str
    path: Path
    state: str = K.QUARANTINED


def _read_bounded(path: str, limit: int) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(limit + 1)


def _check_license(root: str) -> None:
    for name in LICENSE_NAMES:
        path = os.path.join(root, name)
        if os.path.isfile(path) and os.path.realpath(path).startswith(root + os.sep):
            text = _read_bounded(path, LICENSE_READ_CHARS)[:LICENSE_READ_CHARS].decode("utf-8", "replace")
            found = repo_classifier._identify(text)
            if found != LICENSE_ID:
                raise ExtractRefused(f"license is {found}, only {LICENSE_ID} may be extracted")
            return
    raise ExtractRefused(f"no license file; only {LICENSE_ID} may be extracted")


def _selected(root: str, relative: object) -> str:
    """The real path of one allowlisted tutorial, else a refusal."""
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise ExtractRefused(f"selection entry {relative!r} is not a relative posix path")
    parts = relative.split("/")
    if relative.startswith("/") or any(p in ("", ".", "..") for p in parts) or not relative.endswith(ALLOWED_SUFFIX):
        raise ExtractRefused(f"selection entry {relative!r} is outside the allowlist ({ALLOWED_SUFFIX} tutorials under the repo)")
    path = os.path.realpath(os.path.join(root, *parts))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        raise ExtractRefused(f"selection entry {relative!r} is not a tutorial file inside the repo")
    return path


def _section(path: str, relative: str, commit: str) -> str:
    if os.path.getsize(path) > MAX_FILE_BYTES:
        raise ExtractRefused(f"{relative} exceeds the {MAX_FILE_BYTES}-byte cap")
    data = _read_bounded(path, MAX_FILE_BYTES)
    if len(data) > MAX_FILE_BYTES:
        raise ExtractRefused(f"{relative} exceeds the {MAX_FILE_BYTES}-byte cap")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractRefused(f"{relative} is not UTF-8 text") from exc
    if "\x00" in text:
        raise ExtractRefused(f"{relative} is not text")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    title = Path(relative).stem.replace("_", " ")
    return f"{_SECTION}{title}\n\nSource: {relative} @ {commit}\n\n{text}"


def extract(repo_dir: str | os.PathLike, commit: str, selection: dict[str, list[str]],
            out_dir: str | os.PathLike) -> list[ExtractedSkill]:
    """Write one quarantined skill per entry of `selection` under `out_dir`."""
    if not isinstance(commit, str) or not _COMMIT_RE.match(commit):
        raise ExtractRefused("commit must be a pinned 40-character lowercase hex SHA")
    root = os.path.realpath(repo_dir)
    if not os.path.isdir(root):
        raise ExtractRefused(f"{repo_dir} is not a directory")
    _check_license(root)
    if not isinstance(selection, dict) or set(selection) != set(SKILLS):
        raise ExtractRefused(f"selection must name exactly {sorted(SKILLS)}")

    rendered: dict[str, str] = {}
    for name in sorted(SKILLS):
        entries = selection[name]
        if not isinstance(entries, (list, tuple)) or not entries:
            raise ExtractRefused(f"selection for {name} must list at least one tutorial")
        sections = [_section(_selected(root, rel), rel, commit) for rel in sorted(set(entries))]
        frontmatter = {"name": name, "description": SKILLS[name], "version": "1.0.0",
                       "license": LICENSE_ID, "source_url": UPSTREAM_URL, "commit": commit}
        body = (f"Attribution: derived from {UPSTREAM_URL} ({LICENSE_ID}) at commit {commit}. "
                "Knowledge only: no example from that repository is executed, installed or copied wholesale.\n\n"
                + "\n\n".join(sections))
        text = F.render_skill_md(frontmatter, body)
        if len(text) > K.MAX_SKILL_CONTENT_CHARS:
            raise ExtractRefused(f"{name} exceeds {K.MAX_SKILL_CONTENT_CHARS} characters; select fewer tutorials")
        rendered[name] = text

    skills = []
    for name, text in rendered.items():
        directory = Path(out_dir) / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / F.SKILL_FILE).write_bytes(text.encode("utf-8"))
        skills.append(ExtractedSkill(name=name, path=directory))
    return skills


def lint(skill_dir: str | os.PathLike) -> list[str]:
    """Provenance problems of an extracted skill; [] when it carries everything."""
    skill = F.SkillFiles(Path(skill_dir))
    if not skill.exists():
        return ["SKILL.md is missing"]
    try:
        frontmatter, body = skill.read()
    except K.SkillError as exc:
        return [str(exc)]
    problems = []
    if frontmatter.get("license") != LICENSE_ID:
        problems.append(f"license must be {LICENSE_ID}")
    if not str(frontmatter.get("source_url") or "").strip():
        problems.append("source_url is missing")
    commit = str(frontmatter.get("commit") or "")
    if not _COMMIT_RE.match(commit):
        problems.append("commit is missing or not a pinned SHA")
    if not re.search(r"^Attribution: .*" + re.escape(LICENSE_ID), body, re.MULTILINE):
        problems.append("the body has no attribution line")
    chunks = body.split("\n" + _SECTION)[1:]
    if not chunks:
        problems.append("no sections")
    for chunk in chunks:
        title = chunk.split("\n", 1)[0]
        match = _SOURCE_RE.search(chunk)
        if not match:
            problems.append(f"section {title!r} has no source path and commit")
        elif commit and match.group(2) != commit:
            problems.append(f"section {title!r} cites a different commit than the frontmatter")
    return problems
