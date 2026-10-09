"""
Pattern extraction: take what a repository teaches without installing it.

Some repositories are worth learning from and not worth running: the runtime
is heavier than the idea, couples to a model or a memory FRIDAY already has,
or executes more than FRIDAY should let it. For those the right outcome is
neither "install" nor "ignore" but EXTRACT_PATTERN: write the pattern down in
words, then build it natively as a FRIDAY capability.

What this module records is a decision, not a download. The invariants are
enforced by `PatternDecision` itself rather than left to the caller:

    install_runtime is False   the repository's runtime is never installed
    code_copied is False       the native build is written from the
                               description, so no upstream licence terms
                               attach to FRIDAY's source

The repository is read once, as data, through `repo_classifier` (which never
imports, executes or installs it) to put its licence and class on the record.
After that the description stands alone: `implement_natively` needs only the
decision and FRIDAY's own source, so the clone can be deleted.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from friday.config import DATA_DIR

logger = logging.getLogger("friday.pattern_extraction")

DECISIONS_PATH = DATA_DIR / "forge" / "pattern_decisions.jsonl"

EXTRACT_PATTERN = "EXTRACT_PATTERN"

#: The owner's adoption outcomes (docs/plans/build-harness/
#: 00-owner-directive-2026-10-02d.md, `recommended_adoption`). Only
#: EXTRACT_PATTERN has a recorder here; the rest belong to CAP-FORGE-014.
DECISIONS = ("USE_DIRECTLY", "WRAP", EXTRACT_PATTERN, "BUILD_ADAPTER",
             "REFERENCE_ONLY", "REJECT")

MAX_TEXT = 2000
MAX_ITEMS = 20
MAX_ITEM = 200


class PatternError(ValueError):
    """A pattern decision that would break one of the invariants above."""


def _clean(value: str, field: str, limit: int = MAX_TEXT) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise PatternError(f"{field} is required")
    if len(text) > limit:
        raise PatternError(f"{field} is {len(text)} characters; the limit is {limit}")
    return text


def _items(values, field: str, *, required: bool) -> tuple[str, ...]:
    if isinstance(values, str) or not hasattr(values, "__iter__"):
        raise PatternError(f"{field} must be a list of short descriptions")
    items = tuple(_clean(v, field, MAX_ITEM) for v in values)
    if required and not items:
        raise PatternError(f"{field} must name at least one part of the pattern")
    if len(items) > MAX_ITEMS:
        raise PatternError(f"{field} has {len(items)} entries; the limit is {MAX_ITEMS}")
    return items


@dataclass(frozen=True)
class PatternDecision:
    """
    One repository, one pattern, one reason it is simpler or safer to build
    than to install.

    `description` is the pattern in words and `elements` its adopted parts:
    together they are the whole specification, which is why the native build
    never needs the repository. `not_adopted` names what stays out (runtime,
    shell access, model or memory coupling) so the omission is on the record.
    """
    repository: str
    pattern: str
    description: str
    elements: tuple[str, ...]
    rationale: str
    not_adopted: tuple[str, ...] = ()
    license: str = ""
    source_commit: str = ""
    repo_class: str = ""
    decision: str = EXTRACT_PATTERN
    install_runtime: bool = False
    code_copied: bool = False
    decided_at: str = ""

    def __post_init__(self) -> None:
        if self.decision != EXTRACT_PATTERN:
            raise PatternError(
                f"this records {EXTRACT_PATTERN} only, not {self.decision!r}")
        if self.install_runtime:
            raise PatternError(
                "a pattern is extracted precisely so the repository is not "
                "installed; install_runtime must stay False")
        if self.code_copied:
            raise PatternError(
                "a pattern is a description, not source; code_copied must "
                "stay False")
        set_ = object.__setattr__
        set_(self, "repository", _clean(self.repository, "repository", MAX_ITEM))
        set_(self, "pattern", _clean(self.pattern, "pattern", MAX_ITEM))
        set_(self, "description", _clean(self.description, "description"))
        set_(self, "rationale", _clean(self.rationale, "rationale"))
        set_(self, "elements", _items(self.elements, "elements", required=True))
        set_(self, "not_adopted", _items(self.not_adopted, "not_adopted", required=False))
        if not self.decided_at:
            from friday.contracts import now_iso

            set_(self, "decided_at", now_iso())

    def to_dict(self) -> dict:
        raw = asdict(self)
        raw["elements"] = list(self.elements)
        raw["not_adopted"] = list(self.not_adopted)
        return raw

    @classmethod
    def from_dict(cls, raw: dict) -> "PatternDecision":
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        known["elements"] = tuple(known.get("elements", ()))
        known["not_adopted"] = tuple(known.get("not_adopted", ()))
        return cls(**known)


def from_repository(path: str | os.PathLike, *, repository: str, pattern: str,
                    description: str, elements, rationale: str,
                    not_adopted=(), source_commit: str = "") -> PatternDecision:
    """
    Describe a pattern found in a local clone, putting the clone's licence and
    class on the record. The tree is only read; nothing in it is imported,
    executed or installed.
    """
    from friday import repo_classifier

    dossier = repo_classifier.build_dossier(path)
    classification = repo_classifier.classify(dossier)
    return PatternDecision(
        repository=repository, pattern=pattern, description=description,
        elements=tuple(elements), rationale=rationale,
        not_adopted=tuple(not_adopted), license=dossier.license,
        source_commit=source_commit, repo_class=classification.repo_class)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

def record(decision: PatternDecision, *, path: Path | None = None) -> dict:
    """Append the decision to the log. Append-only: a changed mind is a new line."""
    target = Path(path or DECISIONS_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    row = decision.to_dict()
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    logger.info("recorded %s for %s: %s", EXTRACT_PATTERN,
                decision.repository, decision.pattern)
    return row


def decisions(*, path: Path | None = None,
              repository: str | None = None) -> list[PatternDecision]:
    """Every recorded decision, oldest first; unreadable lines are skipped and logged."""
    target = Path(path or DECISIONS_PATH)
    if not target.is_file():
        return []
    found: list[PatternDecision] = []
    for number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            decision = PatternDecision.from_dict(json.loads(line))
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("%s line %d is not a pattern decision: %s",
                           target.name, number, exc)
            continue
        if repository is None or decision.repository == repository:
            found.append(decision)
    return found


# ---------------------------------------------------------------------------
# Building it natively
# ---------------------------------------------------------------------------

def _dependency_key(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(".", "_")


def implement_natively(decision: PatternDecision, spec, source: str, *,
                       registry=None, path: Path | None = None) -> dict:
    """
    Submit and verify a FRIDAY-native build of a recorded pattern.

    The build goes through the ordinary forge lifecycle, so it is statically
    gated and run against its own declared cases in a credential-free process.
    It ends VERIFIED (or REJECTED); enabling stays a separate decision made by
    someone other than the forge. Nothing here takes a path to the repository:
    the description is the specification, and the clone may already be gone.
    """
    from friday import forge

    if decision not in decisions(path=path):
        raise PatternError(
            f"no recorded {EXTRACT_PATTERN} decision for {decision.pattern!r} "
            f"from {decision.repository!r}; record it first so the build has "
            "a provenance to point at")
    upstream = _dependency_key(decision.repository.rstrip("/").rsplit("/", 1)[-1])
    if any(_dependency_key(dep) == upstream for dep in spec.dependencies):
        raise PatternError(
            f"the native build depends on {upstream!r}, the repository the "
            "pattern was extracted from; that is installing it by another name")
    provenance = (f"{EXTRACT_PATTERN}:{decision.repository}"
                  f"@{decision.source_commit or 'unpinned'}")
    registry = registry or forge.Registry()
    registry.submit(spec, source, provenance=provenance)
    return registry.verify(spec.name, actor="pattern_extraction")
