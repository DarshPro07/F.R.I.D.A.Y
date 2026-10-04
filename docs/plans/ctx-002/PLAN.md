# CTX-002 staged tool router (owner ruling 2026-10-03b)

Services: mcp-tools (router), fabric (model pins). Status: Done as PARTIAL
(results below; the held-out metrics and the live chooser are open).

## Objective
One search finds the needed long-tail tool in the top six for 75.9% of
development requests. Build a staged router: strong candidate recall first,
a reasoning chooser only when retrieval is uncertain.

## Stages
1. Adjudicate labels (below), before any model is measured.
2. Baseline: router + BM25 + hashed n-gram + bge embedding, reciprocal-rank
   fused (`friday/tool_router.py`). The MiniLM reranker lowered recall and cost
   1.5 s, so it is out of routing (pin and adapter stay, unused).
3. bge-small against bge-base on the same data, weights and tool text. bge-large
   only if base wins materially.
4. Tool cards (`friday/tool_cards.py`): does, does_not, aliases, examples,
   confusable_with, domain. Written from the tools' behaviour, never from a
   dev or holdout request.
5. Stage 2: a confident retrieval routes directly; otherwise a chooser sees
   compact cards for the top k and may return only TOOL / CLARIFY / ABSTAIN.
   Tests use a fake chooser; no paid call comes from the benchmark.

## Adjudication rule (written before any measurement)
`scripts/label_adjudication.py` implements it; its output is an overlay,
`docs/plans/ctx-002/label_overlay.json`. `data/` is not edited and every
original label stays; the benchmark reads the overlay on top.

- A. Substitution. Two tools are substitutes when each one's registered
  description fully carries out what the request literally asks and the text has
  no cue only one of them satisfies. Sets and their cues are declared in the
  script, from the tools' documented behaviour. A matching row gains the other
  set members as `also_correct`.
- C. Clarify. A bare imperative whose only object is a pronoun (`close it`),
  with no turns or context to resolve it, is `expect=CLARIFY`, original label
  kept as `also_correct`.
- Never from a model: no label, set member or cue comes from a retriever or
  model output. The same rule runs, unchanged, over the holdout, and no model
  result on the holdout is read before it runs.
- Not done: a row-by-row human review. The rule covers the substitutions
  stated; other ambiguity is a listed gap, not guessed.

## Measurement
Success@1/3/5/10/20, MRR, ms per request and peak RAM per model, on the
long-tail dev rows with adjudicated labels. The confident-route thresholds are
chosen on dev only. Stage 2 is measured only as far as no paid call allows:
the share of requests it receives, the coverage of the correct tool in its
candidate list, and the false-confident rate on ABSTAIN rows. The live chooser
accuracy is a gap.

## Results (development only; 965 long-tail requests, 190 tools, adjudicated labels)
Reproduce: `scripts/bench_tool_disclosure.py --out <f> --embedder <pin id>
--models-dir F:/F.R.I.D.A.Y/cache/models [--thresholds]`. A named embedder that
cannot load now stops the run (it used to measure the model-free hybrid and label
it the model's result). Raw output: `bench_small.json`, `bench_base.json` here.

| ranker (with tool cards) | S@1 | S@3 | S@5 | S@10 | S@20 | MRR | ms/req | peak RAM |
|---|---|---|---|---|---|---|---|---|
| shipped router alone | .381 | .629 | .714 | .825 | .885 | .531 | 9 | - |
| hybrid + bge-small | .450 | .701 | .764 | .858 | .932 | .592 | 24 | 265 MB |
| hybrid + bge-base | .444 | .696 | .778 | .869 | .933 | .590 | 39 | 584 MB |

Hybrid without cards, bge-small: .423/.685/.763/.853/.926, MRR .572 - the cards
add 2.7 points at S@1. Hybrid with no model at all: S@1 .386, S@10 .837.
Peak RAM is the process peak working set with document vectors already cached.

Decisions:
- bge-small stays the embedder. bge-base moves S@5 +1.5 and S@10 +1.1 points and
  S@1 -0.6 for 1.6x the latency and 2.2x the RAM: not a material win, so
  bge-large (1.34 GB) was not fetched.
- The MiniLM reranker stays out of routing (run 2: it lowered recall, 1.5 s).
- Direct routing is off. At the 95% precision target the sweep finds no cell
  that routes anything, so every request is a stage-2 request: the router is
  staged in structure, but its confidence signal cannot yet skip the chooser.
  Loosest cells: 70% target -> 4.8% routed at 70.7% right; 60% -> 9.5% at 62.6%.
  Score-based signals were tried on the same development rows and did no better
  (cosine top-1 >= .70, cosine gap >= .02, fused-and-cosine top agree: 13% routed
  at 65.8% right; at >=20% routed, 63% right). Top-1 is right on 45% of rows, so
  the ceiling is the data's ambiguity, not the threshold.
- Stage-2 candidate coverage: the correct tool is in the top 10 for 85.8% of
  requests, in the top 20 for 93.2%. A chooser can only be right on the first
  number's share; a larger k is the cheap lever (measure it with the live chooser).

## Rollout: legacy | shadow | staged (owner rulings 2026-10-04)
`friday/tool_router_rollout.py` puts the staged router behind `Router.search`
(`FRIDAY_TOOL_ROUTER_MODE`, default `legacy`; the shipped scorer is now
`Router.legacy_search`, which the benchmark and the staged router's own shipped
ranking call, so nothing recurses).

- **legacy**: unchanged; nothing is built or logged.
- **shadow**: legacy's answer is returned untouched. A one-worker background
  queue (8 pending, extras are counted as `dropped`, never waited for) runs the
  staged router and logs one record per request: legacy choice and top, staged
  top-k and choice, decision, stage, confidence, latency, would-clarify,
  uncertain, chooser-called, fallback. `Router.note_succeeded` (called by
  `use_capability` after a call returns without raising) logs the observed tool
  against the latest search. Stage 2 calls a chooser only for a deterministic
  sample (`SAMPLE_RATE` of request hashes) inside a hard `SAMPLE_BUDGET` of calls
  per process; both default to 0, so shadow costs no model call until configured.
  Records hold a hash of the request, never its text; `FRIDAY_TOOL_ROUTER_LOG`
  adds a JSON-lines copy.
- **staged**: the staged router answers (chooser's tool first, then the other
  candidates). A failed or missing build, an exception or a missed
  `FRIDAY_TOOL_ROUTER_TIMEOUT_S` deadline serves the legacy answer for that
  request; a failed build is not retried for 300 s. The deadline covers the
  first index build too: build and route both run on the route pool, the caller
  waits at most the timeout (default 2 s, so `search_capabilities` can be held
  that long in staged mode; legacy mode is never held), and a build that
  outlives the deadline finishes in the background for the next request.
  `Router.load` -> `reset()` never waits for a build in flight (its result is
  dropped as stale). A `TimeoutError` raised by the build or route is an error,
  not a missed deadline. CLARIFY keeps every candidate and adds a `clarify`
  line to the `search_capabilities` result; ABSTAIN serves the legacy answer
  (counted as `abstain` and `fallbacks`, recorded with `fallback: "abstain"`):
  no candidate does what was asked, so the staged shortlist would only mislead.
- **Metrics** (`Rollout.metrics.snapshot()`): requests, chooser calls and
  `chooser_invocation_rate`, fallbacks, errors, timeouts, clarify, dropped,
  `p95_latency_ms`.
- **Promotion to staged** needs held-out and shadow evidence: Success@1 >= 0.90,
  Success@3 >= 0.97, ambiguous-intent handling, acceptable p95 latency and
  chooser invocation rate, no golden-journey regression. None of that evidence
  exists yet; the default stays legacy.

Tests: `tests/test_tool_router_rollout.py` (each mode, the fallbacks including a
slow first build, a `TimeoutError` from build or route and a stopped pool, shadow
never changes the answer, sample and budget, the invocation-rate and p95
metrics, the Router wiring) and the `chooser_called` / `use_chooser` cases in
`tests/test_tool_router.py`.

## Open (listed as evidence gaps)
- Ruling 1 not done: the stage-2 chooser through a coding-agent CLI (`claude -p`,
  Codex, Antigravity) needs a CLI provider in the model gateway, which does not
  exist (`model_gateway.py` has none); no chooser is configured, so staged mode
  today discloses the top candidates as `uncertain`. The seam is `Rollout(chooser=)`.
- Ruling 2 not done: the sealed holdout has not been measured; it is measured
  once, after the chooser and thresholds are fixed on dev.
- No shadow run has happened, so no shadow metrics exist to promote on.
- Live stage-2 accuracy: no paid call from the benchmark, the OpenAI key was
  invalid 2026-10-02 and the Gemini key rejected; `GatewayChooser` is tested with
  a fake only.
- Held-out metrics: the holdout was not read (the rule is applied to it in the
  overlay, by text rule only); CTX-002 stays PARTIAL until they are measured.
- Adjudication covers the stated substitution sets and bare-pronoun rows, not a
  row-by-row human review.
- Cards are written from tool behaviour; no check can prove none echoes a dev or
  holdout phrase beyond the review that wrote them.
- `scripts/upstream_lock.py --check` reports drift in this worktree because the
  clone directories are empty here (46 clone rows differ; the 3 model rows match
  `derive()`); run it in the main checkout.
