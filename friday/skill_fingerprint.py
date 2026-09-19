"""Skill fingerprints: what a skill was validated AGAINST, so a code change
can say which skills it invalidates - and which it does not.

FR-012 / GB-07 / GB-08. The property that matters is the negative one:

    a README typo must NOT invalidate the provider-debugging skill;
    a change to model_gateway.py MAY.

A skill that is revalidated on every commit is a skill nobody trusts, and a
skill that is never revalidated is a skill that quietly goes wrong. The
fingerprint sits between: it names the exact files, packages and schema a
skill's procedure rests on, hashes them at validation time, and later
answers "has anything this skill depends on actually changed?"

Built on `SkillLadder` - same database, same lifecycle states. This module
adds one table and one state transition (VALIDATED -> NEEDS_REVALIDATION);
it does not create a second skill registry.

Dependency kinds:

    file        a source path, hashed by content
    directory   every tracked file under it, hashed as a set
    package     an installed distribution, keyed by version
    schema      a named schema version string the skill was written for
    skill       another skill, by name (transitive staleness)

Anything a skill does not declare is not a dependency. That is deliberate:
"depends on everything" is the README-typo failure in disguise.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from friday.skill_ladder import SkillLadder

_TABLE = """
CREATE TABLE IF NOT EXISTS skill_fingerprints (
    skill        TEXT NOT NULL,
    version      INTEGER NOT NULL,
    kind         TEXT NOT NULL,        -- file | directory | package | schema | skill
    target       TEXT NOT NULL,        -- path / distribution / schema name / skill name
    digest       TEXT NOT NULL,        -- what it hashed to when the skill was validated
    recorded_at  REAL NOT NULL,
    PRIMARY KEY (skill, version, kind, target)
)
"""

NEEDS_REVALIDATION = "NEEDS_REVALIDATION"

KINDS = ("file", "directory", "package", "schema", "skill")


@dataclass(frozen=True)
class Dependency:
    kind: str
    target: str

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown dependency kind {self.kind!r}; one of {KINDS}")


@dataclass
class Drift:
    """One dependency whose digest no longer matches."""
    kind: str
    target: str
    was: str
    now: str

    def describe(self) -> str:
        if self.now == "MISSING":
            return f"{self.kind} {self.target} no longer exists"
        return f"{self.kind} {self.target} changed"


@dataclass
class Check:
    skill: str
    version: int
    drift: list[Drift] = field(default_factory=list)
    unchanged: int = 0

    @property
    def stale(self) -> bool:
        return bool(self.drift)


# --------------------------------------------------------------------------
# digests
# --------------------------------------------------------------------------

def _digest_file(root: Path, rel: str) -> str:
    p = root / rel
    if not p.is_file():
        return "MISSING"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:24]


def _digest_directory(root: Path, rel: str) -> str:
    """Content hash of every file under the directory, ordered by path.

    Ordered so the digest is a function of the tree, not of enumeration
    order; includes the relative path so a rename registers.
    """
    d = root / rel
    if not d.is_dir():
        return "MISSING"
    h = hashlib.sha256()
    for f in sorted(p for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        h.update(str(f.relative_to(d)).encode("utf-8"))
        h.update(f.read_bytes())
    return h.hexdigest()[:24]


def _digest_package(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:  # noqa: BLE001 - absent is a real, reportable state
        return "MISSING"


def digest(dep: Dependency, *, root: Path, schemas: dict[str, str] | None = None,
           ladder: SkillLadder | None = None) -> str:
    if dep.kind == "file":
        return _digest_file(root, dep.target)
    if dep.kind == "directory":
        return _digest_directory(root, dep.target)
    if dep.kind == "package":
        return _digest_package(dep.target)
    if dep.kind == "schema":
        return (schemas or {}).get(dep.target, "MISSING")
    if dep.kind == "skill":
        # A dependency on another skill is a dependency on THAT skill's
        # validated version. When it re-validates at a new version, we drift.
        cur = ladder.current(dep.target) if ladder else None
        return f"v{cur['version']}:{cur['state']}" if cur else "MISSING"
    raise ValueError(dep.kind)


# --------------------------------------------------------------------------
# the store
# --------------------------------------------------------------------------

class SkillFingerprints:
    """Records what each validated skill depends on; reports drift."""

    def __init__(self, ladder: SkillLadder, *, root: str | Path,
                 schemas: dict[str, str] | None = None) -> None:
        self.ladder = ladder
        self.root = Path(root)
        self.schemas = dict(schemas or {})
        with self.ladder._connect() as db:
            db.execute(_TABLE)

    # -- recording ---------------------------------------------------------

    def record(self, skill: str, deps: list[Dependency]) -> dict:
        """Hash every declared dependency for the skill's CURRENT version.

        Call this when a skill is validated - the digests are the state the
        validation run proved the procedure against. Recording replaces any
        earlier fingerprint for the same version.
        """
        cur = self.ladder.current(skill)
        if cur is None:
            raise KeyError(f"no such skill {skill!r}")
        version = int(cur["version"])
        now = time.time()
        rows = [(skill, version, d.kind, d.target,
                 digest(d, root=self.root, schemas=self.schemas, ladder=self.ladder), now)
                for d in deps]
        with self.ladder._connect() as db:
            db.execute("DELETE FROM skill_fingerprints WHERE skill = ? AND version = ?",
                       (skill, version))
            db.executemany(
                "INSERT INTO skill_fingerprints (skill, version, kind, target, digest, recorded_at)"
                " VALUES (?,?,?,?,?,?)", rows)
        return {"skill": skill, "version": version, "dependencies": len(rows),
                "missing": [r[3] for r in rows if r[4] == "MISSING"]}

    def dependencies(self, skill: str, version: int | None = None) -> list[dict]:
        cur = self.ladder.current(skill)
        if cur is None:
            return []
        version = int(cur["version"]) if version is None else version
        with self.ladder._connect() as db:
            rows = db.execute(
                "SELECT kind, target, digest, recorded_at FROM skill_fingerprints"
                " WHERE skill = ? AND version = ? ORDER BY kind, target",
                (skill, version)).fetchall()
        return [dict(r) for r in rows]

    # -- checking ----------------------------------------------------------

    def check(self, skill: str) -> Check:
        """Re-hash every recorded dependency and report what moved."""
        cur = self.ladder.current(skill)
        if cur is None:
            raise KeyError(f"no such skill {skill!r}")
        result = Check(skill=skill, version=int(cur["version"]))
        for row in self.dependencies(skill):
            dep = Dependency(row["kind"], row["target"])
            now = digest(dep, root=self.root, schemas=self.schemas, ladder=self.ladder)
            if now != row["digest"]:
                result.drift.append(Drift(dep.kind, dep.target, row["digest"], now))
            else:
                result.unchanged += 1
        return result

    def affected_by(self, changed_paths: list[str]) -> dict[str, list[str]]:
        """Which validated skills declare a dependency touched by these paths.

        The Git-diff entry point: feed it `git diff --name-only` and get back
        {skill: [paths that hit it]}. A path under a declared directory
        counts; a path nobody declared affects nobody. This is the function
        that makes a README typo invalidate nothing.
        """
        changed = [Path(p).as_posix() for p in changed_paths]
        with self.ladder._connect() as db:
            rows = db.execute(
                "SELECT f.skill, f.kind, f.target FROM skill_fingerprints f"
                " JOIN skill_candidates c ON c.name = f.skill AND c.version = f.version"
                " WHERE f.kind IN ('file', 'directory') AND c.state = 'VALIDATED'"
            ).fetchall()
        hits: dict[str, list[str]] = {}
        for r in rows:
            target = Path(r["target"]).as_posix()
            for path in changed:
                if (r["kind"] == "file" and path == target) or \
                   (r["kind"] == "directory" and (path == target or path.startswith(target.rstrip("/") + "/"))):
                    hits.setdefault(r["skill"], []).append(path)
        return {k: sorted(set(v)) for k, v in hits.items()}

    # -- the state transition -----------------------------------------------

    def mark_stale(self, skill: str, check: Check) -> dict:
        """VALIDATED -> NEEDS_REVALIDATION, with the drift recorded as the reason.

        Only a VALIDATED skill can go stale; a CANDIDATE was never trusted
        and a DEPRECATED one is already out. Idempotent.
        """
        cur = self.ladder.current(skill)
        if cur is None:
            raise KeyError(skill)
        if cur["state"] != "VALIDATED":
            return cur
        if not check.stale:
            return cur
        reason = json.dumps([d.describe() for d in check.drift])
        with self.ladder._connect() as db:
            db.execute(
                "UPDATE skill_candidates SET state = ?, validation = ?, updated_at = ?"
                " WHERE name = ? AND version = ?",
                (NEEDS_REVALIDATION, f"stale: {reason}", time.time(), skill, cur["version"]))
        return self.ladder.current(skill)

    def sweep(self, changed_paths: list[str] | None = None) -> dict:
        """Check every VALIDATED skill (or only those a diff touches) and mark
        the stale ones. Returns what moved and what did not, so the caller
        can prove the negative - that unrelated skills were left alone."""
        if changed_paths is not None:
            candidates = list(self.affected_by(changed_paths))
        else:
            candidates = [row["name"] for row in self.ladder.listing("VALIDATED")]
        stale, clean = [], []
        for name in candidates:
            chk = self.check(name)
            if chk.stale:
                self.mark_stale(name, chk)
                stale.append({"skill": name, "drift": [d.describe() for d in chk.drift]})
            else:
                clean.append(name)
        untouched = [row["name"] for row in self.ladder.listing("VALIDATED")
                     if row["name"] not in candidates]
        return {"stale": stale, "clean": clean, "untouched": untouched}
