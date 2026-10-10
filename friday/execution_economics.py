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
import logging
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

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
#: Where an imperative starts: the request, or a clause after a comma,
#: "then"/"and", optionally softened ("please", "can you").
_CLAUSE_START = (r"(?:^|[,;:]\s+|\b(?:then|and)\s+)(?:please\s+)?"
                 r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?")
#: Imperative work verbs. Matched only at a clause start: "make" and "stop"
#: are ordinary words in questions ("what makes a good key", "how do I stop
#: a thread") that must stay direct answers, and "stop" is a device command
#: ("stop the music") unless it is "stop X from <doing>". "rename"/"move" are
#: also the literal mechanical commands "rename file"/"move file", so they are
#: work verbs only when not followed by "file(s)". Destructive verbs (delete,
#: drop) are deliberately absent: task_class._CRITICAL owns those.
_WORK_VERB = re.compile(
    _CLAUSE_START + r"(?:change|fix|implement|refactor|build|write|create|add|"
    r"make|prevent|update|remove|replace|migrate|extend|"
    r"(?:rename|move)\b(?!\s+files?\b)|stop\b(?=.*\bfrom\b))\b", re.I)
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
    "import path", "rename variable", "rename the variable",
    "rename a variable", "typo", "comment", "docstring",
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


def wants_a_change(text: str) -> bool:
    """True when the request is an imperative change to something ("make X
    survive Y", "stop X from Y", "fix X"), as opposed to a question or a
    device command. Deterministic; the one owner of this rule."""
    return bool(_WORK_VERB.search(" ".join((text or "").lower().split())))


def classify_task(text: str, *, code_refs: int = 0,
                  acceptance: int = 0) -> TaskEconomics:
    """One deterministic pass over the task text. No model calls."""
    lowered = " ".join((text or "").lower().split())
    work = wants_a_change(lowered)

    def hits(table):
        return tuple(s for s in table if s in lowered)

    def whole_words(table):
        return tuple(s for s in table
                     if re.search(r"\b" + re.escape(s) + r"\b", lowered))

    # Mechanical phrases are literal commands, not stems: "count" must not
    # fire inside "counter" and send a code fix to the zero-model route. An
    # imperative work verb means the phrase is part of what is being changed
    # ("fix the word count display"), not the command itself.
    mech = () if work else whole_words(_MECHANICAL)
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
    elif work or any(w in lowered for w in ("change", "fix", "implement",
                                            "refactor", "build", "write",
                                            "create", "add ")):
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
    # A question that carries reasoning-heavy signals (classify_task marks
    # it integration/core) is not simple, whatever verb it lacks.
    if (econ.kind == "question" and econ.consequence == "low"
            and econ.blast_radius not in ("integration", "core")):
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


#: The built-in tier ladder, used when the friday profile declares no
#: `routing.tiers`. Every id here appears in the friday profile's own
#: provider_models_cache.json (anthropic) - the router never invents a
#: model name. The profile block, when present, overrides per tier: the
#: owner picks the models; this is the default that makes the tiers MEAN
#: something without a config edit, instead of all three collapsing onto the
#: profile default (measured: tier table {} -> every tier resolved to "").
DEFAULT_TIERS = {
    TIER_ECONOMY: "claude-haiku-4-5-20251001",
    TIER_STANDARD: "claude-sonnet-5",
    TIER_DEEP: "",            # the profile default (claude-opus-5 here)
}

#: Whose model ids DEFAULT_TIERS are. On a profile whose main provider is
#: another one they are not its models: a fresh `friday` profile cloned from an
#: openai-codex default asked openai-codex for claude-haiku on every fast call
#: (FRIDAY.exe live test, 2026-10-05). There a tier the profile does not set
#: itself means the profile default.
DEFAULT_TIERS_PROVIDER = "anthropic"


@functools.lru_cache(maxsize=1)
def known_models() -> frozenset:
    """Model ids the friday profile's provider cache actually lists.

    Empty when the cache is absent, which disables validation rather than
    refusing every model - an absent cache is a fresh install, not a wrong
    id. Read once per process; `capability_reload` clears it."""
    from friday.hermes_bridge import ENV_PROFILE, ENV_PROFILE_HOME, \
        profile_home
    home = os.environ.get(ENV_PROFILE_HOME) or profile_home(
        os.environ.get(ENV_PROFILE, "friday"))
    if not home:
        return frozenset()
    path = Path(home) / "provider_models_cache.json"
    try:
        import json
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    found: set[str] = set()
    # Shape on this host: {provider: {"models": ["id", ...]}}. Older caches
    # carried [{"id": ...}] lists; both are read, and anything else is
    # ignored rather than guessed at.
    for entry in (raw.values() if isinstance(raw, dict) else []):
        models = entry.get("models") if isinstance(entry, dict) else entry
        for m in models or []:
            if isinstance(m, str):
                found.add(m)
            elif isinstance(m, dict) and m.get("id"):
                found.add(str(m["id"]))
    return frozenset(found)


@functools.lru_cache(maxsize=1)
def _tier_table() -> dict:
    """
    tier -> concrete model. The friday profile's `routing.tiers` block wins
    where it names a tier; DEFAULT_TIERS fills the rest when the profile's
    main provider is DEFAULT_TIERS_PROVIDER, "" otherwise. "" means the
    profile default model. The router never invents a model name: only this
    table or "" can appear, and a name the provider cache does not list is
    dropped to "" with a log line - a typo in config must degrade to the
    default, not fail every delegation with a 404 from the gateway.
    """
    from friday.hermes_bridge import ENV_PROFILE, ENV_PROFILE_HOME, \
        profile_home
    table = dict(DEFAULT_TIERS)
    configured: dict = {}
    main_provider = ""
    home = os.environ.get(ENV_PROFILE_HOME) or profile_home(
        os.environ.get(ENV_PROFILE, "friday"))
    if home:
        try:
            import yaml
            config = yaml.safe_load(
                (Path(home) / "config.yaml").read_text(encoding="utf-8"))
            tiers = ((config or {}).get("routing") or {}).get("tiers") or {}
            configured = {str(k): str(v) for k, v in tiers.items() if v}
            main_provider = str(((config or {}).get("model") or {}).get("provider") or "")
        except Exception:                                    # noqa: BLE001
            pass
    if main_provider and main_provider != DEFAULT_TIERS_PROVIDER:
        table = {tier: "" for tier in table}
    table.update(configured)
    known = known_models()
    if known:
        for tier, model in list(table.items()):
            if model and model not in known:
                logger.warning("routing tier %s names %r, which the provider "
                               "cache does not list; using the profile default",
                               tier, model)
                table[tier] = ""
    return table


#: Spoken requirements -> tier. "Java, use the cheapest model for this" or
#: "think hard about this" should pick a tier without the planner having
#: to guess from the goal text. Longest phrase wins; absent means "let the
#: classifier decide".
REQUIREMENT_TIERS = (
    ("strongest", TIER_DEEP), ("best model", TIER_DEEP), ("think hard", TIER_DEEP),
    ("deep", TIER_DEEP), ("careful", TIER_DEEP), ("thorough", TIER_DEEP),
    ("cheapest", TIER_ECONOMY), ("cheap", TIER_ECONOMY), ("fast", TIER_ECONOMY),
    ("quick", TIER_ECONOMY), ("economy", TIER_ECONOMY), ("small model", TIER_ECONOMY),
    ("standard", TIER_STANDARD), ("normal", TIER_STANDARD),
)


def tier_from_requirements(text: str) -> str:
    """The tier a request asks for by name, or "" when it does not say."""
    low = (text or "").lower()
    for phrase, tier in REQUIREMENT_TIERS:
        if phrase in low:
            return tier
    return ""


def resolve_model(tier: str) -> str:
    """Concrete model for a tier, or "" meaning the profile default."""
    return _tier_table().get(tier, "")


#: Tier -> Hermes reasoning effort. This is the token-aware depth knob: the
#: gateway's session.create accepts `reasoning_effort` (parsed by
#: hermes_constants.parse_reasoning_effort; levels none..ultra) as a
#: PER-SESSION override, so a bounded rename does not pay for the thinking
#: budget a cross-file design question needs, and vice versa. The model
#: choice and the effort choice are independent: the profile default model
#: with `low` effort is a real, cheap route.
EFFORT_BY_TIER = {
    TIER_ECONOMY: "low",
    TIER_STANDARD: "medium",
    TIER_DEEP: "high",
}


def resolve_effort(tier: str) -> str:
    """Hermes reasoning effort for a tier; "" means inherit the profile."""
    return EFFORT_BY_TIER.get(tier, "")


def _fallback_candidates() -> list[tuple[str, str]]:
    """The profile's `fallback_providers:` list, as (provider, model) pairs.

    Empty when unconfigured (the friday profile ships `fallback_providers:
    []`) - candidates() then falls back to the profile default only.
    """
    from friday.hermes_bridge import ENV_PROFILE, ENV_PROFILE_HOME, \
        profile_home
    home = os.environ.get(ENV_PROFILE_HOME) or profile_home(
        os.environ.get(ENV_PROFILE, "friday"))
    if not home:
        return []
    try:
        import yaml
        config = yaml.safe_load(
            (Path(home) / "config.yaml").read_text(encoding="utf-8"))
        entries = (config or {}).get("fallback_providers") or []
    except Exception:                                        # noqa: BLE001
        return []
    out: list[tuple[str, str]] = []
    for entry in entries:
        if isinstance(entry, dict):
            provider, model = str(entry.get("provider", "")), \
                str(entry.get("model", "") or entry.get("name", ""))
        elif isinstance(entry, str):
            provider, model = entry, ""
        else:
            continue
        if provider:
            out.append((provider, model))
    return out


def candidates(tier: str) -> list[tuple[str, str]]:
    """
    Ordered (provider, model) pairs to try for a tier: the tier's own
    model first (under the profile's default provider - a tier names a
    model, not a provider), then the profile's configured fallback
    providers, then the bare profile default. Never invents a provider
    Hermes was not told about.
    """
    from friday.hermes_bridge import _profile_model_default
    default_provider, default_model = _profile_model_default()
    tier_model = resolve_model(tier) or default_model
    out: list[tuple[str, str]] = [(default_provider, tier_model)]
    for provider, model in _fallback_candidates():
        pair = (provider, model or tier_model)
        if pair not in out:
            out.append(pair)
    default_pair = (default_provider, default_model)
    if default_pair not in out:
        out.append(default_pair)
    return out


def _fmt_hhmm(iso: str) -> str:
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except ValueError:
        return "unknown"


def plan_delegation(text: str, *, code_refs: int = 0, acceptance: int = 0,
                    model: str = "", effort: str = "") -> dict:
    """
    One deterministic pass from a task description to the Hermes route:
    level, tier, model, provider, reasoning effort and the reason - zero
    model calls spent deciding which model to call. Explicit `model`/
    `effort` from the caller win untouched and are recorded as such.

    When the caller did not pin a model, candidates() is walked in order
    and any candidate under an active cooldown (see
    `friday/provider_cooldowns.py`) is skipped - `switched_from` and the
    reason then say which provider was avoided and why. If every
    candidate is cooled, the tier's own model is returned anyway (Hermes
    still has to be told something) with a "waiting for" reason naming
    the soonest reset.
    """
    econ = classify_task(text, code_refs=code_refs, acceptance=acceptance)
    route = choose_route(econ)
    asked = tier_from_requirements(text)
    if asked and not model:
        # A named requirement beats the classifier's guess - but only for
        # the tier. Consequence still decides the route LEVEL: "quick" does
        # not turn a core-touching change into an unverified one.
        route = Route(route.level, asked,
                      f"tier {asked} requested in the goal; {route.reason}")
    chosen_effort = effort or resolve_effort(route.tier)
    base_reason = (f"{route.level}/{route.tier}: {route.reason} "
                   f"[class={econ.kind}, consequence={econ.consequence}, "
                   f"effort={chosen_effort or 'profile'}]")

    provider = ""
    switched_from = ""
    wait_until = ""
    if model:
        chosen_model = model
        reason = f"model pinned by caller ({model}); " + base_reason
    else:
        from friday.provider_cooldowns import active
        cooled = active()
        cands = candidates(route.tier)
        first_provider, first_model = cands[0]
        chosen = None
        earliest = ""
        for cand_provider, cand_model in cands:
            until = cooled.get((cand_provider, cand_model))
            if until is None:
                chosen = (cand_provider, cand_model)
                break
            if not earliest or until < earliest:
                earliest = until
        if chosen is None:
            # Every candidate is cooled - do NOT hand back a capped
            # candidate as "chosen" (that hammers it inside its own
            # cooldown window). model/provider stay empty; the caller gets
            # wait_until so it can requeue instead of dispatching.
            chosen_model = ""
            wait_until = earliest
            reason = (f"waiting for {first_provider or 'the profile default'} "
                      f"until {_fmt_hhmm(earliest)}")
        else:
            provider, chosen_model = chosen
            if chosen != (first_provider, first_model):
                switched_from = first_provider or first_model
                when = _fmt_hhmm(cooled.get((first_provider, first_model), ""))
                reason = (f"{switched_from} capped until {when} → "
                          f"{provider or chosen_model}; " + base_reason)
            else:
                reason = base_reason
    return {"level": route.level, "tier": route.tier, "model": chosen_model,
            "provider": provider, "effort": chosen_effort, "reason": reason,
            "kind": econ.kind, "consequence": econ.consequence,
            "switched_from": switched_from, "wait_until": wait_until}


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
    executor      TEXT NOT NULL DEFAULT '',
    calls         INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    duration_s    REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT '',
    verification  TEXT NOT NULL DEFAULT '',
    rework        INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
)
"""

_CACHE_READ_KEYS = ("cache_read", "cache_read_tokens",
                    "cache_read_input_tokens", "cached_tokens")


def cache_read_tokens(usage: dict) -> int:
    """Prompt tokens a provider served from its cache, from a run's reported
    usage (ORCH-012). Reads the shapes the runs report - Hermes's
    `session.usage` (flat or under "usage"), Anthropic's
    `cache_read_input_tokens`, OpenAI's `cached_tokens` - and counts nothing
    it cannot read as a number: an unreported cache is 0, not an error."""
    if not isinstance(usage, dict):
        return 0
    inner = usage.get("usage")
    for source in (inner, usage):
        if not isinstance(source, dict):
            continue
        for key in _CACHE_READ_KEYS:
            value = source.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            return max(0, int(value))
    return 0


#: Runs a route needs on record, for one task class, before the gateway ranks
#: it (FAB-003). One lucky cheap completion is not a measurement.
MIN_RANKED_RUNS = 3

#: The executor the Hermes bridge records its runs under.
EXECUTOR_HERMES = "hermes"

#: What a benchmark result is keyed by, below its task class (FAB-003): the
#: route (provider, model), the executor that ran it, and the configuration it
#: ran in - `route_level`, single worker or multi or deep. No executor reports
#: the model's own version separately from its id, so there is no version key.
ROUTE_KEY = ("provider", "model", "executor", "route_level")


class RouteOutcomes:
    """Durable H6 records. Same database file as the WorkRun log."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from friday.store import DEFAULT_DB
            db_path = os.getenv("ADA_DB") or DEFAULT_DB
        self._path = str(db_path)
        with self._connect() as db:
            db.execute(_OUTCOMES_TABLE)
            # Additive migration for databases created before the cache and
            # executor columns existed.
            columns = {r[1] for r in db.execute(
                "PRAGMA table_info(route_outcomes)")}
            if "cache_read_tokens" not in columns:
                db.execute("ALTER TABLE route_outcomes ADD COLUMN"
                           " cache_read_tokens INTEGER NOT NULL DEFAULT 0")
            if "executor" not in columns:
                db.execute("ALTER TABLE route_outcomes ADD COLUMN"
                           " executor TEXT NOT NULL DEFAULT ''")
                # The Hermes bridge was the only recorder before this column.
                db.execute("UPDATE route_outcomes SET executor = ?",
                           (EXECUTOR_HERMES,))

    def _connect(self):
        from friday.dbconn import ledger_connection
        return ledger_connection(self._path)

    def record(self, work_run_id: str, *, task_class: str, route_level: str,
               tier: str, model: str = "", provider: str = "",
               calls: int = 0, prompt_tokens: int = 0,
               output_tokens: int = 0, duration_s: float = 0,
               status: str = "", verification: str = "",
               rework: int = 0, cache_read_tokens: int = 0,
               executor: str = "") -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO route_outcomes (work_run_id,"
                " task_class, route_level, tier, model, provider, executor,"
                " calls, prompt_tokens, output_tokens, cache_read_tokens,"
                " duration_s, status, verification, rework, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (work_run_id, task_class, route_level, tier, model,
                 provider, executor, calls, prompt_tokens, output_tokens,
                 cache_read_tokens, duration_s, status, verification, rework,
                 time.time()))

    @staticmethod
    def cache_hit_rate(record: dict) -> float | None:
        """One worker's prompt-cache hit rate: the share of its prompt tokens
        the provider served from cache. None when the run reported no prompt
        tokens, so an unmeasured worker is not read as a cold one."""
        prompt = record.get("prompt_tokens") or 0
        if prompt <= 0:
            return None
        return min(1.0, (record.get("cache_read_tokens") or 0) / prompt)

    def cache_hit_rates(self, prefix: str = "") -> dict[str, float | None]:
        """work_run_id -> cache hit rate, for runs whose id starts with
        `prefix` (all of them by default)."""
        return {r["work_run_id"]: self.cache_hit_rate(r)
                for r in self.by_run_prefix(prefix)}

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

    def by_run_prefix(self, prefix: str, limit: int = 1000) -> list[dict]:
        """Records whose work_run_id starts with `prefix`, by id. A literal
        match (no LIKE wildcards): evals namespace their runs this way."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM route_outcomes WHERE substr(work_run_id, 1, ?)"
                " = ? ORDER BY work_run_id LIMIT ?",
                (len(prefix), prefix, limit)).fetchall()
        return [dict(r) for r in rows]

    def execution_value(self, record: dict) -> float:
        """
        Quality-adjusted cost, the H6 evaluation philosophy:
        quality x success / (tokens + latency + rework). Zero for
        failures; rework multiplies cost, never subtracts quality.
        """
        if record.get("status") != "COMPLETE":
            return 0.0
        return 1_000_000.0 / self.run_cost(record)

    @staticmethod
    def run_cost(record: dict) -> float:
        """What one run cost, whatever its outcome: tokens, plus the repair
        its rework implies, plus latency."""
        tokens = (record.get("prompt_tokens") or 0) + \
                 (record.get("output_tokens") or 0)
        # A rework is not a discount on quality - it is the cost of the
        # repair run, and the spec's own arithmetic prices a repair at
        # roughly 2x the original (20k + 40k repair loses to 45k done
        # right). So each rework adds ~2x the original cost.
        cost = max(tokens, 1) * (1 + 2 * (record.get("rework") or 0))
        cost += (record.get("duration_s") or 0) * 100     # latency weight
        return cost

    def cost_per_completion(self, task_class: str, limit: int = 500,
                            min_runs: int = 1,
                            key: tuple[str, ...] = ("provider", "model")
                            ) -> dict[tuple[str, ...], float]:
        """FAB-003: per `key` (the route a gateway request names, (provider,
        model), by default; `ROUTE_KEY` adds the executor and configuration),
        everything the route spent on this task class divided by the runs it
        verifiably completed. Failed runs are paid for by the completions, so
        a cheap route that fails often costs more per task than a dear one
        that completes; a route with no completion is infinity. Two providers
        serving one model are two keys, and under `ROUTE_KEY` so are two
        executors. A run counts as completed when its status is COMPLETE, the
        same test `execution_value` applies. A route with fewer than
        `min_runs` runs on record is left out: too few to rank on."""
        spent: dict[tuple[str, ...], float] = {}
        done: dict[tuple[str, ...], int] = {}
        runs: dict[tuple[str, ...], int] = {}
        for rec in self.by_class(task_class, limit):
            route = tuple(rec[column] for column in key)
            spent[route] = spent.get(route, 0.0) + self.run_cost(rec)
            done[route] = done.get(route, 0) + (rec["status"] == "COMPLETE")
            runs[route] = runs.get(route, 0) + 1
        return {k: (spent[k] / done[k] if done[k] else math.inf)
                for k in spent if runs[k] >= min_runs}


def rank_routes(routes: list[tuple[str, str, str]],
                costs: dict[tuple[str, str], float]
                ) -> list[tuple[str, str, str]]:
    """Order (tier, provider, model) routes by measured cost per completed
    task, cheapest first (FAB-003). Only routes with evidence move: they are
    sorted among the positions the measured routes already held, so an
    unmeasured route keeps its place and an empty `costs` changes nothing."""
    slots = [i for i, (_, p, m) in enumerate(routes) if (p, m) in costs]
    ordered = sorted((routes[i] for i in slots), key=lambda r: costs[(r[1], r[2])])
    out = list(routes)
    for i, route in zip(slots, ordered):
        out[i] = route
    return out


def cheapest_capable(cands: list[str], bench: dict, *, min_n: int, rate_floor: float
                     ) -> tuple[str, list[dict]] | None:
    """The cheapest per verified win among `cands`, with the rows it was chosen
    from; None when no candidate has `min_n` outcomes and a win. `bench` maps
    model -> {n, wins, cost}. A model keeps its place only while its success rate
    is at least `rate_floor` of the best; ties go to the earlier candidate. The
    ranker `build_kernel.pick_model` and ultracode tiers share. It sits beside
    `rank_routes`/`cost_per_completion` (FAB-003), which order routes from a
    different input; the two are not yet expressed through one another."""
    rated = {m: bench[m]["wins"] / bench[m]["n"] for m in cands
             if m in bench and bench[m]["n"] >= min_n and bench[m]["wins"] > 0}
    if not rated:
        return None
    best = max(rated.values())
    rows = [{"model": m, "n": bench[m]["n"], "wins": bench[m]["wins"],
             "cost": bench[m]["cost"], "cost_per_win": bench[m]["cost"] / bench[m]["wins"]}
            for m in cands if m in rated and rated[m] >= rate_floor * best]
    winner = min(rows, key=lambda r: (r["cost_per_win"], cands.index(r["model"])))
    return winner["model"], rows
