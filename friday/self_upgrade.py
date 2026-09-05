"""Staged self-upgrade with automatic rollback (Phase 11; R16 /
build-pack 11).

The 14-step protocol reduced to its load-bearing invariants, each one
enforced by state-machine order rather than narrative discipline:

    checkpoint -> stage -> targeted tests -> [live check] -> promote
                                   |                            |
                                   v (fail)                     v (fail)
                                ROLLBACK  <---------------  health-fail

Invariants:
- No stage without a rollback target (git stash checkpoint) FIRST.
- No promote without the tests the change class requires (the change
  classifier from execution_economics prices the scope).
- Kernel paths (policy.py, sensitive_domains.py, netguard.py,
  user_policy constitutional set, this module itself) REFUSE staged
  upgrade entirely - Level 6 requires the human process, full stop.
- Every transition is journaled durably; a crash mid-upgrade leaves a
  record that names the rollback command.

This module performs REAL git operations on the working tree. It is
deliberately synchronous and small: an upgrade loop that is itself
complex would be the least trustworthy code in the system.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from friday.execution_economics import (AFFECTED, FULL, INTEGRATION,
                                        TARGETED, classify_task,
                                        verification_depth)

#: Level-6 kernel: files the self-development loop may never propose to
#: change, at any autonomy level. Two kinds (audit A-043, 2026-09-05):
#:
#:   * the trust boundaries themselves - policy, tiers, confirmation,
#:     network/file jails, sensitive domains, the constitution; and
#:   * THE JUDGE - everything that decides whether a self-change passes:
#:     the verifier, the honesty audit, the adversarial panel, the
#:     promotion gate, the benchmark, the golden corpus and runner, the
#:     tests that pin the trust plane, the CI workflow, and this loop.
#:
#: Matching is by path: an entry ending in "/" is a directory prefix, any
#: other entry is a file, and a candidate matches when its normalized,
#: repo-relative path ends with the entry (so absolute paths and sandbox
#: worktree paths match too). A change to any of these goes through the
#: human policy process, never through selfdev.
KERNEL_PATHS = (
    # trust boundaries
    "friday/policy.py", "friday/user_policy.py", "friday/trust.py",
    "friday/confirmation.py", "friday/sensitive_domains.py",
    "friday/netguard.py", "friday/fsjail.py", "friday/sandbox.py",
    "friday/privacy.py", "friday/access.py", "friday/secret_broker.py",
    "friday/vault.py",
    # the owner's words are the licence for a write; provider truth
    "friday/write_licence.py", "friday/provider_health.py",
    ".specify/memory/constitution.md",
    # protected agent instructions (read at runtime as tier-3 rules)
    "AGENTS.md",
    # the judge
    "friday/self_upgrade.py", "friday/selfdev.py", "friday/selfdev_benchmark.py",
    "friday/toolsets/selfdev.py", "friday/promotion.py", "friday/evaluation.py",
    "friday/honesty.py", "friday/adversarial.py", "friday/golden.py",
    "friday/reachability.py",
    "docs/golden/", "scripts/golden_corpus.py", "scripts/perf_profile.py",
    # CI security gates
    ".github/workflows/", ".gitleaks.toml", ".gitleaksignore",
    "tests/conftest.py", "tests/test_trust.py", "tests/test_user_policy.py",
    "tests/test_privacy.py", "tests/test_selfdev.py", "tests/test_self_upgrade.py",
    "tests/test_promotion.py", "tests/test_adversarial.py", "tests/test_golden_suite.py",
    "tests/test_silent_excepts.py", "tests/test_reachability.py",
    "tests/test_tool_isolation.py", "tests/test_secret_broker.py",
    "tests/test_sensitive_domains.py", "tests/test_netguard.py", "tests/test_fsjail.py",
    "tests/test_injection_pages.py", "tests/test_write_licence_agent.py",
    "tests/test_repo_hygiene.py", "tests/test_invariants.py",
)


def _normalize_path(path: str) -> str:
    """One shape for every spelling of the same file, so an alias cannot
    walk past the kernel list (invariant A-048 "trust"):

      separators   `\\` -> `/`, runs collapsed (`friday//policy.py`)
      dot segments `./` and `x/../` resolved lexically, never via the
                   filesystem (a symlink is resolved by the caller with
                   the real tree; this is the name check)
      case         folded - Windows and macOS mount case-insensitively,
                   so `FRIDAY/POLICY.PY` IS policy.py there
      whitespace   stripped at both ends; a trailing `/` dropped
    """
    text = (path or "").strip().replace("\\", "/")
    parts: list[str] = []
    for seg in text.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts).casefold()


#: KERNEL_PATHS, normalized once, compared against normalized input.
_KERNEL_NORMALIZED = tuple(
    (_normalize_path(k) + ("/" if k.endswith("/") else ""), k) for k in KERNEL_PATHS)


def is_kernel_path(path: str) -> str | None:
    """The kernel entry a path falls under, or None.

    Matches by the normalized name (see `_normalize_path`); a repo-relative
    or absolute path whose tail is a kernel entry matches, so sandbox
    worktrees and absolute Windows paths are covered. The caller that owns
    a real checkout resolves symlinks first (`selfdev` passes the diff's
    paths, which git reports link-free)."""
    normalized = _normalize_path(path)
    if not normalized:
        return None
    for kernel_norm, kernel in _KERNEL_NORMALIZED:
        if kernel_norm.endswith("/"):
            if normalized.startswith(kernel_norm) or f"/{kernel_norm}" in normalized + "/":
                return kernel
        elif normalized == kernel_norm or normalized.endswith("/" + kernel_norm):
            return kernel
    return None

_SCOPE_TO_TESTS = {
    TARGETED: ["tests/test_execution_economics.py"],
    AFFECTED: [],          # caller names the affected tests explicitly
    INTEGRATION: ["tests"],
    FULL: ["tests"],
}


class UpgradeRefused(RuntimeError):
    pass


class SelfUpgrade:
    def __init__(self, repo: str | Path, *,
                 journal: str | Path | None = None) -> None:
        self.repo = Path(repo)
        self.journal_path = Path(journal) if journal else (
            self.repo / "data" / "upgrade_journal.jsonl")
        self.checkpoint_ref: str | None = None

    # -- journal -----------------------------------------------------------

    def _log(self, step: str, **detail) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.journal_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), "step": step,
                                 **detail}) + "\n")

    # -- git helpers -------------------------------------------------------

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.repo).replace("\\", "/"), *args],
            capture_output=True, text=True, timeout=120)

    # -- the protocol ------------------------------------------------------

    def guard_kernel(self, files: list[str]) -> None:
        """Step 0: Level-6 refusal, before anything is touched."""
        for f in files:
            kernel = is_kernel_path(f)
            if kernel:
                self._log("refused_kernel", file=f, kernel=kernel)
                raise UpgradeRefused(
                    f"{f} is constitutional-kernel surface ({kernel}): "
                    "self-upgrade is not authorized at any level - "
                    "changes go through the human policy process")

    def checkpoint(self) -> str:
        """Step 1: rollback target BEFORE any change."""
        result = self._git("stash", "create",
                           "self-upgrade checkpoint")
        ref = (result.stdout or "").strip()
        if not ref:
            # clean tree: HEAD is the checkpoint
            ref = self._git("rev-parse", "HEAD").stdout.strip()
        self.checkpoint_ref = ref
        self._log("checkpoint", ref=ref)
        return ref

    def verification_plan(self, description: str,
                          affected_tests: list[str] | None = None
                          ) -> tuple[str, list[str]]:
        """Step 2: price the verification by blast radius, not habit."""
        econ = classify_task(description)
        scope, reason = verification_depth(econ)
        tests = list(affected_tests or [])
        if not tests:
            tests = list(_SCOPE_TO_TESTS.get(scope, ["tests"]))
        self._log("verification_plan", scope=scope, reason=reason,
                  tests=tests)
        return scope, tests

    def run_tests(self, tests: list[str],
                  timeout: int = 1200) -> tuple[bool, str]:
        """Step 3: the change class's tests, for real."""
        cmd = [str(self.repo / ".venv" / "Scripts" / "python.exe"),
               "-m", "pytest", "-q", "-p", "no:cacheprovider",
               "-m", "not live and not slow", *tests]
        result = subprocess.run(cmd, cwd=str(self.repo),
                                capture_output=True, text=True,
                                timeout=timeout)
        tail = "\n".join((result.stdout or "").splitlines()[-3:])
        passed = result.returncode == 0
        self._log("tests", passed=passed, tail=tail)
        return passed, tail

    def rollback(self) -> dict:
        """The automatic path back to last-known-good."""
        if not self.checkpoint_ref:
            return {"status": "failed", "error": "no checkpoint recorded"}
        head = self._git("rev-parse", "HEAD").stdout.strip()
        if self.checkpoint_ref == head:
            # checkpoint was clean HEAD: discard working changes
            self._git("checkout", "--", ".")
        else:
            self._git("checkout", "--", ".")
            self._git("stash", "apply", self.checkpoint_ref)
        self._log("rollback", to=self.checkpoint_ref)
        return {"status": "rolled_back", "to": self.checkpoint_ref}

    def upgrade(self, *, description: str, files: list[str], apply_change,
                affected_tests: list[str] | None = None,
                live_check=None) -> dict:
        """
        The loop: guard -> checkpoint -> apply -> tests -> live check ->
        promote | rollback. `apply_change()` performs the edit;
        `live_check()` (optional) returns truthy on healthy live
        behavior.
        """
        self.guard_kernel(files)
        self.checkpoint()
        scope, tests = self.verification_plan(description, affected_tests)
        try:
            apply_change()
        except Exception as exc:                             # noqa: BLE001
            self.rollback()
            return {"status": "rolled_back",
                    "stage": "apply", "error": str(exc)[:500]}
        passed, tail = self.run_tests(tests)
        if not passed:
            self.rollback()
            return {"status": "rolled_back", "stage": "tests",
                    "detail": tail}
        if live_check is not None:
            try:
                healthy = bool(live_check())
            except Exception as exc:                         # noqa: BLE001
                healthy = False
                tail = str(exc)[:500]
            if not healthy:
                self.rollback()
                return {"status": "rolled_back", "stage": "live_check",
                        "detail": tail}
        self._log("promoted", description=description, files=files,
                  scope=scope)
        return {"status": "promoted", "scope": scope, "tests": tests}
