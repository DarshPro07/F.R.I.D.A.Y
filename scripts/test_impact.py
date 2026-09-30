"""Select the test files affected by a change, from a static import graph.

    python scripts/test_impact.py [--base REF] [--direct] [--files a.py b.py]

Changed files are `git diff --name-only REF` (default HEAD) plus untracked
`.py` files, or the explicit --files. A test is affected if it changed, or
imports a changed module directly (--direct) or transitively (default). A
test that mentions `<script>.py` as a string also counts as importing that
script (tests load scripts with importlib by path). A change to
tests/conftest.py selects every test.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def module_name(rel: str) -> str:
    """`friday/x/y.py` -> `friday.x.y`; scripts, tests and root files by stem."""
    parts = rel[:-3].split("/") if rel.endswith(".py") else rel.split("/")
    if parts[0] == "friday":
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)
    return parts[-1]


def _imported_names(source: str, own_module: str, is_package: bool) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    pkg = own_module if is_package else own_module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = pkg.split(".") if pkg else []
                anchor = anchor[: len(anchor) - (node.level - 1)] if node.level > 1 else anchor
                base = ".".join(anchor + ([base] if base else []))
            if base:
                names.add(base)
            for a in node.names:
                names.add(f"{base}.{a.name}" if base else a.name)
    return names


def build_graph(sources: dict[str, str]) -> dict[str, set[str]]:
    """Map each rel path to the set of rel paths (in `sources`) it imports."""
    by_module = {module_name(rel): rel for rel in sources}
    scripts = {rel: Path(rel).name for rel in sources if rel.startswith("scripts/")}
    graph: dict[str, set[str]] = {}
    for rel, src in sources.items():
        own = module_name(rel)
        is_pkg = rel.endswith("__init__.py")
        deps = {by_module[n] for n in _imported_names(src, own, is_pkg) if n in by_module}
        for s_rel, fname in scripts.items():
            if s_rel != rel and fname in src:
                deps.add(s_rel)
        deps.discard(rel)
        graph[rel] = deps
    return graph


def _is_test(rel: str) -> bool:
    p = Path(rel)
    return rel.startswith("tests/") and p.name.startswith("test_") and p.suffix == ".py"


def affected_tests(changed: set[str], graph: dict[str, set[str]], direct: bool = False) -> list[str]:
    """Test files that changed or depend (directly or transitively) on a change."""
    if "tests/conftest.py" in changed:
        return sorted(r for r in graph if _is_test(r))
    hit = {c for c in changed if c in graph}
    if direct:
        reach = set(hit)
        reach.update(r for r, deps in graph.items() if deps & hit)
    else:
        reverse: dict[str, set[str]] = {}
        for r, deps in graph.items():
            for d in deps:
                reverse.setdefault(d, set()).add(r)
        reach, todo = set(hit), list(hit)
        while todo:
            for parent in reverse.get(todo.pop(), ()):
                if parent not in reach:
                    reach.add(parent)
                    todo.append(parent)
    return sorted(r for r in reach if _is_test(r))


def collect_sources(root: Path) -> dict[str, str]:
    files = [*root.glob("friday/**/*.py"), *root.glob("scripts/*.py"),
             *root.glob("tests/*.py"), *root.glob("*.py")]
    out: dict[str, str] = {}
    for f in files:
        rel = f.relative_to(root).as_posix()
        out[rel] = f.read_text(encoding="utf-8", errors="replace")
    return out


def _git_lines(args: list[str], root: Path) -> list[str]:
    res = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return [ln.strip().replace("\\", "/") for ln in res.stdout.splitlines() if ln.strip()]


def changed_files(base: str, root: Path) -> set[str]:
    changed = set(_git_lines(["diff", "--name-only", base], root))
    changed.update(_git_lines(["ls-files", "--others", "--exclude-standard"], root))
    return {c for c in changed if c.endswith(".py")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--direct", action="store_true", help="direct importers only")
    ap.add_argument("--files", nargs="+", help="explicit changed files (skip git)")
    args = ap.parse_args(argv)
    changed = {f.replace("\\", "/") for f in args.files} if args.files else changed_files(args.base, ROOT)
    graph = build_graph(collect_sources(ROOT))
    tests = affected_tests(changed, graph, direct=args.direct)
    print(f"changed: {len(changed)} file(s); affected tests: {len(tests)}")
    for t in tests:
        print(f"  {t}")
    print("pytest command: " + " ".join(["pytest", *tests]) if tests else "pytest command: (none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
