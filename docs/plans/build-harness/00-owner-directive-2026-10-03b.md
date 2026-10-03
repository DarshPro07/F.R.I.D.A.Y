# Owner ruling 2026-10-03 (b): CTX-002 staged router

The owner's answer, extracted verbatim from the session transcript, to the controller's question after CTX-002's second run (2026-10-03, wave 55) came back BLOCKED: pinned bge-small embedder and MiniLM reranker built as fabric providers; best dev R@1 0.41 / R@3 0.68 against 0.90 / 0.97; the reranker lowered recall and cost 1.5 s per request; many misses were requests with more than one right tool.

It supersedes the 2026-10-02 (h) instruction not to relabel the dev split: ambiguous labels are adjudicated first.

---

Yes — we can use **option 2 and option 3 together**, but not naively.

My recommendation is:

> **Upgrade retrieval first, then use a larger reasoning model only on the uncertain top-k cases.**

That gives better quality without making every request expensive.

The reason is simple: your current `Recall@5 = 0.75`. If the correct tool is missing from the top 5, even a perfect large model cannot choose it. So option 3 by itself has a hard ceiling. Tool-retrieval research shows this is a real problem: general-purpose retrieval models often perform poorly on tool selection, and downstream LLM accuracy falls with them. [ACL Anthology](https://aclanthology.org/2025.findings-acl.1258/?utm_source=chatgpt.com)

At the same time, larger embedding models do improve retrieval benchmarks somewhat, but not massively. BGE's own published MTEB retrieval numbers go roughly from `51.68` for small, to `53.25` for base, to `54.29` for large. So moving from small → large is not guaranteed to turn `0.41` into `0.90`. [Hugging Face](https://huggingface.co/BAAI/bge-base-en-v1.5?utm_source=chatgpt.com)

For your hardware and `<2 GB per model` rule, both are technically possible at the weight-file level: `bge-base-en-v1.5` has a 438 MB safetensors file, and `bge-large-en-v1.5` has a 1.34 GB safetensors file. [Hugging Face](https://huggingface.co/BAAI/bge-base-en-v1.5/tree/main?utm_source=chatgpt.com)

## What I would build

Use a **3-stage router**:

```text
User request
    ↓
Stage 1 — cheap hybrid retriever
BM25 + embedding
    ↓
top 10–20 candidates
    ↓
Stage 2 — semantic chooser
strong model only if uncertain
    ↓
choose tool
or CLARIFY
or ABSTAIN
    ↓
Stage 3 — execute + verify
```

Not:

```text
every request
→ big LLM
```

### Fast path

If the hybrid router has something like:

```text
top1 score high
margin to top2 high
intent unambiguous
```

then:

```text
direct selection
~25 ms
zero large-model call
```

### Hard path

If:

```text
top1/top2 close
multiple plausible tools
ambiguous language
low confidence
```

then give a bigger model:

```text
user request
+
top 10 or top 20 compact tool cards
```

and ask it to return only:

```json
{
  "decision": "TOOL | CLARIFY | ABSTAIN",
  "tool_id": "...",
  "confidence": 0.0
}
```

This is much better than letting it browse the whole catalog.

ToolRerank and newer tool-retrieval work support exactly this kind of retrieve-then-rerank architecture rather than asking one model to search every tool directly. [ACL Anthology](https://aclanthology.org/2024.lrec-main.1413/?utm_source=chatgpt.com)

## For option 2: benchmark `bge-base` before `bge-large`

I would not immediately download the 1.34 GB model.

Run:

```text
bge-small baseline
vs
bge-base
vs
bge-large
```

using the **same corrected labels**.

Why start with base?

Because:

```text
bge-small retrieval: 51.68
bge-base:             53.25
bge-large:            54.29
```

on BGE's published MTEB retrieval suite. Base captures much of the improvement at far lower memory cost. [Hugging Face](https://huggingface.co/BAAI/bge-base-en-v1.5?utm_source=chatgpt.com)

So:

```text
if bge-base gives meaningful lift:
    maybe stop there

if large gives another substantial FRIDAY-specific lift:
    promote large
```

Do not promote because it is bigger.

Promote because it wins FRIDAY's benchmark.

## Option 3 should use top-10 or top-20, not top-5

Your current:

```text
Recall@5 = 0.75
```

is too low.

First measure:

```text
Success@5
Success@10
Success@20
```

after fixing the labels.

If you get something like:

```text
Success@5  = 0.82
Success@10 = 0.94
Success@20 = 0.98
```

then the architecture becomes obvious:

```text
retrieve top 20
↓
large model sees 20 tiny tool cards
↓
select / clarify
```

That could potentially hit your end-to-end target without needing a giant embedding model at all.

Recent Toollery work specifically uses compact top-k candidate sets before final LLM selection because full-library prompting adds token cost, latency and distractors. [arXiv](https://arxiv.org/abs/2609.22218?utm_source=chatgpt.com)

## Big model should also resolve ambiguity

This is where option 3 is strongest.

Example:

```text
"hold the computer"
```

Candidates:

```text
lock
sleep
hibernate
```

A pure retriever cannot truly know what the user meant.

A reasoning model can return:

```text
CLARIFY:
"Do you want me to lock the screen, sleep, or hibernate?"
```

That is actually a **better answer than forcing Recall@1**.

Likewise:

```text
"show me the music"
```

could map to:

```text
YouTube
Spotify
local music
```

A good router should recognize semantic ambiguity rather than hallucinate certainty.

## I would not use your failed reranker

Drop:

```text
ms-marco-MiniLM-L6-v2
```

from production routing.

It was trained as a generic passage reranker, not specifically for FRIDAY tool semantics, and in your actual benchmark it:

```text
hurt quality
+
added ~1.5 s
```

That's enough evidence.

Sentence Transformers' own docs position it as a passage reranker trained for MS MARCO, not as a specialized tool selector. [Sentence Transformers](https://sbert.net/docs/cross_encoder/pretrained_models.html?utm_source=chatgpt.com)

Tool-specific retrieval research also finds that ordinary IR models are often not tool-savvy and that tool-specific training/document expansion helps more. [ACL Anthology](https://aclanthology.org/2025.findings-acl.1258/?utm_source=chatgpt.com)

## Better than just a larger model: improve tool cards

This may give a bigger lift than jumping from 133 MB to 1.3 GB.

For every tool, index:

```yaml
id: computer_lock

does:
  - lock workstation
  - require login to resume

aliases:
  - lock my PC
  - secure computer
  - lock screen

does_not:
  - sleep
  - hibernate
  - shut down

confusable_with:
  - computer_sleep
  - computer_hibernate

examples:
  - "lock the computer"
  - "secure my workstation"
```

Tool-DE found that simply enriching tool documentation substantially improved retrieval, and it built specialized retriever/reranker models around that richer representation. [arXiv](https://arxiv.org/abs/2510.22670?utm_source=chatgpt.com)

That is highly relevant to FRIDAY.

# What I would tell the builder

Use this:

```text
CTX-002 decision:

Do not choose option 2 OR option 3 exclusively.

Build a staged benchmark.

1. Fix/adjudicate ambiguous benchmark labels first.

2. Keep the current BM25 + embedding hybrid as baseline.

3. Benchmark:
   A. bge-small
   B. bge-base-en-v1.5
   C. bge-large-en-v1.5

   Same dataset.
   Same hybrid weights.
   Same tool metadata.
   Same latency/RAM measurements.

4. Measure:
   Success@1
   Success@3
   Success@5
   Success@10
   Success@20
   MRR
   latency
   peak RAM

5. Do not promote the larger model unless it materially wins.

6. Enrich tool cards with:
   aliases
   examples
   does
   does_not
   confusable_with
   domain/category

7. Re-measure retrieval.

8. Add conditional Stage-2 LLM selection:

   if retrieval confidence high:
       choose deterministically

   if low confidence or ambiguous:
       send top-k compact candidates to a reasoning model

9. Stage-2 model may return only:
   TOOL
   CLARIFY
   ABSTAIN

10. Start with top-10.
    If correct candidate coverage is insufficient, test top-20.

11. Measure end-to-end:
    correct tool
    correct clarification
    correct abstention

12. Use large/cloud reasoning model ONLY on uncertain cases.

13. CTX-002 remains PARTIAL until held-out metrics pass.
```

### My preferred final architecture

```text
                FRIDAY TOOL ROUTER

Request
  │
  ▼
BM25 + BGE-base/large
  │
  ▼
candidate set
  │
  ├── confidence HIGH
  │       ↓
  │    direct route
  │
  └── confidence LOW
          ↓
      large model
      top 10–20 only
          │
          ├── TOOL
          ├── CLARIFY
          └── ABSTAIN
```

That gives you the best of both options:

- **Option 2** improves candidate recall.
- **Option 3** improves semantic choice and ambiguity handling.
- Large-model cost happens only on difficult requests.
- Most requests remain fast and local.

That is what I would choose for FRIDAY.
