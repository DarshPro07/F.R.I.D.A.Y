"""
RSCH-002: deep research behind one interface.

A provider turns (question, depth) into sources. `research()` is the only
caller-facing entry: it routes the depth (RSCH-001), holds each provider to the
canonical inquiry (drift guard) and turns a provider's exception into a failed
result, so an optional provider never takes the parent objective down.

The first provider wraps the existing web research toolset. The second, in the
tests, is a deterministic fake. A third-party deep-research upstream must come
in through friday/fabric_adapters/, not by registering here directly.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol, runtime_checkable

from friday import contracts as c
from friday.toolsets import research


@dataclasses.dataclass(frozen=True)
class ProviderResult:
    """What every provider returns. sources: dicts with url, title, chars."""

    provider: str
    question: str
    depth: str
    status: str
    sources: tuple[dict, ...] = ()
    rejected: tuple[dict, ...] = ()
    error: str = ""


@runtime_checkable
class FridayResearchProvider(Protocol):
    name: str

    async def research(self, run: c.Run, question: str, depth: str) -> ProviderResult: ...


class WebResearchProvider:
    """The existing research toolset (search, read, rank, budget) as a provider."""

    name = "web"

    async def research(self, run: c.Run, question: str, depth: str) -> ProviderResult:
        budget = research.DEPTH_BUDGETS[depth]
        result = await research.web_deep_research(
            run, question, sources=max(1, budget.max_sources), budget=budget.max_chars)
        sources = tuple(
            {"url": s.get("url", ""), "title": s.get("title", ""), "chars": s.get("chars", 0)}
            for s in (result.output or {}).get("sources", []))
        return ProviderResult(provider=self.name, question=question, depth=depth,
                              status=result.status, sources=sources,
                              error=result.error or "")


PROVIDERS: dict[str, FridayResearchProvider] = {"web": WebResearchProvider()}


async def research_with(run: c.Run, question: str, *, provider: str = "web",
                        requested: str | None = None) -> ProviderResult:
    """Research `question` with the named provider at the depth it deserves."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown research provider {provider!r}; "
                         f"available: {', '.join(sorted(PROVIDERS))}")
    depth = research.route_depth(question, requested)
    if depth == "R0":
        return ProviderResult(provider=provider, question=question, depth=depth,
                              status=c.SUCCEEDED)
    try:
        result = await PROVIDERS[provider].research(run, question, depth)
    except Exception as exc:
        return ProviderResult(provider=provider, question=question, depth=depth,
                              status=c.FAILED,
                              error=f"{type(exc).__name__}: {str(exc)[:160]}")
    kept, rejected = research.filter_branches(
        research.CanonicalInquiry.from_question(question), list(result.sources))
    return dataclasses.replace(result, sources=tuple(kept), rejected=tuple(rejected))
