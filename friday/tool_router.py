"""
Staged tool router (PRD v7 19.3, CTX-002).

Stage 1 ranks the catalogue: the shipped keyword-and-metadata router fused with
BM25, hashed n-grams and (when the pinned model files are on disk) a bge
embedding. When the rankers agree on one tool and it leads the next by enough,
that tool is routed to directly: no model call, tens of milliseconds.

When they do not, stage 2 hands the request and compact cards for the top few
candidates to a chooser - a reasoning model through FRIDAY's model gateway -
which may answer only {decision: TOOL | CLARIFY | ABSTAIN, tool_id, confidence}.
A large model is therefore never in the path of an easy request, and a hard one
is chosen among ten tools instead of searched among two hundred.

A chooser that is absent, slow, down or wrong never fails the request: the
router falls back to the top-1 candidate marked `uncertain`, and the caller
discloses the top candidates the way a plain search would.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from typing import Callable, Protocol

from friday import tool_cards
from friday.tool_retrieval import Retriever

log = logging.getLogger("friday.tool_router")

TOOL, CLARIFY, ABSTAIN = "TOOL", "CLARIFY", "ABSTAIN"
DECISIONS = (TOOL, CLARIFY, ABSTAIN)
RRF_K = 10          # damping of the fusion across the shipped router and the first stage
TOP_K = 10          # candidates a chooser sees; coverage@10 is measured in the benchmark


@dataclass(frozen=True)
class Thresholds:
    """Route directly when `agree` and `margin` both reach these.

    `agree` is the share of rankers whose first choice is the fused first
    choice; `margin` is the fused first score's lead over the second, as a share
    of the first. Chosen on the development split only
    (scripts/bench_tool_disclosure.py --thresholds), never on the holdout.

    The sweep found no setting that is right 95% of the time on any share of
    requests (docs/plans/ctx-002/bench_small.json: the loosest target that routes
    anything, 70%, routes 4.8% of requests and is right 70.7% of the time; at 60%
    it routes 9.5% and is right 62.6%). These defaults are the sweep's choice, a
    cell that routes nothing directly on development, so every request goes to
    the chooser. Loosen them only when a better confidence signal or a measured
    chooser says a cell is safe."""

    agree: float = 1.0
    margin: float = 0.4


@dataclass(frozen=True)
class Route:
    decision: str                          # TOOL | CLARIFY | ABSTAIN
    tool_id: str | None
    stage: int                             # 1 = retrieval alone, 2 = a chooser decided
    confidence: float
    candidates: tuple[str, ...]
    uncertain: bool = False                # retrieval was unsure and no chooser settled it
    reason: str = ""
    chooser_called: bool = False           # a chooser was asked, whatever it answered


class Chooser(Protocol):
    def choose(self, query: str, cards: list[str]) -> dict:
        """Return {decision, tool_id, confidence}; may raise."""


def tool_text(name: str, description: str, intent_examples=(), group_purpose: str = "", *,
              cards: bool = True) -> str:
    """The document a tool is found by."""
    spoken = name.replace("_", " ")
    parts = [spoken, spoken, description, " ".join(intent_examples), group_purpose]
    if cards:
        parts.append(tool_cards.index_text(name))
    return " ".join(part for part in parts if part)


def fuse(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    score: dict[str, float] = {}
    for ranking in rankings:
        for rank, name in enumerate(ranking):
            score[name] = score.get(name, 0.0) + 1.0 / (k + rank)
    return sorted(score.items(), key=lambda item: (-item[1], item[0]))


def margin_of(fused: list[tuple[str, float]]) -> float:
    """The first score's lead over the second, as a share of the first."""
    if not fused or not fused[0][1]:
        return 0.0
    second = fused[1][1] if len(fused) > 1 else 0.0
    return (fused[0][1] - second) / fused[0][1]


class StagedRouter:
    def __init__(self, ids: list[str], texts: list[str], *, shipped: Callable[[str], list[str]],
                 embedder=None, cache_dir=None, revision: str = "",
                 thresholds: Thresholds | None = None, chooser: Chooser | None = None,
                 top_k: int = TOP_K, descriptions: dict[str, str] | None = None):
        self.retriever = Retriever(ids, texts, embedder=embedder, cache_dir=cache_dir, revision=revision)
        self.shipped = shipped
        self.thresholds = thresholds or Thresholds()
        self.chooser = chooser
        self.top_k = top_k
        self.descriptions = descriptions or {}

    def rank(self, query: str) -> tuple[list[tuple[str, float]], float]:
        """Fused ranking with scores, and the share of rankers agreeing on its first."""
        shipped = self.shipped(query)
        parts = self.retriever.components(query)
        first_stage = self.retriever.first_stage(query, parts=parts)
        fused = fuse([shipped, first_stage])
        rankers = [shipped, *parts.values()]
        agree = (sum(1 for r in rankers if r and fused and r[0] == fused[0][0]) / len(rankers)
                 if fused else 0.0)
        return fused, agree

    def route(self, query: str, *, use_chooser: bool = True) -> Route:
        """`use_chooser=False` keeps stage 2 off the model for this request (the
        shadow rollout's default): an unsure retrieval then returns its top
        candidate marked `uncertain`, exactly as with no chooser configured."""
        fused, agree = self.rank(query)
        if not fused:
            return Route(ABSTAIN, None, 1, 0.0, (), reason="nothing matched")
        names = tuple(name for name, _ in fused[: self.top_k])
        margin = margin_of(fused)
        t = self.thresholds
        if agree >= t.agree and margin >= t.margin:
            return Route(TOOL, names[0], 1, round(min(agree, 1.0), 3), names,
                         reason=f"agree={agree:.2f} margin={margin:.2f}")
        return self._stage_two(query, names, agree, margin, use_chooser)

    def _stage_two(self, query: str, names: tuple[str, ...], agree: float, margin: float,
                   use_chooser: bool = True) -> Route:
        fallback = Route(TOOL, names[0], 1, round(agree, 3), names, uncertain=True,
                         reason=f"unsure: agree={agree:.2f} margin={margin:.2f}")
        if self.chooser is None or not use_chooser:
            return fallback
        cards = [tool_cards.compact(n, self.descriptions.get(n, "")) for n in names]
        try:
            answer = self.chooser.choose(query, cards)
        except Exception as exc:                                   # noqa: BLE001
            # A provider failure must not fail the request; say so and fall back.
            log.warning("tool chooser failed: %s", exc)
            return Route(fallback.decision, fallback.tool_id, 1, fallback.confidence, names,
                         uncertain=True, reason=f"chooser failed: {type(exc).__name__}",
                         chooser_called=True)
        return replace(self._accept(answer, names, fallback), chooser_called=True)

    @staticmethod
    def _accept(answer, names: tuple[str, ...], fallback: Route) -> Route:
        """A chooser's answer is untrusted: only the three fields, only an offered tool."""
        if (not isinstance(answer, dict) or set(answer) - {"decision", "tool_id", "confidence"}
                or answer.get("decision") not in DECISIONS):
            return Route(fallback.decision, fallback.tool_id, 1, fallback.confidence, names,
                         uncertain=True, reason="chooser answer malformed")
        confidence = answer.get("confidence", 0.0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
            confidence = 0.0
        decision, tool_id = answer["decision"], answer.get("tool_id")
        if decision == TOOL:
            if tool_id not in names:
                return Route(fallback.decision, fallback.tool_id, 1, fallback.confidence, names,
                             uncertain=True, reason="chooser named a tool it was not offered")
            return Route(TOOL, tool_id, 2, float(confidence), names, reason="chooser")
        return Route(decision, None, 2, float(confidence), names, reason="chooser")


CHOOSER_SYSTEM = (
    "You choose which tool serves a user's request. You are given the request and "
    "candidate tools, one per line. Answer with one JSON object and nothing else: "
    '{"decision": "TOOL" | "CLARIFY" | "ABSTAIN", "tool_id": <candidate name or null>, '
    '"confidence": <0 to 1>}. TOOL when exactly one candidate does what was asked. '
    "CLARIFY when the request names no target or fits several candidates equally. "
    "ABSTAIN when no candidate does what was asked, or several separate tasks are asked at once.")


def _chooser_user(query: str, cards: list[str]) -> str:
    return "Request: " + json.dumps(query) + "\nCandidates:\n" + "\n".join(cards)


def _parse_answer(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)


@dataclass
class GatewayChooser:
    """Stage 2 through FRIDAY's model gateway (friday/model_gateway.py).

    `infer(system, user) -> str` is injected so tests use a fake and the live
    wiring uses `gateway_infer()`. The model is told to answer with the three
    fields as JSON; whatever it returns is parsed strictly and then validated
    again by the router."""

    infer: Callable[[str, str], str]
    system: str = field(default=CHOOSER_SYSTEM)

    def choose(self, query: str, cards: list[str]) -> dict:
        return _parse_answer(self.infer(self.system, _chooser_user(query, cards)))


def gateway_infer(task_class: str = "SIMPLE", timeout_s: float = 8.0) -> Callable[[str, str], str]:
    """`infer` for GatewayChooser over the model gateway (a paid call: the gateway's
    spend lease and day budget apply). Raises on any non-ok result."""
    from friday import model_gateway as mg

    def infer(system: str, user: str) -> str:
        request = mg.ModelGatewayRequest(
            objective_id="tool-router", task_class=task_class,
            context_package=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            timeout_s=timeout_s, max_output_tokens=60, temperature=0.0, worker="tool_router")
        result = mg.gateway().infer(request)
        if result.status != "ok":
            raise RuntimeError(f"gateway {result.status}: {'; '.join(result.warnings) or 'no provider'}")
        return result.response

    return infer


def run_cli(cmd: list[str], prompt: str, timeout_s: float) -> str:
    """Run `cmd` with `prompt` on stdin and return its stdout. No shell; raises
    subprocess.TimeoutExpired past `timeout_s`."""
    return subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout_s).stdout


@dataclass
class CliChooser:
    """Stage 2 through the `claude` CLI, for measuring a chooser (the benchmark) without
    the model gateway, which has no CLI route (friday/model_gateway.py).

    The request is untrusted text, so the whole prompt travels on stdin and never in argv.
    `run(cmd, prompt, timeout_s) -> stdout` is injected so tests use a fake. Each call's
    reported cost goes to `on_cost(usd)` - also when the call then fails, since the spend
    happened. A CLI error result or an unparseable answer raises, and the router falls
    back to its retrieval top-1 marked `chooser_called`."""

    model: str = "haiku"
    max_budget_usd: float = 0.05
    timeout_s: float = 60.0
    exe: str | None = None
    run: Callable[[list[str], str, float], str] = run_cli
    on_cost: Callable[[float], None] | None = None
    system: str = field(default=CHOOSER_SYSTEM)

    def command(self) -> list[str]:
        return [self.exe or shutil.which("claude") or "claude", "-p", "--model", self.model,
                "--output-format", "json", "--max-turns", "1",
                "--max-budget-usd", f"{self.max_budget_usd:.2f}",
                "--strict-mcp-config", "--settings", '{"disableAllHooks": true}']

    def choose(self, query: str, cards: list[str]) -> dict:
        prompt = self.system + "\n\n" + _chooser_user(query, cards)
        outer = json.loads(self.run(self.command(), prompt, self.timeout_s))
        if not isinstance(outer, dict):
            raise ValueError("claude CLI result is not a JSON object")
        cost = outer.get("total_cost_usd")
        if self.on_cost is not None and isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self.on_cost(float(cost))
        if outer.get("is_error") or str(outer.get("subtype", "")).startswith("error"):
            raise RuntimeError(f"claude CLI stopped: {outer.get('subtype') or 'is_error'}")
        result = outer.get("result")
        if not isinstance(result, str):
            raise ValueError("claude CLI result has no text")
        return _parse_answer(result)
