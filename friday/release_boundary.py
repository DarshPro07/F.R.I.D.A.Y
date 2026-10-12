"""
The Release Boundary: what an evolution candidate may do on its own.

EVOLAB-003. Extends EVO-003 (authority zones in `friday.policy`), EVO-002 (the
Evolution Archive) and the standing/one-action split of `friday.confirmation`.
A candidate is one of three kinds and the kind decides who may promote it:

  skill / adapter  Promoted with no per-change prompt while the owner's standing
                   authority for that kind is live. The authority is scoped (to
                   kinds, never to core), expiring (it must name a future
                   expiry) and revocable (a revoke is final and immediate). A
                   candidate is low-risk only when its manifest widens no
                   permission, data access or network scope against the
                   manifest it replaces, its paths stay inside its own tree
                   (skills/ or friday/fabric_adapters/) and EVO-003's rule holds
                   (someone other than the builder verified it, with a
                   rollback). A candidate that widens, or whose manifest is
                   missing or malformed, WAITS for the owner: it is not
                   promoted, and nothing here approves it.
  core             Never promoted by a standing authority. It needs two things
                   on that exact candidate: a `scripts/release_gate.py` report
                   (`report.json`) whose commit equals the candidate's full
                   commit, whose tree was clean, which is fresh, and on which
                   every required gate reads PASS; and the owner's one-action
                   yes (`friday.confirmation`) bound to the candidate hash and
                   commit. A yes for another hash or commit does not fit
                   (the fingerprint is recomputed here) and is not spent by a
                   report that already failed.
  any kind         A path that is a protected trust root (EVO-003 zone Z4) is
                   REFUSED whatever authority or yes exists: the owner applies
                   those by hand.

Every decision, including a refusal and a wait, is archived with its evidence.
Promotion is archived as PROMOTE; refusals and waits as REFUSED, which the
archive never counts as a rejection of the experiment.

Standing authorities are an append-only JSON-lines file (`authorities.jsonl`)
beside the archive: one grant or revoke per line, appended in one write and
fsynced. A line that does not parse is a `BoundaryError`: an unreadable file
could hide a revoke, so it fails closed. `grant` and `revoke` are the owner's
acts; they are deliberately not exposed as a tool, for the reason
`PolicyEngine.approve_for_session` is not, and the caller must reach them only
from a real owner answer.

Assumed, revisit if wrong: a release report older than `REPORT_MAX_AGE` (24 h)
is stale; a "low-risk" adapter lives under `friday/fabric_adapters/`; a manifest
widens when it names a permission, data scope or network host the replaced
manifest did not (compared case-folded and trimmed).
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from friday import evolution_archive as A
from friday import policy as P
from friday.config import DATA_DIR

SKILL = "skill"
ADAPTER = "adapter"
CORE = "core"
KINDS = (SKILL, ADAPTER, CORE)
#: What a standing authority can cover. Core is deliberately absent.
STANDING_KINDS = (SKILL, ADAPTER)

PROMOTE = "PROMOTE"
#: Not refused for good, not promoted: the owner has to say something first.
WAIT_OWNER = "WAIT_OWNER"
REFUSED = "REFUSED"

#: The owner's one-action yes for a core candidate (`friday.confirmation`).
CORE_ACTION = "evolution.release_core"

#: The mandatory gates of `scripts/release_gate.py` (PRD section 16). A report
#: missing one of them, or reading anything but PASS, does not release a core.
REQUIRED_GATES = ("Invariants", "Negative controls", "Verification", "Budget",
                  "Persistence", "Provider Routing", "Baseline", "Soak", "Audit")
REPORT_MAX_AGE = timedelta(hours=24)
#: A report stamped slightly ahead of this clock is clock skew, not the future.
_CLOCK_SKEW = timedelta(minutes=5)

MANIFEST_FIELDS = ("permissions", "data_access", "network")
_ADAPTER_PREFIX = "friday/fabric_adapters/"
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_LOCK = threading.RLock()


class BoundaryError(ValueError):
    """An authority is malformed, or the authority file is damaged."""


@dataclass(frozen=True)
class Candidate:
    """One evolution candidate, as the boundary sees it.

    `manifest` / `baseline_manifest` map each of `MANIFEST_FIELDS` to a list of
    names: what the candidate declares and what the version it replaces
    declared (`{}` fields for a brand-new skill that asks for nothing). `commit`
    and `candidate_hash` matter for core only.
    """
    kind: str
    paths: tuple
    built_by: str = ""
    verified_by: str = ""
    rollback: str = ""
    parent_version: str = ""
    candidate_version: str = ""
    objective: str = ""
    hypothesis: str = ""
    baseline: dict = field(default_factory=dict)
    measurements: dict = field(default_factory=dict)
    manifest: dict | None = None
    baseline_manifest: dict | None = None
    commit: str = ""
    candidate_hash: str = ""
    judged_by: tuple = ()
    heldout_passed: bool = False


@dataclass(frozen=True)
class Decision:
    outcome: str
    reasons: tuple
    experiment_id: str = ""
    authority_id: str = ""
    zone: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None \
        and value.utcoffset() is not None


def _name_set(value) -> set[str] | None:
    """A manifest list as a normalised set, or None if it is not a list of names."""
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        return None
    names = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        names.add(" ".join(item.casefold().split()))
    return names


def widened_scopes(manifest, baseline) -> tuple[list[str], list[str]]:
    """(fields that widen, problems). A problem means the manifest cannot be
    judged, which is never treated as "widens nothing"."""
    widening: list[str] = []
    problems: list[str] = []
    for label, value in (("manifest", manifest), ("baseline manifest", baseline)):
        if not isinstance(value, dict):
            problems.append(f"{label} is missing, so widening cannot be ruled out")
    if problems:
        return widening, problems
    for name in MANIFEST_FIELDS:
        new, old = _name_set(manifest.get(name)), _name_set(baseline.get(name))
        if new is None or old is None:
            problems.append(f"{name} is missing or not a list of names in the "
                            f"manifest or the baseline manifest")
        elif new - old:
            widening.append(name)
    return widening, problems


def check_report(report, *, commit: str, now: datetime,
                 max_age: timedelta = REPORT_MAX_AGE) -> list[str]:
    """Why `report` (a `release_gate` report.json) does not release `commit`;
    empty when it does. Each gate is judged on its own verdict: neither the
    report's `ready` flag nor a gate's `mandatory` flag is trusted."""
    if not isinstance(report, dict):
        return ["release report is missing or not a mapping"]
    reasons: list[str] = []
    sha = (report.get("commit") or {}).get("sha") if isinstance(report.get("commit"), dict) else None
    if not isinstance(sha, str) or sha.strip().lower() != commit:
        reasons.append("release report is not for this candidate's commit")
    if not isinstance(report.get("commit"), dict) or report["commit"].get("dirty") is not False:
        reasons.append("release report was not taken on a clean working tree")
    host = report.get("host")
    stamp = host.get("utc") if isinstance(host, dict) else None
    try:
        made = datetime.fromisoformat(stamp)
        if not _aware(made):
            raise ValueError("naive time")
    except (TypeError, ValueError):
        reasons.append("release report has no readable timestamp")
    else:
        if now - made > max_age:
            reasons.append("release report is stale")
        elif made - now > _CLOCK_SKEW:
            reasons.append("release report is dated in the future")
    gates = report.get("gates")
    verdicts: dict[str, list] = {}
    for gate in gates if isinstance(gates, list) else []:
        if isinstance(gate, dict) and isinstance(gate.get("gate"), str):
            verdicts.setdefault(gate["gate"], []).append(gate.get("verdict"))
    for name in REQUIRED_GATES:
        got = verdicts.get(name)
        if not got:
            reasons.append(f"release report has no {name} gate")
        elif any(v != "PASS" for v in got):
            reasons.append(f"release report gate {name} is not PASS")
    for name, got in verdicts.items():
        if name not in REQUIRED_GATES and any(v != "PASS" for v in got):
            reasons.append(f"release report gate {name} is not PASS")
    return reasons


def _report_summary(report) -> dict | None:
    if not isinstance(report, dict):
        return None
    commit = report.get("commit") if isinstance(report.get("commit"), dict) else {}
    host = report.get("host") if isinstance(report.get("host"), dict) else {}
    gates = {}
    for gate in report.get("gates") if isinstance(report.get("gates"), list) else []:
        if isinstance(gate, dict) and isinstance(gate.get("gate"), str):
            gates.setdefault(gate["gate"], str(gate.get("verdict")))
    return {"sha": commit.get("sha"), "dirty": commit.get("dirty"),
            "generated": host.get("utc"), "ready": report.get("ready"), "gates": gates}


class ReleaseBoundary:
    """Decides, and archives, what each evolution candidate may do alone."""

    def __init__(self, root: Path | str | None = None, *,
                 archive: A.EvolutionArchive | None = None) -> None:
        self.root = Path(root) if root is not None else DATA_DIR / "evolution_release"
        self.path = self.root / "authorities.jsonl"
        self.archive = archive or A.EvolutionArchive()

    # -- standing authority ------------------------------------------------

    def _append(self, row: dict) -> None:
        line = (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        with _LOCK:
            self._events()      # never append to a damaged file
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self.path, "ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def _events(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        for number, raw in enumerate(self.path.read_bytes().splitlines(), 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise BoundaryError(f"{self.path} line {number} is damaged: {exc}") from exc
            if not isinstance(row, dict) or row.get("event") not in ("grant", "revoke"):
                raise BoundaryError(f"{self.path} line {number} is not an authority event")
            self._check_event(row, number)
            rows.append(row)
        return rows

    def _check_event(self, row: dict, number: int) -> None:
        """Every field `live_authority` and `revoke` read, present and well-typed."""
        def bad(why: str):
            return BoundaryError(f"{self.path} line {number} is a malformed {row['event']} event: {why}")
        if not isinstance(row.get("id"), str) or not row["id"]:
            raise bad("id must be a non-empty string")
        if row["event"] != "grant":
            return
        scope = row.get("scope")
        if not isinstance(scope, list) or not scope or not all(isinstance(k, str) for k in scope):
            raise bad("scope must be a non-empty list of kinds")
        for key in ("granted_at", "expires_at"):
            try:
                stamp = datetime.fromisoformat(row[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise bad(f"{key} must be an ISO timestamp") from exc
            if not _aware(stamp):
                raise bad(f"{key} must carry a timezone")

    def grant(self, *, scope, expires_at: datetime, granted_by: str,
              now: datetime | None = None) -> dict:
        """The owner's standing low-risk authority for `scope` until `expires_at`."""
        now = now or _now()
        if isinstance(scope, str) or not isinstance(scope, (list, tuple, set, frozenset)) or not scope:
            raise BoundaryError("scope must be a non-empty list of kinds")
        kinds = sorted(set(scope))
        bad = [k for k in kinds if k not in STANDING_KINDS]
        if bad:
            raise BoundaryError(f"a standing authority covers {STANDING_KINDS}, not {bad}: "
                                f"core is released one candidate at a time")
        if not _aware(expires_at) or expires_at <= now:
            raise BoundaryError("a standing authority needs a timezone-aware expiry in the future")
        if not isinstance(granted_by, str) or not granted_by.strip():
            raise BoundaryError("granted_by must name the owner")
        row = {"event": "grant", "id": f"AUTH-{uuid.uuid4().hex[:12]}", "scope": kinds,
               "granted_by": granted_by.strip(), "granted_at": now.isoformat(),
               "expires_at": expires_at.isoformat()}
        self._append(row)
        return row

    def revoke(self, authority_id: str, *, revoked_by: str, now: datetime | None = None) -> dict:
        """Withdraw a grant. Final, and effective from the moment it is written."""
        if not any(r["event"] == "grant" and r.get("id") == authority_id for r in self._events()):
            raise BoundaryError(f"no authority {authority_id!r} to revoke")
        if not isinstance(revoked_by, str) or not revoked_by.strip():
            raise BoundaryError("revoked_by must name the owner")
        row = {"event": "revoke", "id": authority_id, "revoked_by": revoked_by.strip(),
               "revoked_at": (now or _now()).isoformat()}
        self._append(row)
        return row

    def live_authority(self, kind: str, now: datetime | None = None) -> dict | None:
        """A grant covering `kind` that has started, not expired and not been revoked."""
        now = now or _now()
        events = self._events()
        revoked = {r["id"] for r in events if r["event"] == "revoke"}
        for row in events:
            if row["event"] != "grant" or row["id"] in revoked or kind not in row["scope"]:
                continue
            if datetime.fromisoformat(row["granted_at"]) <= now < datetime.fromisoformat(row["expires_at"]):
                return row
        return None

    # -- deciding ------------------------------------------------------------

    def decide(self, candidate: Candidate, *, report=None, book=None, nonce: str | None = None,
               now: datetime | None = None) -> Decision:
        """Decide `candidate`, archive the decision with its evidence, return it.

        `report` is a parsed `release_gate` report.json; `book` and `nonce` are
        the `friday.confirmation` book and the handle of the owner's yes for a
        core candidate.
        """
        now = now or _now()
        # Everything the archive insists on is checked BEFORE any side effect:
        # the owner's single-use yes is spent inside _judge, and a decision that
        # then cannot be archived would burn it for a candidate nobody can record.
        self._require_archivable(candidate)
        outcome, reasons, authority_id = self._judge(candidate, report, book, nonce, now)
        zone = P.zone_of(candidate.paths) if candidate.paths else ""
        evidence = {"outcome": outcome, "reasons": list(reasons), "kind": candidate.kind,
                    "zone": zone, "paths": list(candidate.paths),
                    "candidate_hash": candidate.candidate_hash or None,
                    "commit": candidate.commit or None, "authority_id": authority_id or None,
                    "report": _report_summary(report) if candidate.kind == CORE else None,
                    "decided_at": now.isoformat()}
        row = self.archive.record(
            objective=candidate.objective, parent_version=candidate.parent_version,
            candidate_version=candidate.candidate_version, hypothesis=candidate.hypothesis,
            scope=list(candidate.paths) or [candidate.kind], baseline=candidate.baseline,
            candidate=candidate.measurements, evaluation=evidence,
            decision=A.PROMOTE if outcome == PROMOTE else A.REFUSED,
            rollback_version=(candidate.rollback or "").strip() or candidate.parent_version)
        return Decision(outcome, tuple(reasons), row["id"], authority_id or "", zone)

    @staticmethod
    def _require_archivable(c: Candidate) -> None:
        """Raise BoundaryError if the archive would refuse this candidate's record."""
        try:
            for name in ("objective", "hypothesis", "parent_version", "candidate_version"):
                A._text(name, getattr(c, name))
            if not isinstance(c.rollback, str) or not c.rollback.strip():
                A._text("parent_version (the rollback fallback)", c.parent_version)
            if not isinstance(c.paths, (list, tuple)):
                raise A.ArchiveError("paths must be a list of paths")
            A._scope(list(c.paths) or [c.kind])
            A._measurements("baseline", c.baseline)
            A._measurements("measurements", c.measurements)
        except A.ArchiveError as exc:
            raise BoundaryError(f"candidate cannot be archived, so it is not decided "
                                f"and no authorization is spent: {exc}") from exc

    def _judge(self, c: Candidate, report, book, nonce, now) -> tuple[str, list[str], str]:
        if c.kind not in KINDS:
            return REFUSED, [f"unknown candidate kind {c.kind!r}"], ""
        if not c.paths:
            return REFUSED, ["the candidate names no paths, so its zone cannot be known"], ""
        if P.zone_of(c.paths) == P.Z4:
            return REFUSED, ["a path is a protected trust root: it is never released by "
                             "authority or confirmation, the owner applies it by hand"], ""
        verdict = P.evolution_verdict(P.EvolutionChange(
            paths=tuple(c.paths), built_by=c.built_by, verified_by=c.verified_by,
            heldout_passed=c.heldout_passed, judged_by=tuple(c.judged_by), rollback=c.rollback))
        if verdict.decision == P.DENY:
            return REFUSED, [verdict.reason], ""
        if c.kind == CORE:
            return self._judge_core(c, report, book, nonce, now)
        outside = self._outside_tree(c)
        if outside:
            return REFUSED, [f"{', '.join(outside)} is outside the {c.kind} tree: "
                             f"a change to core is not a {c.kind}"], ""
        waits: list[str] = []
        if verdict.decision != P.AUTO:
            waits.append(verdict.reason)
        widening, problems = widened_scopes(c.manifest, c.baseline_manifest)
        waits += [f"manifest widens {name}: not low-risk" for name in widening]
        waits += [f"manifest cannot be judged: {p}" for p in problems]
        authority = self.live_authority(c.kind, now)
        if authority is None:
            waits.append(f"no live standing authority covers {c.kind} candidates "
                         f"(none granted, expired or revoked)")
        if waits:
            return WAIT_OWNER, waits, ""
        return PROMOTE, [f"{c.kind} widens no scope under standing authority {authority['id']}"], \
            authority["id"]

    @staticmethod
    def _outside_tree(c: Candidate) -> list[str]:
        if c.kind == ADAPTER:
            return [p for p in c.paths if not P._norm_path(p).startswith(_ADAPTER_PREFIX)]
        return [p for p in c.paths if P.zone_of([p]) != P.Z1]

    def _judge_core(self, c: Candidate, report, book, nonce, now) -> tuple[str, list[str], str]:
        reasons: list[str] = []
        commit = (c.commit or "").strip().lower()
        if not _FULL_SHA.match(commit):
            reasons.append("core candidate needs its full 40-hex commit")
        if not isinstance(c.candidate_hash, str) or not c.candidate_hash.strip():
            reasons.append("core candidate needs a candidate hash for the owner to authorize")
        if not reasons:
            reasons = check_report(report, commit=commit, now=now)
        if reasons:
            return REFUSED, reasons, ""
        if not nonce or book is None:
            return WAIT_OWNER, ["release report passes; waiting for the owner to authorize "
                                "this candidate hash"], ""
        spent = book.consume(nonce, run_id=c.candidate_version, action=CORE_ACTION,
                             target=c.candidate_hash, arguments={"commit": commit})
        if not spent.ok:
            return REFUSED, [f"owner authorization refused: {spent.reason}"], ""
        return PROMOTE, ["every required release gate passed on this commit and the owner "
                         "authorized this candidate hash"], ""
