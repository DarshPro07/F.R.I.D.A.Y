"""Render a small code map for a worker packet: what the cited files define.

    python scripts/code_capsule.py FILE... [--budget N]

FILE may be a ledger citation (`path:10-40`; everything from the first `:`
is dropped). Per .py file the capsule lists the module docstring's first
line, classes and top-level functions with line numbers and signatures, the
tests that import it directly and the modules that import it. The graph comes
from scripts/test_impact.py. Tokens are estimated as len(text)/4; over budget
the detail is shed in a fixed order (private members, method lines, docstring
lines, then member lists are truncated). Headers, tests and importers stay.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_impact  # noqa: E402

ROOT = test_impact.ROOT
MAX_IMPORTERS = 5
MAX_DOC = 100
MAX_SIG = 120
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass
class Member:
    name: str
    line: int
    text: str
    method: bool = False
    parent: str = ""

    @property
    def private(self) -> bool:
        return self.name.startswith("_") or self.parent.startswith("_")


@dataclass
class Entry:
    header: str
    doc: str = ""
    members: list[Member] = field(default_factory=list)
    tail: list[str] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def normalize(citation: str) -> str:
    """`scripts\\x.py:10-40` -> `scripts/x.py` (a drive colon is not a line range)."""
    skip = 2 if _DRIVE.match(citation) else 0
    cut = citation.find(":", skip)
    path = citation if cut < 0 else citation[:cut]
    return path.strip().replace("\\", "/")


def _relative(path: str, root: Path) -> str:
    if _DRIVE.match(path) or path.startswith("/"):
        try:
            return Path(path).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path
    return path


def _sig(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        text = f"class {node.name}({bases})" if bases else f"class {node.name}"
    else:
        kw = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        text = f"{kw} {node.name}({ast.unparse(node.args)}){ret}"
    return text if len(text) <= MAX_SIG else text[: MAX_SIG - 3] + "..."


def _members(tree: ast.Module) -> list[Member]:
    out: list[Member] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(Member(node.name, node.lineno, _sig(node)))
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out.append(Member(sub.name, sub.lineno, _sig(sub), True, node.name))
    return out


def _doc_line(tree: ast.Module) -> str:
    doc = ast.get_docstring(tree)
    first = doc.strip().splitlines()[0].strip() if doc and doc.strip() else ""
    return first[:MAX_DOC]


def build_entry(rel: str, source: str, graph: dict[str, set[str]]) -> Entry:
    header = f"## {rel} ({len(source.splitlines())} lines)"
    if not rel.endswith(".py"):
        return Entry(header)
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return Entry(f"{header} (unparseable)")
    tests = [t for t in test_impact.affected_tests({rel}, graph, direct=True) if t != rel]
    users = sorted(r for r, deps in graph.items() if rel in deps and not test_impact._is_test(r))
    shown = ", ".join(users[:MAX_IMPORTERS]) or "(none)"
    if len(users) > MAX_IMPORTERS:
        shown += f" (+{len(users) - MAX_IMPORTERS})"
    tail = [f"tests: {', '.join(tests) or '(none)'}", f"imported by: {shown}"]
    return Entry(header, _doc_line(tree), _members(tree), tail)


def render_entry(e: Entry, level: int, keep: int | None) -> list[str]:
    """level 0 all; 1 no private; 2 also no methods; 3 also no docstring; keep caps member lines."""
    lines = [e.header]
    if e.doc and level < 3:
        lines.append(f"  {e.doc}")
    members = [m for m in e.members if not (level >= 1 and m.private) and not (level >= 2 and m.method)]
    dropped = 0
    if keep is not None and len(members) > keep:
        dropped, members = len(members) - keep, members[:keep]
    for m in members:
        lines.append(f"{'    ' if m.method else '  '}L{m.line} {m.text}")
    if dropped:
        lines.append(f"  (+{dropped} more)")
    return [*lines, *e.tail]


def _render(entries: list[Entry], level: int, keep: int | None) -> str:
    return "\n".join(line for e in entries for line in render_entry(e, level, keep))


def fit(entries: list[Entry], budget_tokens: int) -> str:
    """Shed detail in the documented order until the estimate fits the budget."""
    for level in range(4):
        text = _render(entries, level, None)
        if estimate_tokens(text) <= budget_tokens:
            return text
    widest = max((len([m for m in e.members if not m.private and not m.method]) for e in entries), default=0)
    for keep in range(widest, -1, -1):
        text = _render(entries, 3, keep)
        if estimate_tokens(text) <= budget_tokens:
            return text
    return text


def capsule(files: list[str], root: Path = ROOT, budget_tokens: int = 1000,
            sources: dict[str, str] | None = None) -> str:
    sources = test_impact.collect_sources(root) if sources is None else sources
    graph = test_impact.build_graph(sources)
    entries: list[Entry] = []
    seen: set[str] = set()
    for citation in files:
        rel = _relative(normalize(citation), root)
        if not rel or rel in seen:
            continue
        seen.add(rel)
        source = sources.get(rel)
        if source is None:
            path = root / rel
            if not path.is_file():
                entries.append(Entry(f"## {rel} (missing)"))
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
        entries.append(build_entry(rel, source, graph))
    return fit(entries, budget_tokens)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", help="paths or ledger citations like path:10-40")
    ap.add_argument("--budget", type=int, default=1000, help="estimated token budget")
    args = ap.parse_args(argv)
    print(capsule(args.files, budget_tokens=args.budget))
    return 0


if __name__ == "__main__":
    sys.exit(main())
