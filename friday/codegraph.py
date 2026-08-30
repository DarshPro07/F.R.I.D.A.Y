
"""
A structural map of a codebase, built without asking a model anything.

The idea is Graft's: a coding agent that has to discover a repository by
grepping spends most of its context finding out where things are, and most of
that discovery is the same every run. A structural graph - what exists, where
it is defined, and what calls what - answers those questions for a few
kilobytes instead of a few hundred.

The implementation here is deliberately not Graft's. Graft uses tree-sitter,
which is a dependency this machine does not have and which the boss has asked
not to install. Python's own `ast` is better for the language this repository
is actually written in: it is exact where a parser generator is general, it
ships with the interpreter, and it cannot disagree with the compiler about
what a definition is. Other languages get a regex reader that is honest about
being approximate - `Symbol.exact` says which kind of reading produced it, so
nothing downstream has to guess how much to trust it.

What this is for:

    repo_map()          the shape of the project, for orientation
    find(name)          where a symbol is defined
    callers(name)       who uses it - the question grep answers badly
    api_of(path)        what one file offers, without reading the file
    stale()            which files changed since the graph was built

What it is deliberately not: a semantic index, an embedding store, or
anything that costs a token to build. There is no LLM in this module and
there is not meant to be. A summary pass over the graph is a separate,
opt-in, paid thing; the structure underneath it must stay free, because a
graph that costs money to refresh is a graph that goes stale.
"""

from __future__ import annotations

import ast

import json

import logging

import re

import time

from dataclasses import asdict, dataclass, field

from pathlib import Path

logger = logging.getLogger("friday-agent")

#: Never walked. Build output and dependency trees are the bulk of a
#: repository by file count and none of it is the project's own code.
SKIP_DIRS = frozenset(
    {
        '.claude',
        '.eggs',
        '.env',
        '.git',
        '.github',
        '.gradle',
        '.hg',
        '.idea',
        '.mypy_cache',
        '.next',
        '.nuxt',
        '.omc',
        '.pytest_cache',
        '.remember',
        '.ruff_cache',
        '.specify',
        '.svn',
        '.tox',
        '.venv',
        '.vscode',
        '__pycache__',
        'build',
        'coverage',
        'dist',
        'env',
        'htmlcov',
        'node_modules',
        'out',
        'site-packages',
        'target',
        'third_party',
        'vendor',
        'venv',
    },
)

#: Read exactly, with the language's own parser.
PYTHON = {".py", ".pyi"}

#: Read approximately. The pattern per extension finds definitions well
#: enough to orient in, and `exact=False` says not to trust it further.
APPROXIMATE = {
    ".js": r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*(?:export\s+)?class\s+(\w+)",
    ".jsx": r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*(?:export\s+)?class\s+(\w+)",
    ".ts": r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*(?:export\s+)?class\s+(\w+)",
    ".tsx": r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*(?:export\s+)?class\s+(\w+)",
    ".go": r"^func\s+(?:\([^)]*\)\s*)?(\w+)|^type\s+(\w+)\s+struct",
    ".rs": r"^\s*(?:pub\s+)?fn\s+(\w+)|^\s*(?:pub\s+)?struct\s+(\w+)",
    ".java": r"^\s*(?:public|private|protected).*?\s(\w+)\s*\(|^\s*(?:public\s+)?class\s+(\w+)",
    ".rb": r"^\s*def\s+(\w+)|^\s*class\s+(\w+)",
    ".gd": r"^func\s+(\w+)|^class_name\s+(\w+)",
}

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
MODULE_SCOPE = '<module>'

#: A file bigger than this is skipped. A single enormous generated file is
#: not worth the parse and its symbols would swamp the map.
MAX_BYTES = 2 * 1024 * 1024


@dataclass
class Symbol:
    """One definition, and what it refers to."""

    name: str
    kind: str                 # function | class | method
    path: str                 # relative to the graph root, forward slashes
    line: int
    #: Names called in the body. Names, not resolved targets - resolution
    #: needs imports and type inference, and a name is enough to answer
    #: "who calls this" honestly with a caveat rather than wrongly without.
    calls: tuple[str, ...] = ()
    #: Every name mentioned in the body, called or not - a property read,
    #: a decorator, a base class, a getattr string. See `_references_in`.
    references: tuple[str, ...] = ()
    #: Whether a real parser produced this, or a regex guessed it.
    exact: bool = True
    parent: str = ""          # the class, for a method

    @property
    def qualified(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name


class _PythonReader(ast.NodeVisitor):
    """Walks one module and records what it defines and what it calls."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.symbols: list[Symbol] = []
        self._class: list[str] = []

    # -- definitions -------------------------------------------------------

    def _define(self, node, kind: str) -> None:
        if kind == "class":
            # A class body is not a call site the way a function body is:
            # what the class itself calls is its decorators, its bases and
            # the statements at class level. The methods are symbols of
            # their own, and folding their calls into the class would make
            # every class look like it calls everything its methods do.
            calls = set()
            for decorator in node.decorator_list:
                calls |= _calls_in(decorator)
            for base in node.bases:
                calls |= _calls_in(base)
            for statement in node.body:
                if not isinstance(statement, (ast.FunctionDef,
                                              ast.AsyncFunctionDef,
                                              ast.ClassDef)):
                    calls |= _calls_in(statement)
        else:
            calls = _calls_in(node)

        self.symbols.append(Symbol(
            name=node.name,
            kind="method" if (kind == "function" and self._class) else kind,
            path=self.path, line=node.lineno,
            calls=tuple(sorted(calls)),
            references=tuple(sorted(_references_in(node))),
            parent=self._class[-1] if self._class else ""))

    def visit_FunctionDef(self, node) -> None:
        self._define(node, "function")
        # Not generic_visit: a nested function's calls already counted as the
        # outer one's, and recording it separately double-counts every call.
        # Classes inside functions are rare enough to lose.

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node) -> None:
        self._define(node, "class")
        self._class.append(node.name)
        for child in node.body:
            self.visit(child)
        self._class.pop()


def _calls_in(node) -> set[str]:
    """Every name called anywhere inside a definition."""
    found: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        target = child.func
        if isinstance(target, ast.Name):
            found.add(target.id)
        elif isinstance(target, ast.Attribute):
            # `store.remember(...)` records `remember`. The receiver is not
            # resolvable without type inference, and the method name is what
            # a person searching actually types.
            found.add(target.attr)
    return found


def _references_in(node) -> set[str]:
    """
    Every name mentioned anywhere inside a definition, called or not.

    Broader than `_calls_in` on purpose, and the two answer different
    questions. `callers()` wants "who invokes this"; reachability wants "who
    could possibly touch this", and a property read, a decorator, a base
    class, a name passed as a callback and a getattr string are all real ways
    for code to be live.

    Deliberately over-inclusive. For a reachability audit a false "reachable"
    costs an unnecessary look; a false "dead" costs deleted production code -
    which is not hypothetical, it happened to `Reflex.acts` earlier today on
    the strength of `callers()` returning nothing.
    """
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            # A string that is an identifier counts: getattr(obj, "name"),
            # a capability id, a tool name in a table - all of them reach
            # code that no call edge would ever show.
            text = child.value.strip()
            if text.isidentifier():
                found.add(text)
    return found


def read_python(path: Path, relative: str) -> list[Symbol]:
    """Exact, via the language's own parser. Returns nothing on a syntax error."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"),
                         filename=str(path))
    except (SyntaxError, ValueError):
        # A file that does not parse is a fact about the file, not a reason
        # to abandon the graph.
        logger.debug("codegraph: %s does not parse", relative)
        return []
    reader = _PythonReader(relative)
    reader.visit(tree)

    # Import-time work is live by definition. Everything at module level
    # that is not a definition - imports, constants, registrations, the
    # `if __name__` block - is folded into one synthetic symbol carrying
    # its references, so a reachability walk can see what a module
    # touches just by being imported.
    top = [node for node in tree.body
           if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef))]
    if top:
        references = set()
        for node in top:
            references |= _references_in(node)
        reader.symbols.append(Symbol(
            name=MODULE_SCOPE, kind="module", path=relative, line=1,
            references=tuple(sorted(references))))
    return reader.symbols


def read_approximate(path: Path, relative: str, pattern: str) -> list[Symbol]:
    """A regex reading, marked as such."""
    symbols: list[Symbol] = []
    compiled = re.compile(pattern)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for number, line in enumerate(text.splitlines(), start=1):
        match = compiled.match(line)
        if not match:
            continue
        name = next((g for g in match.groups() if g), "")
        if not name:
            continue
        symbols.append(Symbol(name=name, kind="function", path=relative,
                              line=number, exact=False))
    return symbols


@dataclass
class CodeGraph:
    """
    What a repository contains, and how to ask about it.

    Built by walking; queried without walking again. `built_at` and the
    per-file fingerprints are what make `stale()` answerable, which is the
    difference between a map and a map somebody trusts.
    """

    root: str
    symbols: list[Symbol] = field(default_factory=list)
    #: relative path -> (mtime_ns, size). Cheap, and enough to know a file
    #: changed without hashing every byte of the repository.
    fingerprints: dict[str, list] = field(default_factory=dict)
    built_at: float = 0.0
    #: Files seen but not read, and why.
    skipped: dict[str, str] = field(default_factory=dict)

    # -- building ----------------------------------------------------------

    @classmethod
    def build(cls, root: str | Path) -> "CodeGraph":
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            raise NotADirectoryError(f"no such directory: {root_path}")

        graph = cls(root=str(root_path), built_at=time.time())
        started = time.monotonic()
        for path in _walk(root_path):
            relative = path.relative_to(root_path).as_posix()
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > MAX_BYTES:
                graph.skipped[relative] = "too large"
                continue

            suffix = path.suffix.lower()
            if suffix in PYTHON:
                graph.symbols.extend(read_python(path, relative))
            elif suffix in APPROXIMATE:
                graph.symbols.extend(
                    read_approximate(path, relative, APPROXIMATE[suffix]))
            else:
                continue
            graph.fingerprints[relative] = [stat.st_mtime_ns, stat.st_size]

        logger.info("codegraph.built root=%s files=%d symbols=%d in=%.1fs",
                    root_path.name, len(graph.fingerprints),
                    len(graph.symbols), time.monotonic() - started)
        return graph

    def worth_building(self) -> bool:
        """
        Whether there was anything here to map.

        A brand new project has no meaningful source code, and a graph of it
        is an empty file that later reads as "already done". The bootstrap
        checks this rather than running unconditionally.
        """
        return len(self.named()) >= 5

    # -- freshness ---------------------------------------------------------

    def stale(self) -> list[str]:
        """Files that changed, appeared, or vanished since the build."""
        root_path = Path(self.root)
        changed: list[str] = []
        seen: set[str] = set()
        for path in _walk(root_path):
            relative = path.relative_to(root_path).as_posix()
            suffix = path.suffix.lower()
            if suffix not in PYTHON and suffix not in APPROXIMATE:
                continue
            seen.add(relative)
            try:
                stat = path.stat()
            except OSError:
                continue
            known = self.fingerprints.get(relative)
            if known is None or known != [stat.st_mtime_ns, stat.st_size]:
                changed.append(relative)
        changed.extend(sorted(set(self.fingerprints) - seen))
        return sorted(changed)

    def refresh(self) -> int:
        """
        Re-read only what changed. Returns how many files were re-read.

        Incremental because a full rebuild of a large repository on every
        development run is the kind of cost that gets the whole feature
        turned off.
        """
        changed = self.stale()
        if not changed:
            return 0
        root_path = Path(self.root)
        dropped = set(changed)
        self.symbols = [s for s in self.symbols if s.path not in dropped]
        for relative in changed:
            path = root_path / relative
            if not path.is_file():
                self.fingerprints.pop(relative, None)
                continue
            suffix = path.suffix.lower()
            if suffix in PYTHON:
                self.symbols.extend(read_python(path, relative))
            elif suffix in APPROXIMATE:
                self.symbols.extend(
                    read_approximate(path, relative, APPROXIMATE[suffix]))
            try:
                stat = path.stat()
                self.fingerprints[relative] = [stat.st_mtime_ns, stat.st_size]
            except OSError:
                pass
        self.built_at = time.time()
        logger.info("codegraph.refreshed files=%d symbols=%d",
                    len(changed), len(self.symbols))
        return len(changed)

    def named(self) -> list[Symbol]:
        """
        Real definitions only.

        `MODULE_SCOPE` is a synthetic symbol carrying a file's import-time
        references so reachability can see them. It is not a definition and
        has no business in a query somebody reads - it turned up first in
        `api_of`, listed as part of a module's API.
        """
        return [s for s in self.symbols if s.name != MODULE_SCOPE]

    # -- queries -----------------------------------------------------------

    def find(self, name: str, *, limit: int = 20) -> list[Symbol]:
        """Where a symbol is defined. Exact matches first, then contains."""
        needle = (name or "").strip().lower()
        if not needle:
            return []
        named = self.named()
        exact = [s for s in named if s.name.lower() == needle]
        partial = [s for s in named
                   if s.name.lower() != needle and needle in s.name.lower()]
        return (exact + partial)[:limit]

    def mentions(self, name: str, *, limit: int = 60) -> list[Symbol]:
        """
        Definitions that mention `name` at all, however they use it.

        The question to ask before deleting anything. `callers()` is narrower
        and answers "who invokes this"; this one includes the property read,
        the decorator, the base class and the getattr string.
        """
        needle = (name or "").strip()
        if not needle:
            return []
        return [s for s in self.symbols
                if needle in s.references or needle in s.calls][:limit]

    def callers(self, name: str, *, limit: int = 30) -> list[Symbol]:
        """
        Definitions whose body calls `name`.

        By name, not by resolved target: two different `close` methods look
        the same here. That is stated rather than hidden, because the honest
        version of this answer is still far better than grepping for a string
        that also appears in comments and strings.

        **An empty result does not mean unused.** Only calls make edges, so a
        property, a class referenced as a base, a name passed as a callback
        and anything reached by getattr are all invisible here. Do not delete
        code on the strength of this returning nothing - see `Symbol.calls`
        for the time that went wrong.
        """
        needle = (name or "").strip()
        if not needle:
            return []
        return [s for s in self.symbols if needle in s.calls][:limit]

    def api_of(self, path: str, *, include_private: bool = False) -> list[Symbol]:
        """What one file offers, without opening the file."""
        wanted = path.replace("\\", "/").strip("/")
        named = self.named()
        found = [s for s in named if s.path == wanted]
        if not found:                       # tolerate a suffix
            found = [s for s in named if s.path.endswith(wanted)]
        if not include_private:
            found = [s for s in found if not s.name.startswith("_")]
        return sorted(found, key=lambda s: s.line)

    def repo_map(self, *, limit: int = 40) -> dict:
        """
        The shape of the project: the files with the most in them.

        Deliberately a summary. The whole graph does not go into a context
        window - that would reproduce the problem it exists to solve.
        """
        named = self.named()
        by_file: dict[str, int] = {}
        for symbol in named:
            by_file[symbol.path] = by_file.get(symbol.path, 0) + 1
        biggest = sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        return {
            "root": self.root,
            "files": len(self.fingerprints),
            "symbols": len(named),
            "classes": sum(1 for s in named if s.kind == "class"),
            "approximate": sum(1 for s in named if not s.exact),
            "largest": [{"path": p, "symbols": n} for p, n in biggest],
        }

    # -- persistence -------------------------------------------------------

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "root": self.root,
            "built_at": self.built_at,
            "fingerprints": self.fingerprints,
            "skipped": self.skipped,
            "symbols": [asdict(s) for s in self.symbols],
        }), encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: str | Path) -> "CodeGraph | None":
        source = Path(path)
        if not source.is_file():
            return None
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt graph is rebuilt, not raised. It is a cache.
            logger.warning("codegraph: %s is unreadable; rebuilding", source)
            return None
        graph = cls(root=raw.get("root", ""), built_at=raw.get("built_at", 0.0),
                    fingerprints=raw.get("fingerprints", {}),
                    skipped=raw.get("skipped", {}))
        for row in raw.get("symbols", []):
            row["calls"] = tuple(row.get("calls", ()))
            row["references"] = tuple(row.get("references", ()))
            graph.symbols.append(Symbol(**row))
        return graph


def _walk(root: Path):
    """Every file under root, skipping the directories nobody means."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and not entry.is_symlink():
                    stack.append(entry)
            elif entry.is_file():
                yield entry


def graph_path(project_root: str | Path) -> Path:
    """
    The cache file for one project.

    Kept in Friday's own data directory rather than in the project. The graph
    is derived, machine-local and regenerated on demand, so committing it
    would mean every branch carrying a stale copy of a thing that rebuilds in
    under a second.
    """
    from friday.config import DATA_DIR

    resolved = Path(project_root).resolve()
    stamp = f"{resolved.name}-{abs(hash(str(resolved))) % (10 ** 10)}"
    return Path(DATA_DIR) / "codegraph" / f"{stamp}.json"


def ensure(project_root: str | Path, *, refresh: bool = True) -> CodeGraph | None:
    """
    The project's graph, built or refreshed as needed.

    Returns None when there is nothing worth mapping yet - a new project with
    a README and no code. Running against an empty repository and calling the
    result a graph is how a bootstrap step becomes a lie: it succeeds, writes
    nothing useful, and every later check sees "already built".
    """
    cache = graph_path(project_root)
    graph = CodeGraph.load(cache)

    if graph is None:
        graph = CodeGraph.build(project_root)
        if not graph.worth_building():
            logger.info("codegraph: %s has nothing to map yet", project_root)
            return None
        graph.save(cache)
        return graph

    if refresh and graph.refresh():
        graph.save(cache)
    return graph


# ---------------------------------------------------------------------------
# Where a project's graph lives
# ---------------------------------------------------------------------------
