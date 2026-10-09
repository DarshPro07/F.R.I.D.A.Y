"""
System One (S1-001): one small decision gateway - choose / score / yes_no /
rank / classify / confidence - in front of pluggable backends.

A "System One" call is a quick judgement over short text, not a conversation.
The caller names the question and, where it applies, the options; which
backend answers is the gateway's business. Backends are anything with a
`name` and `call(op, prompt, items)`. `MockBackend` is the deterministic one
the contract tests run against; Laya / Jev adapters plug in behind the same
two members.

Privacy is not re-implemented here. Each backend is put through
`data_class.decision` - the same seam `model_gateway` asks before a request
leaves for a provider - so locality is `privacy.LOCAL_BACKENDS` by name, and
a LOCAL_ONLY (or SECRET) request is REFUSED for every other backend before
that backend is called. A refused backend is skipped, never retried as a
fault; if nothing is left the result is `refused` and no backend saw the text.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from friday import data_class, privacy

OPERATIONS = ("choose", "score", "yes_no", "rank", "classify", "confidence")


@dataclass
class SystemOneResult:
    status: str                      # ok | refused | failed
    value: object = None
    backend: str = ""                # the backend that answered
    data_class: str = ""
    redactions: int = 0
    reason: str = ""
    #: One row per backend looked at: {backend, status, error?}.
    attempts: list[dict] = field(default_factory=list)


class MockBackend:
    """Deterministic backend: the same question always gets the same answer."""

    def __init__(self, name: str = "mock") -> None:
        self.name = name

    @staticmethod
    def _h(*parts: str) -> int:
        return int.from_bytes(hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()[:8], "big")

    def call(self, op: str, prompt: str, items: tuple[str, ...]):
        h = self._h(op, prompt, *items)
        if op in ("choose", "classify"):
            return items[h % len(items)]
        if op in ("score", "confidence"):
            return (h % 1001) / 1000
        if op == "yes_no":
            return h % 2 == 0
        if op == "rank":
            return sorted(items, key=lambda item: self._h(op, prompt, item))
        raise ValueError(f"unknown operation {op!r}")


def _check(op: str, value, items: tuple[str, ...]):
    """The backend's answer in the shape OP promises, or ValueError."""
    if op in ("choose", "classify"):
        if value not in items:
            raise ValueError(f"{op} answer {value!r} is not one of the options")
        return value
    if op in ("score", "confidence"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{op} answer {value!r} is not a number in [0, 1]")
        return float(value)
    if op == "yes_no":
        if not isinstance(value, bool):
            raise ValueError(f"yes_no answer {value!r} is not a bool")
        return value
    if not isinstance(value, (list, tuple)) or sorted(value) != sorted(items):
        raise ValueError("rank answer is not a reordering of the items")
    return list(value)


class SystemOne:
    """Asks `backends` in order and returns the first answer one may give."""

    def __init__(self, backends) -> None:
        self.backends = tuple(backends)
        if not self.backends:
            raise ValueError("SystemOne needs at least one backend")

    def choose(self, prompt: str, options, **kw) -> SystemOneResult:
        return self._ask("choose", prompt, self._options(options), **kw)

    def classify(self, prompt: str, labels, **kw) -> SystemOneResult:
        return self._ask("classify", prompt, self._options(labels), **kw)

    def rank(self, prompt: str, items, **kw) -> SystemOneResult:
        return self._ask("rank", prompt, self._options(items, distinct=True, minimum=2), **kw)

    def score(self, prompt: str, **kw) -> SystemOneResult:
        return self._ask("score", prompt, (), **kw)

    def yes_no(self, prompt: str, **kw) -> SystemOneResult:
        return self._ask("yes_no", prompt, (), **kw)

    def confidence(self, answer: str, prompt: str, **kw) -> SystemOneResult:
        """How sure the backend is that ANSWER is right for PROMPT (0..1)."""
        return self._ask("confidence", prompt, (answer,), **kw)

    @staticmethod
    def _options(options, *, distinct: bool = False, minimum: int = 1) -> tuple[str, ...]:
        items = tuple(str(o) for o in options)
        if len(items) < minimum:
            raise ValueError(f"needs at least {minimum} option(s)")
        if distinct and len(set(items)) != len(items):
            raise ValueError("options must be distinct")
        return items

    def _ask(self, op: str, prompt: str, items: tuple[str, ...], *,
             data_class_hint: str | None = None) -> SystemOneResult:
        # Classified once over everything a backend would see; the hint can
        # only raise the class (a caller that forces LOCAL_ONLY is believed).
        content_class = data_class.classify("\n".join((prompt, *items)), hint=data_class_hint)
        mode = privacy.mode()
        attempts: list[dict] = []
        for backend in self.backends:
            verdict = data_class.decision(content_class, backend=backend.name, mode=mode)
            if verdict.verdict == "REFUSE":
                attempts.append({"backend": backend.name, "status": "refused",
                                 "error": verdict.reason})
                continue
            sent_prompt, sent_items, redactions = prompt, items, 0
            if verdict.verdict == "REDACT":
                sent_prompt, n = data_class.redact(prompt, content_class)
                redactions = n
                sent_items = []
                for item in items:
                    red, n = data_class.redact(item, content_class)
                    sent_items.append(red)
                    redactions += n
                sent_items = tuple(sent_items)
            try:
                value = _check(op, backend.call(op, sent_prompt, sent_items), sent_items)
            except Exception as exc:  # a backend fault fails over; it is recorded, not hidden
                attempts.append({"backend": backend.name, "status": "failed",
                                 "error": f"{type(exc).__name__}: {exc}"})
                continue
            attempts.append({"backend": backend.name, "status": "ok"})
            return SystemOneResult("ok", value, backend.name, content_class, redactions,
                                   verdict.reason, attempts)
        if attempts and all(a["status"] == "refused" for a in attempts):
            return SystemOneResult("refused", None, "", content_class, 0,
                                   attempts[-1]["error"], attempts)
        return SystemOneResult("failed", None, "", content_class, 0,
                               "no backend produced a valid answer", attempts)
