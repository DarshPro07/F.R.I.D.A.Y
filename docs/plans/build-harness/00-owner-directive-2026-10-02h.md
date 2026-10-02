# Owner ruling 2026-10-02 (h): CTX-002 gets small local models

Recorded verbatim from the owner's answer to the controller's question after
CTX-002's run (wave 30) came back BLOCKED at Recall@1 0.369 / Recall@3 0.634
against the 0.90 / 0.97 target.

Question: "CTX-002 (context retrieval quality) needs a learned
embedding/reranker to get from Recall@1 0.37 / Recall@3 0.63 to your
0.90 / 0.97 targets. How should FRIDAY get one?"

Answer chosen: **Small local models (Recommended)** - "Approve downloading a
small embedding model plus a small reranker (together well under the 2 GB
local-model rule), pinned by hash and licence-checked through the fabric, then
benchmark them on the dev split. Local-first, no per-query cost."

Options not chosen: a hosted embedding API; relabelling the dev split first;
deferring CTX-002.

## What the controller did with it (2026-10-02)

- Picked the two models from their official repos with explicit licences and
  ONNX exports (onnxruntime, tokenizers, huggingface_hub and numpy are already
  installed, so nothing new is installed):
  - embedding `BAAI/bge-small-en-v1.5`, MIT, revision
    `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, `onnx/model.onnx` 133.1 MB
    sha256 `828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35`,
    `tokenizer.json` sha256 `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66`
  - reranker `cross-encoder/ms-marco-MiniLM-L6-v2`, Apache-2.0, revision
    `233902d25c440f23af6f7d6e94d2946bac0bee0a`, `onnx/model.onnx` 91.0 MB
    sha256 `5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a`,
    `tokenizer.json` sha256 `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66`
- Downloaded both by pin into `F:/F.R.I.D.A.Y/cache/models` (huggingface_hub
  cache layout); the ONNX hashes match the Hub's LFS hashes.
- Loaded both on CPU: 1.5 s, +240 MB RSS together; "play some music" ranks the
  Spotify tool first under both; reranking three pairs took 22 ms.
- Kept the BLOCKED run's retrievers and benchmark on `wip/ctx-002-retrievers`
  (0c13aee) and removed its stale worktree, so the next attempt starts from
  current main.
- The design the next worker gets is CTX-002's `next_action` in
  `scripts/prd_ledger.py` (PROGRESS_V7).
