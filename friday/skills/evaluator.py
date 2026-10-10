"""
Whether a skill still works, and how well.

    lint          well-formed, safe, no secrets            (friday.skills.lint)
    risk          classified from the content; an agent-created skill in a
                  high-risk class is refused here, before any activation
    trigger       the declared phrases select it           (friday.skills.router)
    non-trigger   the declared `never_for` phrases do not
    procedure     the declared tests pass                  (subprocess, bounded)
    tokens        loading it stays within budget
    overlap       not a near-duplicate of a skill that is already active
    value         measured lift: tasks done with the skill against the same
                  kind of task done without it (recorded outcomes, both arms)

The result is an EvaluationReport, recorded in the registry under its
nonce. That record - not a line in the SKILL.md, not the skill's own
metadata - is the only thing the lifecycle accepts as evidence for
activation. A skill cannot mark itself verified.

Quality is arithmetic over FRIDAY's own outcomes (successes, failures,
corrections, freshness, token cost), never one model's opinion.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from friday.skills import contracts as K
from friday.skills import files as F
from friday.skills import lint as L
from friday.skills import router as RT
from friday.skills import scan as SC

PASS, WARN, FAIL = L.PASS, L.WARN, L.FAIL

#: A procedure test may not run longer than this; a hung test is a FAIL.
TEST_TIMEOUT_S = 600.0
#: Loading a skill above this is a WARN in lint and a FAIL here: the
#: router's promise is that a skill costs less than rediscovery.
TOKEN_BUDGET = 8_000

#: Two skills whose routing metadata (description and triggers, as the router
#: reads them) share this fraction of their words compete for the same
#: requests: the later one is flagged and cannot activate beside the earlier.
OVERLAP_THRESHOLD = 0.8
#: A skill is measured only with at least this many recorded tasks in EACH arm
#: (with the skill, without it), and is worth activating only when its success
#: rate beats the without-arm by at least MIN_LIFT (a fraction: 0.1 = 10 points).
MIN_TRIALS = 3
MIN_LIFT = 0.1

Runner = Callable[[list[str], Path, float], tuple[int, str]]


def run_command(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    """(exit code, tail of combined output). The real runner."""
    try:
        completed = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout,
                                   stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout:.0f}s"
    except OSError as exc:
        return 127, str(exc)
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output[-2000:]


def test_argv(command: str, skill_dir: Path | None = None) -> list[str]:
    """
    A declared test to a command line. `pytest …` runs in this interpreter
    (quiet, no cache); a `.py` path likewise - resolved inside the skill's
    own directory when it lives there (`scripts/check.py`), else relative
    to the repository; anything else is split as a shell line would be,
    without a shell.
    """
    parts = shlex.split(command, posix=True)
    if not parts:
        raise K.SkillError("empty test command")
    if parts[0] in ("pytest", "python -m pytest"):
        return [sys.executable, "-m", "pytest", *parts[1:], "-q", "-p", "no:cacheprovider"]
    if parts[0].endswith(".py"):
        script = parts[0]
        if skill_dir is not None and (Path(skill_dir) / script).is_file():
            script = str(Path(skill_dir) / script)
        return [sys.executable, script, *parts[1:]]
    return parts


def _routing_words(record: K.SkillRecord) -> set[str]:
    return set(RT.tokens(" ".join([record.description, *record.triggers])))


def similarity(a: K.SkillRecord, b: K.SkillRecord) -> float:
    """Jaccard overlap of what the router would match a request against."""
    first, second = _routing_words(a), _routing_words(b)
    union = first | second
    return round(len(first & second) / len(union), 3) if union else 0.0


def success_rate(outcomes: list[str]) -> float:
    """A task counts as won only when it succeeded; a failure and a use the
    owner had to correct are both not wins."""
    return sum(1 for outcome in outcomes if outcome == "success") / len(outcomes)


class SkillEvaluator:
    def __init__(self, registry, *, repo_root: Path | None = None, runner: Runner | None = None,
                 timeout_s: float = TEST_TIMEOUT_S, scanner: SC.Scanner | None = None) -> None:
        if repo_root is None:
            from friday.config import PROJECT_ROOT

            repo_root = PROJECT_ROOT
        self.registry = registry
        self.repo_root = Path(repo_root)
        self.runner: Runner = runner or run_command
        self.timeout_s = timeout_s
        self.scanner = scanner               # the external scan; None is the fabric's SkillSpector

    # -- the evaluation ----------------------------------------------------------

    def evaluate(self, skill: F.SkillFiles, record: K.SkillRecord, *,
                 run_tests: bool = True, others: list[K.SkillRecord] | None = None) -> K.EvaluationReport:
        checks: list[tuple[str, str, str]] = []
        text = skill.text() if skill.exists() else ""

        report = L.lint(skill)
        if report.verdict == FAIL:
            checks.append(("lint", FAIL, "; ".join(f"{f.code}: {f.detail}" for f in report.failures())[:400]))
        else:
            warnings = ", ".join(sorted({f.code for f in report.warnings()}))
            checks.append(("lint", WARN if warnings else PASS, warnings or "clean"))

        scan = SC.scan_skill(skill, scanner=self.scanner, trust=record.trust) if skill.exists() else {}
        if scan:
            checks.append(("scan", FAIL if scan["verdict"] == SC.REJECTED
                           else WARN if scan["verdict"] == SC.NOT_FULLY_SCANNED else PASS, self._scan_detail(scan)))

        try:
            _, body = F.parse_skill_md(text)
        except K.SkillError:
            body = text
        risk = L.classify_risk(body)         # the procedure, not the trigger phrases
        if risk in K.HIGH_RISK and record.trust == K.TRUST_AGENT:
            checks.append(("risk", FAIL, f"classified {risk}: an agent-created skill in this class needs the owner"))
        elif risk in K.HIGH_RISK:
            checks.append(("risk", WARN, f"classified {risk}: every use goes through policy"))
        else:
            checks.append(("risk", PASS, risk))

        pool = [record] + [r for r in (others if others is not None else self.registry.list())
                           if r.name != record.name and r.state in K.SELECTABLE]
        for phrase in record.triggers:
            ranked = RT.rank(phrase, pool, selectable_only=False)
            names = [r.name for r, _ in ranked[:3]]
            checks.append(("trigger", PASS if record.name in names else FAIL,
                           f"{phrase!r} -> {names or 'nothing'}"))
        for phrase in record.never_for:
            ranked = RT.rank(phrase, pool, selectable_only=False)
            names = [r.name for r, _ in ranked[:3]]
            checks.append(("non_trigger", FAIL if record.name in names else PASS,
                           f"{phrase!r} -> {names or 'nothing'}"))

        if run_tests:
            for command in record.tests:
                try:
                    argv = test_argv(command, skill.dir)
                except K.SkillError as exc:
                    checks.append(("procedure", FAIL, f"{command!r}: {exc}"))
                    continue
                started = time.monotonic()
                code, output = self.runner(argv, self.repo_root, self.timeout_s)
                seconds = time.monotonic() - started
                tail = output.strip().splitlines()[-1] if output.strip() else ""
                checks.append(("procedure", PASS if code == 0 else FAIL,
                               f"{command!r} exit {code} in {seconds:.0f}s: {tail[:200]}"))
        elif record.tests:
            checks.append(("procedure", WARN, f"{len(record.tests)} test(s) declared, not run"))
        else:
            checks.append(("procedure", WARN, "no tests declared"))

        estimate = K.estimate_tokens(text)
        checks.append(("tokens", PASS if estimate <= TOKEN_BUDGET else FAIL,
                       f"about {estimate:,} tokens to load (budget {TOKEN_BUDGET:,})"))

        checks.append(self._overlap(record, pool[1:]))
        checks.append(self._value(record))

        result = K.EvaluationReport(skill=record.name, version=record.version,
                                    content_hash=skill.content_hash() if skill.exists() else "",
                                    passed=not any(state == FAIL for _, state, _ in checks),
                                    checks=checks, token_estimate=estimate, scan=scan)
        self.registry.record_evaluation(result)
        return result

    @staticmethod
    def _overlap(record: K.SkillRecord, active: list[K.SkillRecord]) -> tuple[str, str, str]:
        close = sorted(((similarity(record, other), other.name) for other in active), reverse=True)
        close = [(score, name) for score, name in close if score >= OVERLAP_THRESHOLD]
        if close:
            return ("overlap", FAIL, "near-duplicate of active " + ", ".join(
                f"{name} ({score:.0%} of routing words)" for score, name in close[:3])
                + "; differentiate it or retire the other before activating")
        return ("overlap", PASS, f"no active skill shares {OVERLAP_THRESHOLD:.0%} of its routing words")

    def _value(self, record: K.SkillRecord) -> tuple[str, str, str]:
        """The with/without comparison on recorded outcomes. No measurement is
        not a pass: a safe skill nobody has shown to help stays inactive."""
        used = [row["outcome"] for row in self.registry.usage(record.name, limit=1000)]
        without = [row["outcome"] for row in self.registry.baseline(record.name, limit=1000)]
        if len(used) < MIN_TRIALS or len(without) < MIN_TRIALS:
            detail = (f"no measured value: {len(used)} task(s) with the skill, {len(without)} without recorded "
                      f"(need {MIN_TRIALS} of each); it stays inactive until measured")
            if record.state == K.NEEDS_REVALIDATION:
                return ("value", WARN, f"{detail} - already active, so not re-gated for revalidation")
            return ("value", FAIL, detail)
        with_rate, without_rate = success_rate(used), success_rate(without)
        lift = with_rate - without_rate
        measured = (f"{with_rate:.0%} success with the skill ({len(used)} tasks) vs {without_rate:.0%} without "
                    f"({len(without)} tasks): lift {lift * 100:+.0f} points")
        if lift < MIN_LIFT:
            return ("value", FAIL, f"no measured lift: {measured} (needs +{MIN_LIFT * 100:.0f}); it stays inactive")
        return ("value", PASS, measured)

    @staticmethod
    def _scan_detail(scan: dict) -> str:
        found = scan["injection"]["findings"]
        external = scan["external"]
        parts = [f"{f['code']} in {f['file']}" for f in found[:3]] or ["no injection patterns"]
        parts.append(f"external scan {external['status']}")
        if external.get("detail") and external["status"] != SC.CLEAN:
            parts.append(external["detail"][:120])
        return "; ".join(parts) + " (static; not runtime-safe)"

    # -- quality -----------------------------------------------------------------

    @staticmethod
    def quality(record: K.SkillRecord, *, now: datetime | None = None) -> float:
        """
        0..1 from outcomes FRIDAY observed:
            0.5  success rate     (successes / successes+failures; 0.5 when unknown)
            0.2  correction rate  (1 - corrections / uses)
            0.2  freshness        (verified within 30 days: 1; decays to 0 at 180)
            0.1  token cost       (1 at 0 tokens, 0 at the budget)
        """
        judged = record.success_count + record.failure_count
        success = record.success_count / judged if judged else 0.5
        corrections = (record.correction_count / record.usage_count) if record.usage_count else 0.0
        freshness = 0.0
        if record.last_verified:
            try:
                verified = datetime.strptime(record.last_verified, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                age_days = ((now or datetime.now(timezone.utc)) - verified).total_seconds() / 86400
                freshness = 1.0 if age_days <= 30 else max(0.0, 1.0 - (age_days - 30) / 150)
            except ValueError:
                freshness = 0.0
        tokens = 0.0
        return round(0.5 * success + 0.2 * (1 - min(corrections, 1.0)) + 0.2 * freshness + 0.1 * (1 - tokens), 3)

    def rescore(self, name: str) -> float:
        record = self.registry.get(name)
        if record is None:
            raise K.SkillError(f"no skill named {name!r}")
        score = self.quality(record)
        self.registry.set_quality(name, score)
        return score
