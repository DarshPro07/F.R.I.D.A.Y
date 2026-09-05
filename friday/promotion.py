
"""
The gate between "the agent finished" and "the code is on the branch".

The existing worktree manager can already promote and roll back. What it
cannot do is refuse, because it was never told what would make a promotion
wrong - it takes an instruction and carries it out. This module is the part
that decides, and its default answer is no.

    A development run may be promoted when, and only when:

      1. an objective verifier passed, run after the agent stopped
      2. the diff is confined to files the run was allowed to touch
      3. the diff is reviewable - no tool debris, no machine configuration,
         no binary nobody looked at
      4. the ground has not moved: the repository is still on the commit the
         work started from
      5. nothing in the diff is a secret
      6. a human or an explicit policy said yes to what remains

Each of those is a separate refusal with its own reason, because "rejected"
without a reason turns into "promote it anyway" the second time somebody is
in a hurry.

The order matters. Verification comes first because it is the cheapest thing
that invalidates everything after it. Reviewability comes before the secret
scan because debris gets in front of a person before the interesting part
does, and a reviewer who skips six leftovers skims the seventh. And the
secret scan comes before the approval so that nobody is ever asked to
approve a diff containing a key.
"""

from __future__ import annotations

import logging

import re
import subprocess

from dataclasses import dataclass, field

from pathlib import Path

logger = logging.getLogger("friday-agent")

#: Refusal reasons. Strings, because they end up in a report a person reads.
NOT_VERIFIED = "NOT_VERIFIED"

VERIFIER_INCONCLUSIVE = "VERIFIER_INCONCLUSIVE"

OUT_OF_SCOPE = "OUT_OF_SCOPE"

SECRET_IN_DIFF = "SECRET_IN_DIFF"

NOT_APPROVED = "NOT_APPROVED"

NOTHING_TO_PROMOTE = "NOTHING_TO_PROMOTE"

#: FR-012: the worker that implemented a consequential change is not the
#: sole authority certifying it. A review by a different attribution is
#: required past this many changed files; a DISPUTED review refuses.
REVIEW_DISPUTED = "REVIEW_DISPUTED"

NOT_INDEPENDENTLY_REVIEWED = "NOT_INDEPENDENTLY_REVIEWED"

#: Changes touching more files than this are consequential enough to need
#: the second authority. Below it the objective verifier alone suffices.
CONSEQUENTIAL_FILES = 3

#: Files a development run may never bring back, whatever it was asked to do.
#: A run that edited its own permissions is not a run whose result can be
#: trusted to say whether it should have been allowed to.
NEVER = (
    ".env", ".env.local", ".env.production",
    "id_rsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc",
    "credentials.json", "token.json", "service-account.json",
)

#: Shapes that mean a secret got committed. Deliberately a small list of
#: things that are unambiguous - a scanner that cries wolf gets bypassed.
SECRET_SHAPES = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
)

DEBRIS = ('.pytest_cache', '__pycache__', '.ruff_cache', '.mypy_cache', 'node_modules', '.DS_Store', 'Thumbs.db', '.coverage', 'htmlcov', '.graft', '.opencode', '.claude/settings.local.json', 'npm-debug.log', 'yarn-error.log', '.sandbox', 'sandbox.json')

GLOBAL_CONFIG = ('.gitconfig', '.bashrc', '.zshrc', '.profile', '.bash_profile', 'settings.json', 'mcp.json', '.mcp.json', 'AGENTS.md')

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
BINARY_SUFFIXES = (
    '.exe',
    '.dll',
    '.so',
    '.dylib',
    '.bin',
    '.o',
    '.a',
    '.lib',
    '.pyc',
    '.pyd',
    '.class',
    '.jar',
    '.war',
    '.zip',
    '.tar',
    '.gz',
    '.7z',
    '.rar',
    '.pdf',
    '.png',
    '.jpg',
    '.jpeg',
    '.gif',
    '.ico',
    '.mp4',
    '.mp3',
    '.wav',
    '.sqlite',
    '.sqlite3',
    '.db',
    '.pack',
    '.idx',
)

SMALL_BINARY = 65536

DEBRIS_IN_DIFF = 'DEBRIS_IN_DIFF'

GLOBAL_CONFIG_CHANGED = 'GLOBAL_CONFIG_CHANGED'

UNEXPECTED_BINARY = 'UNEXPECTED_BINARY'

WRONG_BASE = 'WRONG_BASE'


def debris_in(changed) -> list[str]:
    """Tool and sandbox leftovers that are not the work."""
    found = []
    for path in changed:
        posix = str(path).replace("\\", "/")
        if any(mark in posix for mark in DEBRIS):
            found.append(path)
    return found


def global_config_in(changed) -> list[str]:
    """
    Changes to how the machine behaves rather than to the project.

    A run may change what it is building. It may not change what it is
    allowed to do next time, which is what editing an agent settings file
    amounts to - and Graft's own `--no-global` flag exists because
    initialisation can write exactly these.
    """
    found = []
    for path in changed:
        name = Path(str(path).replace("\\", "/")).name
        if name in GLOBAL_CONFIG:
            found.append(path)
    return found


def unexpected_binaries(workspace, changed) -> list[str]:
    """Binary files a source change brought with it."""
    root = Path(workspace)
    found = []
    for path in changed:
        target = root / path
        if target.suffix.lower() not in BINARY_SUFFIXES:
            continue
        try:
            if target.is_file() and target.stat().st_size <= SMALL_BINARY:
                continue
        except OSError:
            pass
        found.append(path)
    return found


def base_matches(workspace, expected: str) -> tuple[bool, str]:
    """
    Whether the repository is still on the commit the work started from.

    A patch verified against one base and applied to another is how a clean
    review lands broken code. Checked rather than assumed, because the boss
    may well have committed something himself while the agent worked.
    """
    if not expected:
        return True, ""
    try:
        out = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not read HEAD: {exc}"
    if out.returncode != 0:
        return False, (out.stderr or "git could not read HEAD").strip()[:200]
    head = (out.stdout or "").strip()
    if head.startswith(expected) or expected.startswith(head):
        return True, head
    return False, (f"the work started from {expected[:12]} and the repository "
                   f"is now on {head[:12]}")


@dataclass
class Decision:
    """Whether this run may land, and everything that went into saying so."""

    allowed: bool
    reason: str = ""
    detail: str = ""
    #: Files the run changed, as the gate saw them.
    changed: tuple[str, ...] = ()
    #: Each check that ran, in order, with its outcome. The audit trail.
    checks: list[dict] = field(default_factory=list)

    def note(self, name: str, ok: bool, detail: str = "") -> "Decision":
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        return self

    def refuse(self, reason: str, detail: str = "") -> "Decision":
        self.allowed = False
        self.reason = reason
        self.detail = detail
        logger.info("promotion.refused reason=%s %s", reason, detail[:200])
        return self

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason,
                "detail": self.detail, "changed": list(self.changed),
                "checks": self.checks}


def touches_forbidden(changed) -> list[str]:
    """Files nothing may promote, whatever the task was."""
    found = []
    for path in changed:
        name = Path(path).name.lower()
        if name in NEVER or any(name.endswith(f) for f in NEVER):
            found.append(path)
    return found


def out_of_scope(changed, allowed: tuple[str, ...]) -> list[str]:
    """
    Files outside what the run was allowed to touch.

    `allowed` is a tuple of path prefixes, relative and posix. An empty tuple
    means the run was not scoped, and an unscoped run is not blocked here -
    the scope check can only be as strict as the scope it was given, and
    pretending otherwise would refuse every run that never declared one.
    """
    if not allowed:
        return []
    outside = []
    for path in changed:
        posix = str(path).replace("\\", "/").lstrip("./")
        if not any(posix == prefix or posix.startswith(prefix.rstrip("/") + "/")
                   for prefix in allowed):
            outside.append(path)
    return outside


def secrets_in(workspace: str | Path, changed, *,
               max_bytes: int = 2_000_000) -> list[str]:
    """
    Changed files whose contents match a secret shape.

    Reads the file rather than the diff, because a key added in one commit
    and left in place by the next is still a key on the branch.
    """
    root = Path(workspace)
    found = []
    for path in changed:
        target = root / path
        try:
            if not target.is_file() or target.stat().st_size > max_bytes:
                continue
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for shape in SECRET_SHAPES:
            if shape.search(text):
                found.append(path)
                break
    return found


def decide(workspace: str | Path, changed, *, attempt=None,
           allowed_paths: tuple[str, ...] = (), base_commit: str = "",
           approved: bool = False, review=None,
           consequential: bool | None = None) -> Decision:
    """
    The gate. Default no.

    `attempt` is a `friday.evaluation.Attempt` - the objective verdict on
    whether the work works. It is checked first because everything after it
    is wasted if the tests failed.

    `review` is a `friday.adversarial.ReviewEvidence` from a reviewer that
    is not the implementing worker (FR-012). A consequential change - more
    than `CONSEQUENTIAL_FILES` files, or `consequential=True` - is refused
    without one, and refused outright when the review is DISPUTED. A
    review by the implementer itself does not count.
    """
    changed = tuple(changed or ())
    decision = Decision(allowed=True, changed=changed)

    if not changed:
        return decision.note("changes", False).refuse(
            NOTHING_TO_PROMOTE, "the run changed no files")
    decision.note("changes", True, f"{len(changed)} file(s)")

    # 1. Did it actually work?
    from friday import evaluation as E

    if attempt is None:
        return decision.note("verified", False).refuse(
            NOT_VERIFIED,
            "no verifier was run; a run reporting success is a claim, not a "
            "result")
    if attempt.verdict == E.INCONCLUSIVE:
        return decision.note("verified", False, attempt.detail[:200]).refuse(
            VERIFIER_INCONCLUSIVE,
            "the verifier could not reach a verdict, which is not a pass")
    if not attempt.passed:
        return decision.note("verified", False, attempt.detail[:200]).refuse(
            NOT_VERIFIED, f"the verifier failed with exit {attempt.exit_code}")
    decision.note("verified", True, attempt.detail[-200:] if attempt.detail else "")

    # 1b. FR-012: a second, independent authority for consequential changes.
    needs_review = (consequential if consequential is not None
                    else len(changed) > CONSEQUENTIAL_FILES)
    if review is not None and not getattr(review, "independent", True):
        decision.note("independent_review", False,
                      f"reviewed by the implementer ({review.reviewed_by})")
        review = None
    if review is not None:
        from friday import adversarial as A
        if review.verdict == A.DISPUTED:
            return decision.note("independent_review", False,
                                 "; ".join(review.findings)[:300]).refuse(
                REVIEW_DISPUTED,
                f"the independent reviewer disputed the change: "
                f"{'; '.join(review.findings[:3]) or 'no findings listed'}")
        if review.verdict == A.INCONCLUSIVE and needs_review:
            return decision.note("independent_review", False,
                                 review.error or "inconclusive").refuse(
                NOT_INDEPENDENTLY_REVIEWED,
                "the independent review was inconclusive and the change is "
                "consequential")
        decision.note("independent_review", review.verdict == A.CONFIRMED,
                      f"{review.verdict} by {review.reviewed_by}")
    elif needs_review:
        return decision.note("independent_review", False).refuse(
            NOT_INDEPENDENTLY_REVIEWED,
            f"{len(changed)} file(s) changed and no reviewer other than the "
            f"implementer has looked; the implementing worker is not the sole "
            f"authority")
    else:
        decision.note("independent_review", True, "not required at this size")

    # 2. Did it stay where it was told?
    forbidden = touches_forbidden(changed)
    if forbidden:
        return decision.note("scope", False, ", ".join(forbidden)).refuse(
            OUT_OF_SCOPE,
            f"the run changed files nothing may promote: {', '.join(forbidden)}")
    outside = out_of_scope(changed, allowed_paths)
    if outside:
        return decision.note("scope", False, ", ".join(outside[:5])).refuse(
            OUT_OF_SCOPE,
            f"{len(outside)} file(s) outside the run's scope: "
            f"{', '.join(outside[:5])}")
    decision.note("scope", True)

    # 3. Is it a diff a person could review? Leftovers, machine settings and
    # binaries are refused before anyone is asked to read them.
    debris = debris_in(changed)
    if debris:
        return decision.note("reviewable", False, ", ".join(debris[:5])).refuse(
            DEBRIS_IN_DIFF,
            f"{len(debris)} tool or sandbox leftover(s) in the diff: "
            f"{', '.join(debris[:5])}")
    machine = global_config_in(changed)
    if machine:
        return decision.note("reviewable", False, ", ".join(machine)).refuse(
            GLOBAL_CONFIG_CHANGED,
            f"this changes how the machine behaves rather than the project: "
            f"{', '.join(machine)}")
    blobs = unexpected_binaries(workspace, changed)
    if blobs:
        return decision.note("reviewable", False, ", ".join(blobs[:5])).refuse(
            UNEXPECTED_BINARY,
            f"binary file(s) nobody reviewed: {', '.join(blobs[:5])}")
    decision.note("reviewable", True)

    # 4. Is it still the code the work was verified against?
    if base_commit:
        same, detail = base_matches(workspace, base_commit)
        if not same:
            return decision.note("base", False, detail).refuse(
                WRONG_BASE, detail)
        decision.note("base", True, detail[:12])

    # 5. Is there a secret in it? Before anyone is asked to look.
    leaked = secrets_in(workspace, changed)
    if leaked:
        return decision.note("secrets", False, ", ".join(leaked)).refuse(
            SECRET_IN_DIFF,
            f"a secret-shaped string is in {', '.join(leaked)}; nothing was "
            f"promoted and the value should be treated as compromised")
    decision.note("secrets", True)

    # 6. Did anyone say yes?
    if not approved:
        return decision.note("approved", False).refuse(
            NOT_APPROVED, "verified and clean, but nobody has approved it yet")
    decision.note("approved", True)

    logger.info("promotion.allowed files=%d", len(changed))
    return decision
