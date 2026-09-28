"""
Where a skill may travel, and the gate at each door.

    LOCAL        STATE_ROOT/skills/user/<name>          the owner's machine
    PRIVATE_GIT  $FRIDAY_SKILLS_PRIVATE_DIR/<name>       a checkout of the private tap (skills/)
    PUBLIC_GIT   $FRIDAY_SKILLS_PUBLIC_DIR/<name>        a checkout of the public tap (skills/)

"Publish" never means "make public". The layout is Hermes's tap layout
(`skills/<name>/SKILL.md`), so a Hermes user adds the repository with
`hermes skills tap add owner/repo` and installs from it; FRIDAY controls
what may move from private to public:

    ACTIVE, evaluated, lint clean for publication (no secrets, no private
    paths, no personal data), a license, quality above the floor, a scope
    that is not codebase knowledge, the skill's own `publish: public`
    intent, AND standing authorization from the owner
    (FRIDAY_SKILLS_PUBLISH_PUBLIC=1) or an explicit approval for this one.

An approval that does not arrive is a refusal. Pushing is a separate,
opt-in step (FRIDAY_SKILLS_PUSH=1): a commit in a local checkout is
reversible, a push is not.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from friday.skills import contracts as K
from friday.skills import files as F
from friday.skills import lint as L

LOCAL, PRIVATE_GIT, PUBLIC_GIT = "LOCAL", "PRIVATE_GIT", "PUBLIC_GIT"
TARGETS = (LOCAL, PRIVATE_GIT, PUBLIC_GIT)

TARGET_TIER = {LOCAL: K.LOCAL_USER, PRIVATE_GIT: K.SHARED_PRIVATE, PUBLIC_GIT: K.PUBLISHED_PUBLIC}

#: Below this a skill is not worth sharing with anyone else.
MIN_QUALITY_TO_SHARE = 0.5

Approver = Callable[[str, str], bool]     # (skill name, target) -> approved


@dataclass
class PublishPolicy:
    allow_public: bool = False               # standing authorization for public taps
    push: bool = False                       # git push after commit
    min_quality: float = MIN_QUALITY_TO_SHARE
    private_dir: str = ""
    public_dir: str = ""

    @classmethod
    def from_env(cls) -> "PublishPolicy":
        flag = os.getenv("FRIDAY_SKILLS_PUBLISH_PUBLIC", "").strip().lower()
        push = os.getenv("FRIDAY_SKILLS_PUSH", "").strip().lower()
        return cls(allow_public=flag in ("1", "true", "yes"),
                   push=push in ("1", "true", "yes"),
                   private_dir=os.getenv("FRIDAY_SKILLS_PRIVATE_DIR", "").strip(),
                   public_dir=os.getenv("FRIDAY_SKILLS_PUBLIC_DIR", "").strip())


@dataclass
class PublishResult:
    skill: str
    target: str
    ok: bool
    reason: str
    path: str = ""
    commit: str = ""
    pushed: bool = False
    refusals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"skill": self.skill, "target": self.target, "ok": self.ok, "reason": self.reason,
                "path": self.path, "commit": self.commit, "pushed": self.pushed, "refusals": self.refusals}


#: The commit identity for skill publications. FRIDAY made the commit, so
#: FRIDAY signs it; the owner can name it (FRIDAY_SKILLS_GIT_NAME/EMAIL)
#: and a checkout with no identity of its own still works.
GIT_NAME = os.getenv("FRIDAY_SKILLS_GIT_NAME", "FRIDAY")
GIT_EMAIL = os.getenv("FRIDAY_SKILLS_GIT_EMAIL", "friday@localhost")


def _git(args: list[str], cwd: Path, timeout: float = 60.0) -> tuple[int, str]:
    try:
        completed = subprocess.run(["git", "-c", f"user.name={GIT_NAME}", "-c", f"user.email={GIT_EMAIL}", *args],
                                   cwd=str(cwd), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return completed.returncode, (completed.stdout + completed.stderr).strip()


class SkillPublisher:
    def __init__(self, registry, *, roots: dict[str, Path] | None = None,
                 policy: PublishPolicy | None = None, approver: Approver | None = None,
                 git=_git) -> None:
        self.registry = registry
        self.policy = policy or PublishPolicy.from_env()
        self.roots = roots if roots is not None else F.tier_roots()
        self.approver = approver
        self._git = git

    # -- the gate ------------------------------------------------------------------

    def refusals(self, name: str, target: str) -> list[str]:
        """Every reason this skill may not go to `target`; empty means it may."""
        if target not in TARGETS:
            return [f"unknown target {target!r}"]
        record = self.registry.get(name)
        if record is None:
            return [f"no skill named {name!r}"]
        reasons: list[str] = []
        if record.state != K.ACTIVE:
            reasons.append(f"state is {record.state}, not ACTIVE")
        skill = F.SkillFiles(Path(record.path))
        if not skill.exists():
            return reasons + [f"{skill.skill_md} is missing"]
        if target == LOCAL:
            return reasons
        report = L.lint(skill, for_publication=True)
        if report.verdict == L.FAIL:
            reasons.append("lint for publication failed: " + ", ".join(sorted({f.code for f in report.failures()})))
        frontmatter, _ = skill.read()
        if record.quality_score < self.policy.min_quality:
            reasons.append(f"quality {record.quality_score} is below {self.policy.min_quality}")
        if TARGET_TIER[target] not in self.roots:
            reasons.append(f"no directory configured for {target} (FRIDAY_SKILLS_{'PRIVATE' if target == PRIVATE_GIT else 'PUBLIC'}_DIR)")
        if target == PRIVATE_GIT and record.publish not in (K.PUBLISH_PRIVATE, K.PUBLISH_PUBLIC):
            reasons.append(f"the skill declares publish: {record.publish}; private needs private or public")
        if target == PUBLIC_GIT:
            if record.scope in K.CODEBASE_SCOPES:
                reasons.append(f"scope {record.scope} is codebase knowledge; never public")
            if record.publish != K.PUBLISH_PUBLIC:
                reasons.append(f"the skill declares publish: {record.publish}, not public")
            if not str(frontmatter.get("license", "") or "").strip():
                reasons.append("no license in the frontmatter")
            if record.trust == K.TRUST_COMMUNITY:
                reasons.append("installed from outside; not ours to republish")
            if not self.policy.allow_public:
                approved = False
                if self.approver is not None:
                    try:
                        approved = bool(self.approver(name, target))
                    except Exception:                       # noqa: BLE001 - a failed prompt is a no
                        approved = False
                if not approved:
                    reasons.append("public publication needs standing authorization "
                                   "(FRIDAY_SKILLS_PUBLISH_PUBLIC=1) or an explicit approval; none given")
        return reasons

    # -- the move -------------------------------------------------------------------

    def export(self, name: str, destination: Path) -> Path:
        record = self.registry.get(name)
        if record is None:
            raise K.SkillError(f"no skill named {name!r}")
        skill = F.SkillFiles(Path(record.path))
        return F.snapshot(skill, Path(destination) / name)

    def publish(self, name: str, target: str, *, actor: str = "friday") -> PublishResult:
        refusals = self.refusals(name, target)
        if refusals:
            self.registry.note(name, f"publish to {target} refused: " + "; ".join(refusals), actor=actor)
            return PublishResult(name, target, False, "refused", refusals=refusals)
        root = self.roots[TARGET_TIER[target]]
        exported = self.export(name, root)
        result = PublishResult(name, target, True, "exported", path=str(exported))
        if target in (PRIVATE_GIT, PUBLIC_GIT):
            record = self.registry.get(name)
            repo = self._repo_root(root)
            if repo is None:
                result.ok, result.reason = False, f"{root} is not inside a git checkout"
                self.registry.note(name, f"publish to {target}: {result.reason}", actor=actor)
                return result
            code, out = self._git(["add", "--", str(exported.relative_to(repo).as_posix())], repo)
            if code != 0:
                result.ok, result.reason = False, f"git add failed: {out[:200]}"
                return result
            code, out = self._git(["commit", "-m", f"skill: {name} {record.version}", "--", str(exported.relative_to(repo).as_posix())], repo)
            if code != 0 and "nothing to commit" not in out:
                result.ok, result.reason = False, f"git commit failed: {out[:200]}"
                return result
            code, out = self._git(["rev-parse", "--short", "HEAD"], repo)
            result.commit = out if code == 0 else ""
            result.reason = "committed" if "nothing to commit" not in out else "already committed"
            if self.policy.push:
                code, out = self._git(["push"], repo, timeout=180.0)
                result.pushed = code == 0
                if code != 0:
                    result.reason += f"; push failed: {out[:200]}"
        self.registry.note(name, f"published to {target}: {result.reason} {result.path}", actor=actor)
        return result

    @staticmethod
    def _repo_root(path: Path) -> Path | None:
        for candidate in (path, *path.parents):
            if (candidate / ".git").exists():
                return candidate
        return None
