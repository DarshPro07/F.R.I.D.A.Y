"""
SharedBrainAdapter — the one door between Friday/Hermes and GBrain.

Phase 2 ruling (boss): GBrain answers "what do we already know?" and
NOTHING else. ObjectiveRun keeps "what are we doing", WorkRun keeps "what
executed", Skills keep "how do we repeat it". This adapter is deliberately
small so that separation cannot blur:

* Verbs surface ONLY. The child process is `gbrain serve --surface verbs`
  (7 memory verbs; hidden operations fail closed). Friday never sees the
  ~20-op starter surface or the full catalog.
* One owner brain (PGLite at D:/friday-brain), sources per topic -
  friday-core / hermes-core / research / project-<id> - never one brain
  per project.
* recall is the workhorse and ALWAYS carries budget_tokens: packing is
  server-side and the response reports budget_used/dropped_count. H
  economics chooses the budget; nothing here re-trims client-side.
* synthesize is NOT exposed on this adapter at all. The already-selected
  model reasons over recalled evidence; a separate GBrain LLM call needs
  an explicit H justification and its own review.
* ADMISSION IS FILTERED HERE, before anything reaches the brain: secret
  shapes and banking content are refused at this boundary (insertion = 0),
  reusing the same redaction rules the browser capability trusts.

Transport: stdio JSON-RPC per call (spawn, initialize, call, exit). A
persistent daemon would be faster; correctness first - the brain is
PGLite-embedded, and per-call spawn keeps crash/lock semantics trivial.
Measured cost is recorded by the A/B baseline, not assumed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass

#: bun is spawned for every health check and every verb call. Without this the
#: console it opens flashes a black window over whatever the owner is doing --
#: once a minute, forever, because the UI polls the brain's health.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

logger = logging.getLogger("friday-agent.brain")

#: The pinned clone. `bun run src/cli.ts` is the launcher the install
#: verified; a compiled binary can replace it without touching callers.
_BUN = os.environ.get("GBRAIN_BUN", r"C:\Users\marke\.bun\bin\bun.exe")
_CLI = os.environ.get("GBRAIN_CLI", r"D:\gbrain\src\cli.ts")

#: Default recall budgets by task weight - H's initial tuning ranges
#: (boss: starting points to measure against, not hard universal limits).
BUDGETS = {
    "trivial": 400,
    "bounded": 1200,
    "project": 2500,
    "deep": 4000,
}

#: Admission refusal patterns: secret-shaped material never enters the
#: brain. Deliberately broad on key shapes, narrow on prose.
_SECRET_SHAPES = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|api[_-]?key\s*[:=]\s*\S{8,}|"
    r"bearer\s+[A-Za-z0-9._-]{20,}|BEGIN [A-Z ]*PRIVATE KEY|"
    r"password\s*[:=]\s*\S+|ANTHROPIC_TOKEN=\S+)", re.IGNORECASE)


class AdmissionRefused(ValueError):
    """The fact was refused BEFORE ingestion. Not an error to hide."""


#: Provenance classes that name something READ, not something verified
#: (invariant A-048 "memory"). The brain ledger is tier-3 of Friday's memory
#: - its facts come back to her as RULES on the next turn - so a fact whose
#: only source is a page, a message, a tool result or a worker's word is an
#: instruction laundered through memory. Such a fact may enter only through
#: `memory_promotion.promote()` (evidence, confidence, contradiction check),
#: never through this adapter's direct write.
UNTRUSTED_PROVENANCE = re.compile(
    r"^\s*(page|web|url|http|https|browser|scrape|search|email|mail|message|"
    r"chat|dm|sms|telegram|slack|discord|handoff|worker|hermes|subagent|"
    r"tool[_ -]?result|tool[_ -]?output|model|llm|assistant|untrusted|external)"
    r"(\s*[:\-/(\s]|\s*$)", re.I)


def _untrusted_provenance(provenance: str) -> str | None:
    """Why this provenance cannot write directly, or None if it may."""
    if UNTRUSTED_PROVENANCE.match(provenance or ""):
        return (f"provenance {provenance[:60]!r} names read material; a fact "
                "from a page, a message or a worker goes through the promotion "
                "gate (memory_promotion.promote), not straight into the ledger")
    return None


def _sensitive(text: str) -> str | None:
    """Why this text must not be remembered, or None if admissible."""
    if _SECRET_SHAPES.search(text):
        return "secret-shaped content"
    try:
        from friday.sensitive_domains import is_sensitive_text
        if is_sensitive_text(text):                      # pragma: no cover
            return "sensitive-domain content"
    except (ImportError, AttributeError):
        pass
    lowered = text.lower()
    if any(w in lowered for w in ("account number", "routing number",
                                  "card number", "cvv", "iban ")):
        return "banking-shaped content"
    return None


@dataclass
class BrainAnswer:
    """One recall outcome in the shape Friday reasons over."""

    facts: list
    results: list
    budget_used: int
    dropped_count: int
    degraded: str = ""

    def compact(self) -> dict:
        return {
            "facts": [
                {"fact": f.get("fact"), "entity": f.get("entity_slug"),
                 "provenance": f.get("provenance"),
                 "fact_id": f.get("fact_id")}
                for f in self.facts],
            "snippets": [
                {"slug": r.get("slug"), "title": r.get("title"),
                 "chunk": (r.get("chunk") or "")[:800]}
                for r in self.results],
            "budget_used": self.budget_used,
            "dropped_count": self.dropped_count,
            **({"degraded": self.degraded} if self.degraded else {}),
        }


class SharedBrainAdapter:
    """Friday's (and, via MCP registration, Hermes's) door to the brain."""

    def __init__(self, bun: str | None = None, cli: str | None = None,
                 timeout: float = 60.0) -> None:
        self._bun = bun or _BUN
        self._cli = cli or _CLI
        self._timeout = timeout

    # -- transport ---------------------------------------------------------

    def _call(self, verb: str, arguments: dict) -> dict:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "friday-shared-brain",
                                       "version": "1.0"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": verb, "arguments": arguments}},
        ]
        # Interactive pipe, NOT communicate-with-closed-stdin: gbrain
        # serve shuts down on stdin EOF, and a slow tool call (semantic
        # recall waits on an embedding) would be killed mid-answer if we
        # closed stdin up front. Keep stdin open until id=2 arrives.
        proc = subprocess.Popen(
            [self._bun, "run", self._cli, "serve", "--surface", "verbs"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            cwd=os.path.dirname(self._cli), **_NO_WINDOW)
        import threading
        answer: dict = {}
        error: list = []

        def read_out():
            for line in proc.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") == 2:
                    if "error" in message:
                        error.append(str(message["error"])[:300])
                    else:
                        content = message.get("result", {}).get(
                            "content", [])
                        for chunk in content:
                            if chunk.get("type") == "text":
                                try:
                                    answer.update(
                                        json.loads(chunk["text"]))
                                except json.JSONDecodeError:
                                    error.append(chunk["text"][:300])
                    return

        reader = threading.Thread(target=read_out, daemon=True)
        reader.start()
        try:
            for request in requests:
                proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
            reader.join(timeout=self._timeout)
            if reader.is_alive():
                raise RuntimeError(f"gbrain {verb} timed out after "
                                   f"{self._timeout}s")
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if error:
            raise RuntimeError(error[0])
        if not answer and verb != "forget":
            raise RuntimeError(f"gbrain gave no response for {verb}")
        return answer

    # -- verbs -------------------------------------------------------------

    def recall(self, query: str = "", *, entity: str = "",
               budget: str = "bounded",
               budget_tokens: int | None = None) -> BrainAnswer:
        arguments: dict = {"budget_tokens":
                           budget_tokens or BUDGETS.get(budget, 1200)}
        if query:
            arguments["query"] = query
        if entity:
            arguments["entity"] = entity
        answer = self._call("recall", arguments)
        return BrainAnswer(
            facts=answer.get("facts") or [],
            results=answer.get("results") or [],
            budget_used=int(answer.get("budget_used") or 0),
            dropped_count=int(answer.get("dropped_count") or 0),
            degraded=str(answer.get("search_degraded") or ""))

    def remember(self, fact: str, *, provenance: str, entity: str = "",
                 kind: str = "fact", ttl: str = "") -> dict:
        """Admission-filtered write. Refuses secrets/banking BEFORE the
        brain ever sees the text - insertion count for refused content
        is structurally zero."""
        reason = _sensitive(fact)
        if reason:
            raise AdmissionRefused(
                f"refused before ingestion: {reason}. The brain stores "
                f"knowledge, never credentials or banking material.")
        if not provenance.strip():
            raise ValueError("provenance is required - a fact without a "
                             "source is a rumor")
        untrusted = _untrusted_provenance(provenance)
        if untrusted:
            raise AdmissionRefused(f"refused before ingestion: {untrusted}")
        arguments = {"fact": fact, "provenance": provenance[:500]}
        if entity:
            arguments["entity"] = entity
        if kind != "fact":
            arguments["kind"] = kind
        if ttl:
            arguments["ttl"] = ttl
        out = self._call("remember", arguments)
        # System-of-record rule (boss, Gate C): PGLite is a DERIVED index,
        # never the only copy. Every admitted durable fact is appended to
        # a canonical git-tracked JSONL ledger the brain can be rebuilt
        # from (replay_ledger). Runtime-only state stays in runtime DBs.
        if out.get("status") == "inserted":
            self._ledger_append({
                "fact": fact, "provenance": provenance[:500],
                "entity": entity, "kind": kind, "ttl": ttl,
                "fact_id": out.get("id"),
                "recorded_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc).isoformat()})
        return out

    #: Canonical system-of-record ledger. Git-tracked, append-only,
    #: human-diffable. Override for tests via GBRAIN_LEDGER.
    _LEDGER_DEFAULT = (
        r"E:\friday-tony-stark-demo-main\docs\knowledge\brain_ledger.jsonl")

    def _ledger_path(self) -> str:
        return os.environ.get("GBRAIN_LEDGER", self._LEDGER_DEFAULT)

    def _ledger_append(self, entry: dict) -> None:
        try:
            path = self._ledger_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:                       # pragma: no cover
            # The ledger failing must not lose the brain write - but say so.
            logger.warning("brain ledger append failed: %s", exc)

    def replay_ledger(self, ledger_path: str | None = None) -> dict:
        """Rebuild a (fresh) brain from the canonical ledger: replay every
        entry through remember. Duplicates collapse server-side, so replay
        is idempotent. Run against a DISPOSABLE brain via GBRAIN_HOME when
        testing - never destructively against production."""
        path = ledger_path or self._ledger_path()
        replayed = skipped = 0
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                try:
                    out = self._call("remember", {
                        "fact": entry["fact"],
                        "provenance": entry.get("provenance") or "ledger",
                        **({"entity": entry["entity"]}
                           if entry.get("entity") else {}),
                        **({"kind": entry["kind"]}
                           if entry.get("kind") not in (None, "", "fact")
                           else {})})
                    replayed += 1 if out.get("status") in (
                        "inserted", "duplicate", "superseded") else 0
                except Exception:                            # noqa: BLE001
                    skipped += 1
        return {"replayed": replayed, "skipped": skipped}

    def entity(self, name: str) -> dict:
        return self._call("entity", {"name": name})

    def forget(self, fact_id: str, reason: str = "") -> dict:
        arguments: dict = {"id": str(fact_id)}
        if reason:
            arguments["reason"] = reason
        return self._call("forget", arguments)

    def available(self) -> bool:
        """Cheap liveness: brain reachable AND answering the protocol.
        Failure here must degrade gracefully - objectives continue."""
        try:
            self._call("recall", {"budget_tokens": 50, "limit": 1})
            return True
        except Exception:                                    # noqa: BLE001
            return False
