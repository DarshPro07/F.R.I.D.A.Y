"""
The other side of the process boundary: where a forged skill actually runs.

Executed as `python -I -m friday.forge_runtime <request.json>` with an
environment built from nothing, so this process holds none of Friday's
credentials. It loads one skill, gives it a CapabilityContext, runs the
spec's declared cases, and prints one line of JSON.

Two layers stop a skill from being arbitrary Python, and they are independent
on purpose - the static gate reads the code, this enforces at runtime, and
neither relies on the other having been thorough:

    restricted builtins   `open`, `eval`, `exec`, `compile` and `__import__`
                          are not in the namespace the skill executes in. A
                          name that is absent cannot be misspelled past a
                          reviewer.
    an import allow-list  the only `__import__` available consults the spec.
                          The static gate checks the same list; this enforces
                          it while the code runs.

Neither is a sandbox, and this module does not pretend otherwise. Restricted
builtins are well known to be escapable in CPython by walking from any object
to `object.__subclasses__`, which is exactly why those attribute names are
refused by the static gate rather than only discouraged here. Two layers that
must both fail is meaningfully better than one; it is still not a privilege
boundary, and a skill that wants one has to wait for a real OS-level sandbox.
"""

from __future__ import annotations

import builtins
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# The surface a skill is given
# ---------------------------------------------------------------------------

#: Builtins a skill may use. Everything absent from here is absent from the
#: skill's namespace entirely, rather than present and discouraged.
SAFE_BUILTINS = (
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
    "callable", "chr", "complex", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "getattr", "hasattr", "hash", "hex", "id",
    "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max",
    "min", "next", "object", "oct", "ord", "pow", "print", "range", "repr",
    "reversed", "round", "set", "setattr", "slice", "sorted", "str", "sum",
    "tuple", "type", "zip",
    # Exceptions: a skill must be able to raise and catch.
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "RuntimeError", "ArithmeticError", "ZeroDivisionError", "StopIteration",
    "AttributeError", "NotImplementedError", "AssertionError", "True",
)


class CapabilityDenied(RuntimeError):
    """The skill asked for something its specification does not permit."""


class CapabilityContext:
    """
    What a skill talks to instead of talking to the machine.

    Every method is a request Friday may refuse. The skill does not open
    files, it asks; it does not make requests, it asks. That indirection is
    the actual boundary in this version - not the process, and certainly not
    a virtual environment.
    """

    def __init__(self, spec: dict) -> None:
        self._spec = spec
        self._scope = [Path(p).resolve() for p in spec.get("scope_paths") or []]
        self._hosts = tuple(spec.get("allowed_hosts") or ())
        self.logs: list[str] = []
        self.artifacts: list[dict] = []
        self.calls: list[str] = []

    # -- observation -------------------------------------------------------

    def log(self, message: str) -> None:
        self.logs.append(str(message)[:500])

    def progress(self, message: str) -> None:
        self.log(f"progress: {message}")

    # -- the world ---------------------------------------------------------

    def read_text(self, path: str, *, max_chars: int = 200_000) -> str:
        """Read a file, if the spec scoped the filesystem and this is inside it."""
        self.calls.append("read_text")
        if self._spec.get("filesystem") != "scoped":
            raise CapabilityDenied(
                "this skill's specification denies filesystem access")
        target = Path(path).expanduser().resolve()
        if not any(target == root or target.is_relative_to(root)
                   for root in self._scope):
            raise CapabilityDenied(
                f"{target.name!r} is outside the paths this skill was given")
        return target.read_text(encoding="utf-8", errors="replace")[:max_chars]

    def fetch(self, url: str, *, timeout: float = 10.0) -> str:
        """Fetch a URL, if the spec scoped the network and named this host."""
        self.calls.append("fetch")
        if self._spec.get("network") != "scoped":
            raise CapabilityDenied(
                "this skill's specification denies network access")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise CapabilityDenied(f"{parts.scheme!r} is not an allowed scheme")
        if parts.hostname not in self._hosts:
            raise CapabilityDenied(
                f"{parts.hostname!r} is not one of this skill's allowed hosts")
        import httpx  # noqa: PLC0415 - deliberately not in the skill's namespace

        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        return response.text

    def emit(self, name: str, content: str) -> None:
        """Hand something back. The caller decides where it lands, not the skill."""
        self.calls.append("emit")
        self.artifacts.append({"name": str(name)[:120], "chars": len(content),
                               "content": content[:100_000]})


# ---------------------------------------------------------------------------
# Loading a skill without giving it the interpreter
# ---------------------------------------------------------------------------

STDLIB_SAFE_FALLBACK = frozenset()


def _guarded_import(allowed: frozenset[str]):
    """The only __import__ a skill gets. It consults the spec, every time."""

    from friday.forge import NEVER_IMPORT

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            raise CapabilityDenied("relative imports are not permitted")
        root = name.split(".")[0]
        # NEVER_IMPORT first, and independently of the allow-list: a spec that
        # declared `os` as one of its dependencies would otherwise be stopped
        # only by the static gate, and the whole point of two layers is that
        # neither assumes the other ran.
        if root in NEVER_IMPORT:
            raise CapabilityDenied(
                f"{name!r} is refused outright, whatever the spec declares")
        if root not in allowed and name not in allowed:
            raise CapabilityDenied(
                f"{name!r} is not in this skill's allowed imports "
                f"({', '.join(sorted(allowed)) or 'none'})")
        return builtins.__import__(name, globals, locals, fromlist, level)

    return guarded


def load(source: str, allowed_imports: frozenset[str]):
    """Execute the skill's source in a namespace it cannot climb out of."""
    safe = {name: getattr(builtins, name)
            for name in SAFE_BUILTINS if hasattr(builtins, name)}
    safe["__import__"] = _guarded_import(allowed_imports)
    namespace: dict = {"__builtins__": safe, "__name__": "forged_skill"}
    # Executing generated code is this module's entire job - there is no
    # version of "run the skill" that does not run the skill. What makes it
    # defensible is everything around this line: the code has already passed
    # the static gate, this process holds no credentials, and `namespace`
    # gives it neither `open` nor an unrestricted `__import__`. What it is
    # NOT is a privilege boundary; see the module docstring.
    exec(compile(source, "<forged>", "exec"), namespace)  # noqa: S102
    entry = namespace.get("run")
    if not callable(entry):
        raise CapabilityDenied("the skill has no callable `run`")
    return entry


# ---------------------------------------------------------------------------
# Running the declared cases
# ---------------------------------------------------------------------------


def _matches(got, expected) -> bool:
    """
    Compare an outcome to what the spec said it should be.

    A declared expectation is a *subset* check on mappings, so a spec can
    assert the fields it cares about without having to restate the whole
    output. Anything else is equality.
    """
    if isinstance(expected, dict):
        if not isinstance(got, dict):
            return False
        return all(key in got and _matches(got[key], value)
                   for key, value in expected.items())
    if isinstance(expected, list):
        return (isinstance(got, list) and len(got) == len(expected)
                and all(_matches(g, e) for g, e in zip(got, expected)))
    return got == expected


def main(argv: list[str]) -> int:
    request = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    spec = request["spec"]
    source = Path(request["skill"]).read_text(encoding="utf-8")

    from friday.forge import STDLIB_SAFE

    allowed = frozenset(STDLIB_SAFE) | {d.split(".")[0] for d in
                                        (spec.get("dependencies") or ())}

    cases: list[dict] = []
    findings: list[str] = []
    try:
        entry = load(source, allowed)
    except Exception as exc:
        findings.append(f"the skill could not be loaded: {type(exc).__name__}: {exc}")
        print(json.dumps({"cases": [], "findings": findings}))
        return 1

    budget = float(spec.get("budget_seconds") or 20.0)
    for index, case in enumerate(spec.get("verification") or ()):
        ctx = CapabilityContext({**spec, **(case.get("context") or {})})
        started = time.monotonic()
        record = {"case": index, "inputs": case.get("inputs") or {}}
        try:
            got = entry(ctx, **(case.get("inputs") or {}))
        except Exception as exc:
            record.update(passed=False,
                          error=f"{type(exc).__name__}: {exc}")
        else:
            expected = case.get("expect")
            ok = _matches(got, expected)
            record.update(passed=ok, got=_safe(got))
            if not ok:
                record["expected"] = expected
        took = time.monotonic() - started
        record["took_ms"] = int(took * 1000)
        record["context_calls"] = ctx.calls
        if took > budget:
            record["passed"] = False
            record["error"] = f"took {took:.1f}s, over its {budget}s budget"
        cases.append(record)

    print(json.dumps({"cases": cases, "findings": findings}))
    return 0 if all(case.get("passed") for case in cases) else 1


def _safe(value):
    """Whatever the skill returned, made printable without trusting it."""
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)[:500]
    return value


if __name__ == "__main__":
    sys.exit(main(sys.argv))
