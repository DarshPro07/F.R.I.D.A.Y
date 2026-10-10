"""
Guarded transitions, versions with rollback, quarantine.

    DISCOVER -> DRAFT/QUARANTINED -> STAGED -> TESTING -> ACTIVE
                                                            |
                              (dependency changed)  NEEDS_REVALIDATION -> ACTIVE | STALE | BROKEN
                              (quality fell)        DEGRADED -> rollback -> ACTIVE
                              (unsafe content)      QUARANTINED -> DRAFT -> ...

Every arrival at ACTIVE needs an EvaluationReport that the evaluator
recorded for the exact content on disk (nonce, content hash, version). The
skill file cannot supply that; neither can a caller by constructing a
report - the registry has to have seen it. A rollback restores a snapshot
whose recorded evaluation is replayed through the same check.

Agent-created and downloaded skills start in QUARANTINE and leave it only
through lint (`stage`). High-risk classes never activate through an
agent-created path; the owner's approval is a different trust level, not
a flag.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from friday.skills import contracts as K
from friday.skills import files as F
from friday.skills import lint as L
from friday.skills.evaluator import SkillEvaluator

#: Below this the skill is DEGRADED; with a verified previous version, it
#: rolls back on its own.
QUALITY_FLOOR = 0.4

#: States from which `activate` may proceed once evidence is on record.
ACTIVATABLE = (K.STAGED, K.TESTING, K.NEEDS_REVALIDATION, K.DEGRADED, K.DISABLED, K.STALE, K.BROKEN)

#: What "clean" means for the SkillSpector gate: no high or critical finding,
#: a verdict below DO_NOT_INSTALL, and complete coverage (friday.forge reads
#: all three; anything else is not CLEAN). Bump it when that rule changes so
#: a report kept under the old rule is scanned again instead of reused.
SCAN_POLICY_VERSION = 1


def default_scan(directory: Path):
    """The SkillSpector scan of a skill directory: the dedicated pinned
    environment's strict command when it exists, else forge's own command
    (FRIDAY_SKILLSPECTOR_CMD or `skillspector` on PATH, UNAVAILABLE when
    neither is there). Resolved per call, so a repaired install is picked up."""
    from friday import forge
    from friday.fabric_adapters import skillspector as ss

    exe = None if os.environ.get(forge.SKILLSPECTOR_ENV) else ss.executable()
    if exe is None:
        return forge.scan_import(directory)
    return forge.scan_import(directory, command=[str(exe), *ss.STRICT_ARGS], timeout=ss.SCAN_TIMEOUT)


def record_from_skill(skill: F.SkillFiles, *, tier: str, trust: str, scope: str | None = None,
                      source: str = "", author: str = "", state: str = K.DISCOVERED) -> K.SkillRecord:
    """A registry record built from the SKILL.md, with FRIDAY's judgment
    (risk from the content, state from the trust) on top."""
    frontmatter, body = skill.read()
    friday = F.friday_metadata(frontmatter)
    hermes = F.hermes_metadata(frontmatter)
    declared_scope = str(friday.get("scope", "") or "").upper()
    resolved_scope = scope or (declared_scope if declared_scope in K.SCOPES else K.USER)
    publish = str(friday.get("publish", K.PUBLISH_LOCAL) or K.PUBLISH_LOCAL).lower()
    if publish not in K.PUBLISH_LEVELS:
        publish = K.PUBLISH_LOCAL
    return K.SkillRecord(
        name=skill.name,
        description=str(frontmatter.get("description", "") or "").strip(),
        version=str(frontmatter.get("version", "") or "1.0.0"),
        scope=resolved_scope,
        tier=tier,
        state=state,
        path=str(skill.dir),
        source=source,
        author=str(frontmatter.get("author", "") or author),
        trust=trust,
        risk=L.classify_risk(body),          # the procedure, not the trigger phrases
        content_hash=skill.content_hash(),
        category=str(hermes.get("category", "") or friday.get("category", "") or ""),
        tags=F.as_list(hermes.get("tags")),
        platforms=F.as_list(frontmatter.get("platforms")),
        permissions=F.as_list(friday.get("permissions")),
        dependencies=F.as_list(friday.get("depends_on")),
        tests=F.as_list(friday.get("tests")),
        triggers=F.as_list(friday.get("triggers")),
        never_for=F.as_list(friday.get("never_for")),
        task=str(friday.get("task", "") or ""),
        publish=publish,
    )


class SkillLifecycle:
    def __init__(self, registry, *, snapshots_dir: Path | None = None,
                 evaluator: SkillEvaluator | None = None, graph=None, scanner=None) -> None:
        if snapshots_dir is None:
            from friday.config import SKILLS_DIR

            snapshots_dir = SKILLS_DIR / ".versions"
        self.registry = registry
        self.snapshots_dir = Path(snapshots_dir)
        self.evaluator = evaluator or SkillEvaluator(registry)
        self.graph = graph
        #: Path -> forge.ScanReport. None means `default_scan`.
        self.scanner = scanner

    # -- registration -------------------------------------------------------------

    def register(self, skill: F.SkillFiles, *, tier: str, trust: str, scope: str | None = None,
                 source: str = "", author: str = "", actor: str = "friday") -> K.SkillRecord:
        """Bring a skill on disk under management. Owner-written and built-in
        skills start as DRAFT; anything an agent wrote or that came from
        outside starts QUARANTINED until lint clears it."""
        if not skill.exists():
            raise K.SkillError(f"{skill.skill_md} does not exist")
        existing = self.registry.get(skill.name)
        record = record_from_skill(skill, tier=tier, trust=trust, scope=scope, source=source, author=author)
        if existing is not None:
            record.state = existing.state
            record.created_at = existing.created_at
            record.quality_score = existing.quality_score
            record.usage_count, record.success_count = existing.usage_count, existing.success_count
            record.failure_count, record.correction_count = existing.failure_count, existing.correction_count
            record.last_verified = existing.last_verified
            self.registry.upsert(record)
            if existing.content_hash and existing.content_hash != record.content_hash and existing.state == K.ACTIVE:
                # The verified evidence no longer describes this content:
                # it is unverified for a moment, then quarantined until lint
                # and tests clear it again (SKILL-006).
                self.registry.transition(skill.name, K.UPDATE_UNVERIFIED, actor=actor,
                                         reason="content changed on disk since the verified version")
                self.registry.transition(skill.name, K.QUARANTINED, actor=actor,
                                         reason="unverified update: quarantined until lint and tests pass")
                record.state = K.QUARANTINED
            return record
        record.state = K.DISCOVERED
        self.registry.upsert(record)
        first = K.DRAFT if trust in (K.TRUST_OWNER, K.TRUST_BUILT_IN) else K.QUARANTINED
        self.registry.transition(skill.name, first, actor=actor,
                                 reason=f"registered from {tier} with trust {trust}")
        record.state = first
        if self.graph is not None:
            self.graph.index(skill)
        return record

    # -- staging ----------------------------------------------------------------------

    def stage(self, name: str, *, actor: str = "friday") -> tuple[K.SkillRecord, L.LintReport]:
        """DRAFT/QUARANTINED -> STAGED when lint has no FAIL; otherwise into
        (or staying in) QUARANTINE with the failures named."""
        record = self._require(name)
        skill = F.SkillFiles(Path(record.path))
        report = L.lint(skill)
        if report.verdict == L.FAIL:
            codes = ", ".join(sorted({f.code for f in report.failures()}))
            if record.state != K.QUARANTINED:
                record = self.registry.transition(name, K.QUARANTINED, actor=actor, reason=f"lint failed: {codes}")
            else:
                self.registry.note(name, f"lint still failing: {codes}", actor=actor)
            raise K.TransitionRefused(f"{name}: lint failed ({codes})")
        if record.state == K.QUARANTINED:
            record = self.registry.transition(name, K.DRAFT, actor=actor, reason="lint passed; leaving quarantine")
        if record.state == K.DRAFT:
            record = self.registry.transition(name, K.STAGED, actor=actor, reason="lint " + report.verdict.lower())
        return record, report

    # -- activation -----------------------------------------------------------------

    def activate(self, name: str, report: K.EvaluationReport, *, actor: str = "friday") -> K.SkillRecord:
        """The one door to ACTIVE. Evidence first, then the transition, then
        a snapshot of exactly what was verified."""
        record = self._require(name)
        skill = F.SkillFiles(Path(record.path))
        if report.skill != name:
            raise K.TransitionRefused(f"the report is for {report.skill!r}, not {name!r}")
        if not report.passed:
            raise K.TransitionRefused(f"{name}: the evaluation did not pass: " + "; ".join(report.failures())[:300])
        if not self.registry.evaluation_on_record(report):
            raise K.TransitionRefused(f"{name}: that evaluation is not on record - only the evaluator can produce evidence")
        current_hash = skill.content_hash()
        if report.content_hash != current_hash:
            raise K.TransitionRefused(f"{name}: the skill changed since it was evaluated")
        if record.risk in K.HIGH_RISK and record.trust == K.TRUST_AGENT:
            raise K.TransitionRefused(f"{name}: {record.risk} content cannot activate through an agent-created path")
        if record.state not in ACTIVATABLE:
            raise K.TransitionRefused(f"{name}: cannot activate from {record.state}")
        record.version = report.version or record.version
        record.content_hash = current_hash
        self._require_clean_scan(record, skill, actor)
        if self.graph is not None:
            # The codebase fingerprint: which repository, at which commit,
            # this version was verified against. Empty outside git.
            record.codebase_id, record.codebase_commit = self.graph.identity()
        self.registry.upsert(record)
        record = self.registry.transition(name, K.ACTIVE, actor=actor, reason=f"evaluation {report.nonce} passed; skillspector scan clean")
        snapshot = F.snapshot(skill, self.snapshots_dir / name / record.version)
        self.registry.record_version(name, record.version, current_hash, snapshot_path=str(snapshot),
                                     evaluation=report)
        if self.graph is not None:
            self.graph.index(skill)
        self.evaluator.rescore(name)
        return self.registry.get(name)

    def _require_clean_scan(self, record: K.SkillRecord, skill: F.SkillFiles, actor: str) -> None:
        """Every skill, whatever its trust, needs a SkillSpector scan that is
        CLEAN for exactly the tree on disk. Missing, failing, raising,
        incomplete or over-threshold scanners all refuse; none is approval.
        The scan is kept beside the version snapshot whatever the outcome; a
        condemning finding quarantines the skill where the lifecycle allows
        it. A kept CLEAN report for this very tree, from the pinned scanner
        under the current policy, is reused, so an unchanged verified skill
        does not depend on the scanner being installed."""
        from friday import forge

        before = forge.tree_digest(skill.dir)
        kept = self.snapshots_dir / record.name / f"{record.version}.skillspector.json"
        if self._kept_scan_still_holds(kept, before):
            return
        try:
            report = (self.scanner or default_scan)(skill.dir)
        except Exception as exc:  # a crashing scanner is a refusal with its cause, never a pass
            report = forge.ScanReport(status=forge.SCAN_ERROR, detail=f"the scanner raised {exc!r}")
        scan = report.to_dict()
        scan["policy_version"] = SCAN_POLICY_VERSION
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps(scan, indent=2, sort_keys=True), encoding="utf-8")
        if scan.get("target_sha256") == before and forge.scan_admits(scan, skill.dir):
            return
        status = scan.get("status")
        if status == forge.SCAN_BLOCKED:
            why = f"{scan.get('blocking', 0)} blocking finding(s)"
        elif status == forge.SCAN_CLEAN:
            why = "the tree changed during the scan"
        else:
            why = scan.get("detail") or "not scanned"
        reason = f"skillspector scan {status}: {why}"
        if status == forge.SCAN_BLOCKED and K.QUARANTINED in K.TRANSITIONS.get(record.state, ()):
            self.registry.transition(record.name, K.QUARANTINED, actor=actor, reason=reason)
        else:
            self.registry.note(record.name, reason, actor=actor)
        raise K.TransitionRefused(f"{record.name}: {reason}")

    @staticmethod
    def _kept_scan_still_holds(kept: Path, tree_digest: str) -> bool:
        """The kept report is CLEAN for this tree, under this policy, from the pinned scanner."""
        from friday import forge

        try:
            scan = json.loads(kept.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (isinstance(scan, dict) and scan.get("status") == forge.SCAN_CLEAN
                and scan.get("target_sha256") == tree_digest
                and scan.get("policy_version") == SCAN_POLICY_VERSION
                and str(scan.get("scanner_version") or "").lstrip("v") == forge.SKILLSPECTOR_RELEASE.lstrip("v"))

    def evaluate_and_activate(self, name: str, *, run_tests: bool = True, actor: str = "friday") -> tuple[K.EvaluationReport, K.SkillRecord]:
        record = self._require(name)
        skill = F.SkillFiles(Path(record.path))
        if record.state == K.STAGED:
            record = self.registry.transition(name, K.TESTING, actor=actor, reason="evaluation started")
        report = self.evaluator.evaluate(skill, record, run_tests=run_tests)
        if report.passed:
            return report, self.activate(name, report, actor=actor)
        failures = "; ".join(report.failures())[:300]
        for stage, state, detail in report.checks:
            if stage in ("overlap", "value") and state == "FAIL":
                # Why it stays inactive is its own history row, not the tail of a truncated list.
                self.registry.note(name, f"inactive: {stage}: {detail}"[:400], actor=actor)
        if record.state == K.NEEDS_REVALIDATION:
            record = self.registry.transition(name, K.BROKEN, actor=actor, reason=f"revalidation failed: {failures}")
        elif record.state == K.TESTING:
            record = self.registry.transition(name, K.DRAFT, actor=actor, reason=f"evaluation failed: {failures}")
        else:
            self.registry.note(name, f"evaluation failed: {failures}", actor=actor)
        return report, self.registry.get(name)

    # -- revalidation after a code change ---------------------------------------------

    def revalidate(self, name: str, *, run_tests: bool = True, actor: str = "revalidation") -> tuple[str, K.EvaluationReport | None]:
        """
        For a NEEDS_REVALIDATION skill: if nothing it depends on actually
        differs from the verified fingerprints, it is FRESH again without a
        test; otherwise the evaluation decides ACTIVE (fresh) or BROKEN.
        """
        record = self._require(name)
        if record.state != K.NEEDS_REVALIDATION:
            return record.state, None
        if self.graph is not None and self.graph.check(name)["fresh"]:
            self.registry.transition(name, K.ACTIVE, actor=actor, reason="dependencies match the verified fingerprints")
            return "FRESH", None
        report, record = self.evaluate_and_activate(name, run_tests=run_tests, actor=actor)
        if report.passed and self.graph is not None:
            self.graph.refresh(name)
        return ("FRESH" if report.passed else K.BROKEN), report

    # -- versions -----------------------------------------------------------------------

    def propose_update(self, name: str, *, bump: str, actor: str = "friday") -> K.SkillRecord:
        """The content on disk is a new candidate version: stamp the version,
        take the skill back to STAGED. Nothing is verified yet."""
        record = self._require(name)
        skill = F.SkillFiles(Path(record.path))
        frontmatter, body = skill.read()
        current = str(frontmatter.get("version") or record.version)
        # "keep": the file already carries its version (a shipped upgrade).
        new_version = current if bump == "keep" else K.bump_version(current, bump)
        frontmatter["version"] = new_version
        skill.write(frontmatter, body)
        # Re-read everything from the file: a new version may declare new
        # tests, dependencies or triggers, and the evaluation must judge
        # those, not the ones the previous version was verified with.
        record = self.register(skill, tier=record.tier, trust=record.trust, scope=record.scope,
                               source=record.source, actor=actor)
        if record.state == K.ACTIVE:
            record = self.registry.transition(name, K.DRAFT, actor=actor,
                                              reason=f"update proposed: -> {new_version}")
        staged, _ = self.stage(name, actor=actor)
        return staged

    def update(self, name: str, *, bump: str = "minor", run_tests: bool = True,
               actor: str = "friday") -> dict:
        """Propose, evaluate, and either activate the new version or put the
        last verified one back. The previous version never disappears."""
        before = self._require(name)
        previous = self.registry.last_verified_version(name)
        try:
            self.propose_update(name, bump=bump, actor=actor)
        except K.TransitionRefused as exc:
            restored = self.rollback(name, actor=actor, reason=f"update refused by lint: {exc}") if previous else None
            return {"skill": name, "outcome": "refused", "reason": str(exc),
                    "active_version": restored.version if restored else before.version}
        report, record = self.evaluate_and_activate(name, run_tests=run_tests, actor=actor)
        if report.passed:
            return {"skill": name, "outcome": "activated", "version": record.version, "previous": before.version}
        restored = self.rollback(name, actor=actor,
                                 reason=f"new version failed: {'; '.join(report.failures())[:200]}") if previous else None
        return {"skill": name, "outcome": "rolled_back" if restored else "failed",
                "reason": "; ".join(report.failures())[:300],
                "active_version": restored.version if restored else None}

    def rollback(self, name: str, *, actor: str, reason: str, to_version: str | None = None) -> K.SkillRecord:
        """Restore the newest verified snapshot (or `to_version`) and make it
        ACTIVE again through the same evidence check as any activation."""
        record = self._require(name)
        versions = [v for v in self.registry.versions(name) if v["verified"] and v["snapshot_path"]]
        if to_version is not None:
            versions = [v for v in versions if v["version"] == to_version]
        else:
            versions = [v for v in versions if K.parse_version(v["version"]) < K.parse_version(record.version)] or versions
        if not versions:
            raise K.SkillError(f"{name}: no verified version to roll back to")
        target = max(versions, key=lambda v: K.parse_version(v["version"]))
        skill = F.SkillFiles(Path(record.path))
        F.restore(Path(target["snapshot_path"]), skill.dir)
        try:
            stored = json.loads(target["evaluation"] or "{}")
            report = K.EvaluationReport(**{k: v for k, v in stored.items() if k in K.EvaluationReport.__dataclass_fields__})
            report.checks = [tuple(c) for c in report.checks]
        except (TypeError, ValueError) as exc:
            raise K.SkillError(f"{name}: the stored evaluation for {target['version']} is unreadable: {exc}") from exc
        record.version = target["version"]
        record.content_hash = skill.content_hash()
        self.registry.upsert(record)
        self.registry.note(name, f"rolled back to {target['version']}: {reason}", actor=actor)
        if record.state == K.ACTIVE:
            return self.registry.get(name)
        if record.state in (K.DRAFT, K.QUARANTINED):
            # The restored content passed lint when it was verified; staging
            # it again keeps the table strict (never DRAFT -> ACTIVE) at the
            # cost of one lint run.
            self.stage(name, actor=actor)
        return self.activate(name, report, actor=actor)

    # -- quality ------------------------------------------------------------------------

    def degrade_if_needed(self, name: str, *, floor: float = QUALITY_FLOOR, auto_rollback: bool = True,
                          actor: str = "quality") -> K.SkillRecord:
        record = self._require(name)
        score = self.evaluator.rescore(name)
        if record.state != K.ACTIVE or score >= floor:
            return self.registry.get(name)
        record = self.registry.transition(name, K.DEGRADED, actor=actor, reason=f"quality {score} below {floor}")
        if auto_rollback and self.registry.last_verified_version(name, before=record.version):
            return self.rollback(name, actor=actor, reason=f"quality {score} below {floor}")
        return record

    # -- the owner's switches --------------------------------------------------------------

    def quarantine(self, name: str, *, reason: str, actor: str) -> K.SkillRecord:
        return self.registry.transition(name, K.QUARANTINED, actor=actor, reason=reason)

    def deprecate(self, name: str, *, reason: str, actor: str) -> K.SkillRecord:
        record = self._require(name)
        if record.state in (K.ACTIVE, K.DRAFT, K.DEGRADED, K.STALE, K.BROKEN, K.QUARANTINED):
            return self.registry.transition(name, K.DEPRECATED, actor=actor, reason=reason)
        raise K.TransitionRefused(f"{name}: cannot deprecate from {record.state}")

    def disable(self, name: str, *, reason: str, actor: str) -> K.SkillRecord:
        return self.registry.transition(name, K.DISABLED, actor=actor, reason=reason)

    # -- built-in skills: shipped copies the owner may edit ---------------------------------

    @staticmethod
    def _adopted_hash(record: K.SkillRecord) -> str:
        return record.source.split(":", 1)[1] if record.source.startswith("builtin:") else ""

    def adopt_builtin(self, name: str, *, roots: dict[str, Path], actor: str = "friday") -> K.SkillRecord:
        """Copy a shipped skill into the user tier, where it can be edited,
        tested and activated. The registry remembers exactly which shipped
        content it came from, so a later upgrade knows what the owner changed."""
        if K.BUILT_IN not in roots or K.LOCAL_USER not in roots:
            raise K.SkillError("built-in and user tiers must both be configured")
        source = F.SkillFiles(Path(roots[K.BUILT_IN]) / name)
        if not source.exists():
            raise K.SkillError(f"no built-in skill named {name!r}")
        target = F.SkillFiles(Path(roots[K.LOCAL_USER]) / name)
        if target.exists():
            raise K.SkillError(f"{name} is already adopted at {target.dir}")
        F.snapshot(source, target.dir)
        return self.register(target, tier=K.LOCAL_USER, trust=K.TRUST_BUILT_IN,
                             source=f"builtin:{source.content_hash()}", actor=actor)

    def builtin_updates(self, *, roots: dict[str, Path]) -> list[dict]:
        """Adopted copies whose shipped original changed, and whether the
        owner changed their copy meanwhile."""
        found = []
        for record in self.registry.list(tier=K.LOCAL_USER):
            adopted = self._adopted_hash(record)
            if not adopted or K.BUILT_IN not in roots:
                continue
            upstream = F.SkillFiles(Path(roots[K.BUILT_IN]) / record.name)
            local = F.SkillFiles(Path(record.path))
            if not upstream.exists() or not local.exists():
                continue
            found.append({"skill": record.name,
                          "upstream_changed": upstream.content_hash() != adopted,
                          "locally_modified": local.content_hash() != adopted,
                          "state": record.state, "version": record.version})
        return found

    def upgrade_builtin(self, name: str, *, roots: dict[str, Path], run_tests: bool = True,
                        actor: str = "friday") -> dict:
        """
        Bring an adopted copy up to the shipped version:

            untouched copy   -> replaced by the new shipped content
            edited copy      -> the shipped procedure's new lines are merged
                                under the owner's edits (never overwritten)

        Either way the result is a new version that is evaluated; a failure
        rolls back to the last verified one, so an upgrade can never leave
        the owner with a broken skill or without their edits.
        """
        record = self._require(name)
        adopted = self._adopted_hash(record)
        if not adopted:
            raise K.SkillError(f"{name} was not adopted from a built-in skill")
        upstream = F.SkillFiles(Path(roots[K.BUILT_IN]) / name)
        if not upstream.exists():
            raise K.SkillError(f"the shipped {name} is gone")
        local = F.SkillFiles(Path(record.path))
        upstream_hash = upstream.content_hash()
        if upstream_hash == adopted:
            return {"skill": name, "outcome": "current", "merge": "shipped content unchanged"}
        modified = local.content_hash() != adopted
        if not modified:
            F.snapshot(upstream, local.dir)
            merge = "replaced (the copy was untouched)"
            bump = "keep"
        else:
            from friday.skills.curator import SkillCurator

            up_frontmatter, up_body = upstream.read()
            lo_frontmatter, lo_body = local.read()
            merged, added = SkillCurator.merge_into(lo_body, up_body)
            if added == 0:
                record.source = f"builtin:{upstream_hash}"
                self.registry.upsert(record)
                return {"skill": name, "outcome": "current", "merge": "nothing new to merge"}
            # The shipped frontmatter is authoritative (its tests, dependencies
            # and version are what the new procedure was verified with); the
            # owner's edits live in the body and are kept beneath the merge.
            local.write(up_frontmatter, merged)
            merge = f"merged {added} new line(s) under the owner's edits"
            bump = "keep"
        record = self.registry.get(name)
        record.source = f"builtin:{upstream_hash}"
        self.registry.upsert(record)
        result = self.update(name, bump=bump, run_tests=run_tests, actor=actor)
        result["merge"] = merge
        return result

    def _require(self, name: str) -> K.SkillRecord:
        record = self.registry.get(name)
        if record is None:
            raise K.SkillError(f"no skill named {name!r}")
        return record
