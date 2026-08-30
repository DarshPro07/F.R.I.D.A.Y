"""Execution economics - Friday decides how much intelligence a task
deserves. Phase H (H1-H6).

Design constraints, from the adjudicated H spec:

- H1-H5 are LOGICAL STAGES, not agents: one deterministic pass, zero
  model calls. An LLM call to decide which LLM to call is the first
  prohibited anti-pattern.
- Route selection is risk-weighted, not size-weighted: a one-line auth
  change is not "small"; a rename is not "deep".
- The router NEVER proposes an unconfigured model. Tiers map through
  the profile's routing table; an empty table maps every tier to the
  profile default and records the decision anyway, so telemetry
  accumulates before cheaper models are provisioned.
- Verification depth is priced separately (the change classifier): the
  cheapest evidence sufficient for the risk, with the reason recorded.
- H6 learns from durable outcome records; the value philosophy is
  quality-adjusted cost INCLUDING REWORK, never token minimum.
"""

from __future__ import annotations

import functools
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# H1 - task economics (deterministic classification)
# ---------------------------------------------------------------------------

#: Signal tables, deliberately transparent and editable. Matching is on
#: the TASK TEXT the caller supplied plus structural facts - never on
#: line counts, which measure size, not risk.
_HIGH_CONSEQUENCE = (
    "auth", "security", "credential", "secret", "payment", "policy",
    "permission", "guardrail", "encrypt", "migration", "schema",
    "delete", "drop ", "deploy", "publish", "release",
)
_MECHANICAL = (
    "count", "list files", "how many", "file size", "rename file",
    "move file", "where is", "locate the file", "path of",
)
_RESEARCH = (
    "research", "investigate", "compare", "evaluate", "survey",
    "find out", "what are the options",
)
_MULTI_STREAM = (
    "in parallel", "independent", "two workers", "three workers",
    "separate workstreams", "worker a", "worker b",
)
_DEEP_REASONING = (
    "architecture", "architectural", "design the", "trade-off",
    "prove", "exactly-once", "race", "concurren", "crash",
    "failure timeline",
)
_TINY_CHANGE = (
    "import path", "rename variable", "typo", "comment", "docstring",
    "one line", "single line", "constant", "bump version",
)


@dataclass(frozen=True)
class TaskEconomics:
    """H1 output: what kind of work this is and what failure costs."""

    kind: str            # mechanical | question | code_change | research
                         # | inspection | multi_stream
    novelty: str         # low | medium | high
    ambiguity: str       # low | medium | high
    blast_radius: str    # tiny | local | integration | core
    consequence: str     # low | medium | high
    signals: tuple[str, ...] = ()


def classify_task(text: str, *, code_refs: int = 0,
                  acceptance: int = 0) -> TaskEconomics:
    """One deterministic pass over the task text. No model calls."""
    lowered = " ".join((text or "").lower().split())

    def hits(table):
        return tuple(s for s in table if s in lowered)

    mech = hits(_MECHANICAL)
    deep = hits(_DEEP_REASONING)
    high = hits(_HIGH_CONSEQUENCE)
    tiny = hits(_TINY_CHANGE)
    multi = hits(_MULTI_STREAM)
    research = hits(_RESEARCH)

    if mech and not (deep or high):
        kind = "mechanical"
    elif multi:
        kind = "multi_stream"
    elif research and not tiny:
        kind = "research"
    elif any(w in lowered for w in ("inspect", "trace", "review", "audit")):
        kind = "inspection"
    elif any(w in lowered for w in ("change", "fix", "implement", "refactor",
                                    "build", "write", "create", "add ")):
        kind = "code_change"
    else:
        kind = "question"

    consequence = ("high" if high
                   else "low" if (tiny or mech or kind == "question")
                   else "medium")
    blast_radius = (
        "core" if high else
        "tiny" if tiny or mech else
        "integration" if deep or multi else
        "local")
    ambiguity = ("low" if acceptance or tiny or mech
                 else "medium" if kind in ("code_change", "inspection")
                 else "high" if kind == "research" else "medium")
    novelty = "low" if (tiny or mech or code_refs) else "medium"

    return TaskEconomics(kind=kind, novelty=novelty, ambiguity=ambiguity,
                         blast_radius=blast_radius, consequence=consequence,
                         signals=mech + deep + high + tiny + multi)


# ---------------------------------------------------------------------------
# H2 - route selection (minimum capable route, risk-weighted)
# ---------------------------------------------------------------------------

#: Route levels, cheapest first. DETERMINISTIC means "no model at all" -
#: proven in gate J5 (131 files counted by os.walk, 0 model calls).
DETERMINISTIC = "deterministic"
FRIDAY_DIRECT = "friday_direct"
HERMES_SINGLE = "hermes_single"
HERMES_MULTI = "hermes_multi"
HERMES_DEEP = "hermes_deep"

#: Model tiers. Mapping to concrete models lives in the profile routing
#: table; unset tiers resolve to "" = profile default.
TIER_ECONOMY = "economy"
TIER_STANDARD = "standard"
TIER_DEEP = "deep"


@dataclass(frozen=True)
class Route:
    """H2 output: where the work runs and with what model tier."""

    level: str
    tier: str
    reason: str


def choose_route(econ: TaskEconomics) -> Route:
    """Minimum capable route. Consequence escalates; size never does."""
    if econ.kind == "mechanical":
        return Route(DETERMINISTIC, TIER_ECONOMY,
                     "mechanical work: software, zero model tokens")
    if econ.kind == "question" and econ.consequence == "low":
        return Route(FRIDAY_DIRECT, TIER_ECONOMY,
                     "simple question: Friday answers directly, no worker")
    if econ.kind == "multi_stream":
        return Route(HERMES_MULTI, TIER_STANDARD,
                     "genuinely independent workstreams: parallel workers")
    if econ.consequence == "high" or econ.blast_radius == "core":
        return Route(HERMES_DEEP, TIER_DEEP,
                     f"high consequence ({', '.join(econ.signals[:3])}): "
                     "strongest configured model, full verification")
    if econ.blast_radius == "tiny" and econ.novelty == "low":
        return Route(HERMES_SINGLE, TIER_ECONOMY,
                     "tiny bounded change: economical tier is capable")
    if econ.kind == "research" or econ.blast_radius == "integration":
        return Route(HERMES_SINGLE, TIER_DEEP,
                     "reasoning-heavy scope: deep tier justified")
    return Route(HERMES_SINGLE, TIER_STANDARD,
                 "bounded engineering task: standard tier")


@functools.lru_cache(maxsize=1)
def _tier_table() -> dict:
    """
    tier -> concrete model, from the friday profile's `routing.tiers`
    config block (optional). Absent block = empty mapping = every tier
    resolves to the profile default model. The router never invents a
    model name: only this table or "" (default) can appear.
    """
    from friday.hermes_bridge import ENV_PROFILE, ENV_PROFILE_HOME, \
        profile_home
    home = os.environ.get(ENV_PROFILE_HOME) or profile_home(
        os.environ.get(ENV_PROFILE, "friday"))
    if not home:
        return {}
    try:
        import yaml
        config = yaml.safe_load(
            (Path(home) / "config.yaml").read_text(encoding="utf-8"))
        tiers = ((config or {}).get("routing") or {}).get("tiers") or {}
        return {str(k): str(v) for k, v in tiers.items() if v}
    except Exception:                                        # noqa: BLE001
        return {}


def resolve_model(tier: str) -> str:
    """Concrete model for a tier, or "" meaning the profile default."""
    return _tier_table().get(tier, "")


# ---------------------------------------------------------------------------
# H4 - verification depth (priced evidence)
# ---------------------------------------------------------------------------

TARGETED = "targeted"          # the touched module's tests
AFFECTED = "affected"          # touched + direct consumers
INTEGRATION = "integration"    # boundary suites
FULL = "full"                  # whole suite


def verification_depth(econ: TaskEconomics) -> tuple[str, str]:
    """(scope, reason). Cheapest evidence sufficient for the risk."""
    if econ.consequence == "high" or econ.blast_radius == "core":
        return FULL, ("high-consequence or core-touching change: full "
                      "relevant regression is the cheapest safe evidence")
    if econ.blast_radius == "integration":
        return INTEGRATION, "crosses a boundary: integration suites"
    if econ.blast_radius == "tiny":
        return TARGETED, ("tiny bounded change: targeted tests; escalate "
                          "only if they surface consumers")
    return AFFECTED, "localized change: touched module plus consumers"


# ---------------------------------------------------------------------------
# H5/H6 - outcome records (the learning substrate)
# ---------------------------------------------------------------------------

_OUTCOMES_TABLE = """
CREATE TABLE IF NOT EXISTS route_outcomes (
    work_run_id   TEXT PRIMARY KEY,
    task_class    TEXT NOT NULL DEFAULT '',
    route_level   TEXT NOT NULL DEFAULT '',
    tier          TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    provider      TEXT NOT NULL DEFAULT '',
    calls         INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    duration_s    REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT '',
    verification  TEXT NOT NULL DEFAULT '',
    rework        INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
)
"""


class RouteOutcomes:
    """Durable H6 records. Same database file as the WorkRun log."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from friday.config import DATA_DIR
            db_path = Path(DATA_DIR) / "ada.sqlite3"
        self._path = str(db_path)
        with self._connect() as db:
            db.execute(_OUTCOMES_TABLE)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def record(self, work_run_id: str, *, task_class: str, route_level: str,
               tier: str, model: str = "", provider: str = "",
               calls: int = 0, prompt_tokens: int = 0,
               output_tokens: int = 0, duration_s: float = 0,
               status: str = "", verification: str = "",
               rework: int = 0) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO route_outcomes (work_run_id,"
                " task_class, route_level, tier, model, provider, calls,"
                " prompt_tokens, output_tokens, duration_s, status,"
                " verification, rework, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (work_run_id, task_class, route_level, tier, model,
                 provider, calls, prompt_tokens, output_tokens, duration_s,
                 status, verification, rework, time.time()))

    def mark_rework(self, work_run_id: str) -> None:
        """A later run had to repair this one - the decisive cost signal:
        20k that needs a 40k repair was never cheaper than 45k done
        right."""
        with self._connect() as db:
            db.execute("UPDATE route_outcomes SET rework = rework + 1"
                       " WHERE work_run_id = ?", (work_run_id,))

    def by_class(self, task_class: str, limit: int = 50) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM route_outcomes WHERE task_class = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (task_class, limit)).fetchall()
        return [dict(r) for r in rows]

    def execution_value(self, record: dict) -> float:
        """
        Quality-adjusted cost, the H6 evaluation philosophy:
        quality x success / (tokens + latency + rework). Zero for
        failures; rework multiplies cost, never subtracts quality.
        """
        if record.get("status") != "COMPLETE":
            return 0.0
        tokens = (record.get("prompt_tokens") or 0) + \
                 (record.get("output_tokens") or 0)
        # A rework is not a discount on quality - it is the cost of the
        # repair run, and the spec's own arithmetic prices a repair at
        # roughly 2x the original (20k + 40k repair loses to 45k done
        # right). So each rework adds ~2x the original cost.
        cost = max(tokens, 1) * (1 + 2 * (record.get("rework") or 0))
        cost += (record.get("duration_s") or 0) * 100     # latency weight
        return 1_000_000.0 / cost
