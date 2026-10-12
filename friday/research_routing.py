"""
RSCH-004: research outputs go to their owners, by kind.

A research run produces four different things and each has one home:

  * the raw findings and their sources    -> the research store
                                             (`research_cache`)
  * what is claimed, and why it is believed -> epistemic-ledger records
                                             (`Artifact.to_ledger`)
  * a pointer to the run for the project   -> the Project Brain's `research`
                                             record (`project_brain`)
  * a small, sourced, durable fact         -> memory, through the promotion
                                             gate as read material
                                             (`memory_promotion`)

Long-term memory is the narrowest of the four: a fact crosses into it only if
the run was synthesised (a run with none holds fetched pages, which are corpus),
it is short, the gate accepts it, and no more than MAX_PROMOTED of them per
run. The corpus itself, and any fact too long to be a fact, stay in the
research store, where they carry their sources and expire with their
freshness class. Nothing here is filed under a person scope.

Assumed (owner not asked): 240 characters is a sentence, not a pasted passage,
and 5 facts is what one run can say that is worth remembering; both are
module constants to move if that proves wrong.
"""
from __future__ import annotations

from dataclasses import dataclass

from friday import contracts as c
from friday import research_cache
from friday.memory_promotion import Candidate, Decision, promote
from friday.project_brain import ProjectBrain
from friday.store import Store

#: A fact longer than this is research material, not a fact; memory never sees it.
MAX_FACT_CHARS = 240
#: The most facts one research run offers to memory.
MAX_PROMOTED = 5
#: The Project Brain pointer carries a summary, not the findings.
MAX_FINDING_CHARS = 500

PROJECT_FACT = "project_fact"
RESEARCH_SCOPE = "research"


@dataclass(frozen=True)
class Routed:
    """Where one research run's outputs went."""

    #: Key of the Project Brain `research` record that points at the run.
    brain_key: str
    #: The run's claims as `epistemic.LedgerClaim`s.
    ledger: tuple
    #: Gate decisions for the facts that were accepted into memory.
    promoted: tuple[Decision, ...]


def route(artifact: research_cache.Artifact, *, store: Store,
          project: str = "friday") -> Routed:
    """
    Send each output of one research run to its owner.

    Raises ValueError, before anything is written, for research that names no
    source (the research store refuses it): unsourced research has no owner.
    """
    saved = research_cache.save(artifact)        # raw findings + sources
    ledger = tuple(saved.to_ledger())
    key = "research-" + saved.question_key[:16]
    _point_brain(ProjectBrain(store), project, key, saved, ledger)
    return Routed(brain_key=key, ledger=ledger,
                  promoted=_promote_small_facts(saved, store))


def _point_brain(brain: ProjectBrain, project: str, key: str,
                 artifact: research_cache.Artifact, ledger: tuple) -> None:
    # The finding is the run's synthesis. A run without one holds only fetched
    # source text, so the Brain gets a pointer to the run and no excerpt.
    finding = artifact.synthesis.strip() or "no finding recorded"
    body = {
        "question": artifact.question,
        "finding": finding[:MAX_FINDING_CHARS],
        "claim_ids": [claim.id for claim in ledger],
        "sources": [s.url for s in artifact.sources if s.url],
        "freshness": artifact.freshness,
        # Not a `ref`: that must name a row in the main store, and the research
        # store is its own ledger. This is the key to look the run up by.
        "research_key": artifact.question_key,
    }
    write = brain.supersede if brain.get(project, "research", key) else brain.add
    write(project, "research", key, body, origin="friday")


def _promote_small_facts(artifact: research_cache.Artifact,
                         store: Store) -> tuple[Decision, ...]:
    if not artifact.synthesis.strip():
        # No synthesis: the "facts" are the fetched pages themselves (what
        # web_deep_research produces), i.e. corpus. Only a distilled run offers
        # facts to memory, however short a page is.
        return ()
    small = [f.strip() for f in artifact.facts
             if f.strip() and len(f.strip()) <= MAX_FACT_CHARS][:MAX_PROMOTED]
    evidence = [s.url for s in artifact.sources if s.url]
    accepted = []
    for fact in small:
        decision = promote(Candidate(
            statement=fact, kind=PROJECT_FACT,
            source="research:" + artifact.question_key[:16], owner="friday",
            scope=RESEARCH_SCOPE, confidence=artifact.confidence,
            evidence=evidence, provenance=c.READ_MATERIAL), store=store)
        if decision.accepted:
            accepted.append(decision)
    return tuple(accepted)
