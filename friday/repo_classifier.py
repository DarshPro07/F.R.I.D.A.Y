"""
Repository classifier: what is this repository, before anyone integrates it.

CAP-FORGE-002. Two pure, deterministic stages and nothing else:

    build_dossier(path)  ->  Dossier         what is in the tree
    classify(dossier)    ->  Classification  which of ten classes it is, and why

No model is involved, no network, and nothing in the tree is ever imported,
executed or installed. Manifests are parsed as data (json, tomllib,
configparser) and `setup.py` is read as text with a regex, never run. That
matters because the input is an untrusted third-party checkout: reading it is
the only thing this module is allowed to do with it. Fetching a repository and
analysing it inside a sandbox is CAP-FORGE-003; mapping a class to an
integration mode and a go/no-go is CAP-FORGE-004 and 014. This slice only
answers "what is it".

The reads are bounded so a hostile tree costs a fixed amount: symlinks and
junctions are not followed, directories deeper than MAX_DEPTH and trees past
MAX_FILES are cut off, files over MAX_FILE_BYTES and binary files are not read,
and every cut is recorded in `Dossier.notes` instead of being dropped silently.

The one rule that is not a heuristic: a prose-dominant tree with no resolvable
entry point and no skill, MCP or model signal is a research corpus, whatever
manifests happen to sit in it. A prompt corpus with a stray `setup.py` or a
`package.json` scripts block is therefore never classified as something that
would be run (see `classify`). The cost is deliberate: a documentation-heavy
library with no entry point can land there too, and a wrong answer in the
non-executable direction is the safe one.
"""

from __future__ import annotations

import configparser
import json
import os
import posixpath
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# The ten classes (the PRD's vocabulary). These say what a repository IS; how
# Friday would run it once registered is fabric.INTEGRATION_MODES, a different
# question answered later.
# ---------------------------------------------------------------------------

SKILL = "skill"
MCP = "mcp"
CLI = "cli"
LIBRARY = "library"
APPLICATION = "application"
SERVICE = "service"
MODEL = "model"
RESEARCH_CORPUS = "research_corpus"
CAPABILITY_PACK = "capability_pack"
REFERENCE_ONLY = "reference_only"
CLASSES = (SKILL, MCP, CLI, LIBRARY, APPLICATION, SERVICE, MODEL,
           RESEARCH_CORPUS, CAPABILITY_PACK, REFERENCE_ONLY)

#: Classes that put code in motion if integrated. A skill is a prompt/recipe, a
#: model is weights, a corpus is text, a pack is a bundle whose members are
#: classified on their own, and reference-only is never executed.
EXECUTABLE_CLASSES = frozenset({MCP, CLI, LIBRARY, APPLICATION, SERVICE})

#: Licence text -> label. The same table as scripts/upstream_lock.py
#: SIGNATURES (ordered: the AGPL preamble also contains "GNU GENERAL PUBLIC
#: LICENSE", so the specific pattern must win). It is held here because the lock
#: script is built to run on a tree where `friday` cannot be imported, so it
#: cannot depend on this module; tests/test_repo_classifier.py fails if the two
#: drift apart.
LICENSE_SIGNATURES = (
    ("AGPL-3.0", r"GNU AFFERO GENERAL PUBLIC LICENSE"),
    ("GPL-3.0", r"GNU GENERAL PUBLIC LICENSE"),
    ("Apache-2.0", r"Apache License"),
    ("BSD-3-Clause", r"BSD 3-Clause"),
    ("BSD-2-Clause", r"BSD 2-Clause"),
    ("PolyForm", r"PolyForm"),
    ("MPL-2.0", r"Mozilla Public License"),
    # Last: the MIT grant text, which several other licenses do not contain.
    ("MIT", r"Permission is hereby granted, free of charge"),
)
NO_LICENSE = "NONE"
UNIDENTIFIED_LICENSE = "UNIDENTIFIED"

# ---------------------------------------------------------------------------
# Read bounds. A hostile tree costs at most this much, however it is shaped.
# ---------------------------------------------------------------------------

MAX_DEPTH = 8                 # directory levels below the root
MAX_FILES = 5000
MAX_DIRS = 2000
MAX_FILE_BYTES = 1_000_000    # a larger file is counted, never read
READ_BYTES = 65_536           # source files: only the head is scanned
MAX_CONTENT_SCANS = 300       # source files scanned for permission/server hints
MAX_SKILL_FILES = 200         # SKILL.md files read
MAX_MANIFESTS = 60
MANIFEST_DEPTH = 2            # manifests deeper than this are listed, not parsed
MAX_LISTED = 40               # cap on any one list in the dossier

#: A tree is prose-dominant at or above this share of prose/data files among
#: prose + code files (about 2.3 prose files per code file), and it needs a few
#: files to count as a corpus at all. Corpora in the wild carry a handful of
#: helper scripts and a stray manifest; a lower bar keeps them non-executable.
PROSE_DOMINANT = 0.7
MIN_CORPUS_FILES = 3

IGNORED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv",
                          ".mypy_cache", ".pytest_cache", ".tox"})

LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".go": "go", ".rs": "rust", ".java": "java",
    ".kt": "kotlin", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
    ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".sh": "shell", ".bash": "shell", ".ps1": "powershell",
    ".bat": "batch", ".html": "html", ".css": "css",
}
#: Prose and plain data. Counted against code for the prose ratio; never
#: scanned for entry points, so a shell snippet in markdown is only text.
PROSE_SUFFIXES = frozenset({
    ".md", ".markdown", ".mdx", ".txt", ".rst", ".prompt", ".jsonl", ".json",
    ".yaml", ".yml", ".toml", ".csv", ".tsv",
})
WEIGHT_SUFFIXES = frozenset({
    ".safetensors", ".gguf", ".onnx", ".pt", ".pth", ".ckpt", ".h5",
    ".tflite", ".mlmodel", ".pb",
})
#: Weight formats that are pickles: loading one can run code.
PICKLE_SUFFIXES = frozenset({".pt", ".pth", ".ckpt"})

BUILD_SYSTEM_BY_MANIFEST = {
    "pyproject.toml": "python-pyproject", "setup.py": "python-setuptools",
    "setup.cfg": "python-setuptools", "package.json": "npm",
    "cargo.toml": "cargo", "go.mod": "go", "pom.xml": "maven",
    "build.gradle": "gradle", "build.gradle.kts": "gradle",
    "gemfile": "bundler", "composer.json": "composer",
    "cmakelists.txt": "cmake", "makefile": "make", "dockerfile": "docker",
    "pubspec.yaml": "pub",
}
#: Build systems that mean "something importable is built here".
LIBRARY_BUILD_SYSTEMS = frozenset({
    "python-pyproject", "python-setuptools", "npm", "cargo", "go", "maven",
    "gradle", "bundler", "composer", "cmake",
})
MANIFEST_NAMES = frozenset(BUILD_SYSTEM_BY_MANIFEST) | {
    "requirements.txt", "procfile", "docker-compose.yml",
    "docker-compose.yaml", "compose.yml", "compose.yaml",
}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


MCP_DEPENDENCIES = frozenset(_norm(n) for n in (
    "mcp", "fastmcp", "@modelcontextprotocol/sdk", "mcp-framework",
    "mark3labs/mcp-go"))
SERVICE_FRAMEWORKS = frozenset(_norm(n) for n in (
    "fastapi", "flask", "django", "uvicorn", "gunicorn", "starlette", "sanic",
    "tornado", "express", "koa", "fastify", "@nestjs/core", "actix-web",
    "axum", "rocket", "gin-gonic/gin", "labstack/echo", "gofiber/fiber"))
GUI_FRAMEWORKS = frozenset(_norm(n) for n in (
    "electron", "tauri", "@tauri-apps/api", "pyqt5", "pyqt6", "pyside2",
    "pyside6", "kivy", "wxpython", "flet", "react-native", "expo",
    "customtkinter", "pygame"))
FRONTEND_FRAMEWORKS = frozenset(_norm(n) for n in (
    "react", "vue", "svelte", "next", "nuxt", "@angular/core", "vite",
    "solid-js", "preact"))

#: Hints from reading source. They say "this code can", not "this code does".
PERMISSION_PATTERNS = (
    ("network", re.compile(
        r"\b(requests|httpx|aiohttp|urllib|http\.client|socket|websockets|curl|wget)\b"
        r"|\bfetch\(|\baxios\b|net/http|reqwest")),
    ("subprocess", re.compile(
        r"\bsubprocess\b|os\.system\(|os\.popen\(|\bchild_process\b"
        r"|exec\.Command|std::process::Command|\bPopen\b")),
    ("filesystem", re.compile(
        r"shutil\.rmtree|os\.(remove|unlink|rmdir)\(|\.unlink\("
        r"|fs\.(writeFile|unlink|rm)\b|os\.RemoveAll|remove_dir_all"
        r"|open\([^)\n]*,\s*[\"'][wa]")),
)
MCP_SERVER_CODE = re.compile(
    r"FastMCP\(|\bmcp\.server\b|modelcontextprotocol/sdk|McpServer\b"
    r"|mark3labs/mcp-go|\brmcp\b")
SERVICE_CONSTRUCT = re.compile(
    r"FastAPI\(|Flask\(|\bexpress\(\)|uvicorn\.run|\.listen\(\s*\d"
    r"|ListenAndServe|serve_forever|web\.run_app|HttpServer::new")
LIFECYCLE_SCRIPTS = frozenset({"preinstall", "install", "postinstall",
                               "prepare", "prepack"})
MODEL_CARD_KEYS = frozenset({"pipeline_tag", "library_name", "base_model",
                             "model-index"})

_SCRIPT_BLOCK = re.compile(r"(?:console|gui)_scripts[\"']?\s*:\s*\[(.*?)\]", re.S)
_SCRIPT_ITEM = re.compile(r"[\"']\s*([\w.-]+)\s*=\s*([\w.]+(?::[\w.]+)?)")


class ClassifierError(ValueError):
    """The path given to `build_dossier` cannot be classified at all."""


@dataclass(frozen=True)
class Dossier:
    """
    Everything the classifier knows about a tree. Frozen, built only from
    reads, and free of absolute paths and clocks so the same tree gives the
    same dossier wherever it sits.

    `license` is a label from LICENSE_SIGNATURES, a manifest-declared string,
    `NONE` (nothing found) or `UNIDENTIFIED` (a licence file nobody here can
    recognise); `license_source` says where it came from.
    """
    name: str
    license: str
    license_source: str
    languages: tuple[tuple[str, int], ...]
    build_systems: tuple[str, ...]
    #: Resolved entry points as "kind:name->target". An entry a manifest
    #: declares but the tree does not contain is not listed (it is a note).
    entry_points: tuple[str, ...]
    manifests: tuple[str, ...]
    mcp_signals: tuple[str, ...]          # config:, dependency:, server_code:
    skill_signals: tuple[str, ...]        # skill:, plugin:
    model_signals: tuple[str, ...]        # weights:, model_card:, hf_config:
    service_signals: tuple[str, ...]
    app_signals: tuple[str, ...]          # gui:, frontend:, mobile:
    skill_count: int
    mcp_config_servers: int
    weight_files: int
    permissions: tuple[str, ...]
    file_count: int
    code_files: int
    prose_files: int
    prose_ratio: float
    truncated: bool
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Classification:
    """
    `repo_class` is one of CLASSES. `structural_class` is what the tree is
    before the licence gate; it differs from `repo_class` only when an
    unlicensed tree is held at REFERENCE_ONLY.
    """
    repo_class: str
    confidence: float
    reason: str
    evidence: tuple[str, ...]
    structural_class: str

    @property
    def executable(self) -> bool:
        return is_executable(self.repo_class)


def is_executable(repo_class: str) -> bool:
    return repo_class in EXECUTABLE_CLASSES


# ---------------------------------------------------------------------------
# Stage 1: the bounded read.
# ---------------------------------------------------------------------------

class _File(NamedTuple):
    rel: str
    size: int
    path: str

    @property
    def name(self) -> str:
        return self.rel.rsplit("/", 1)[-1]

    @property
    def suffix(self) -> str:
        return posixpath.splitext(self.name)[1].lower()

    @property
    def depth(self) -> int:
        return self.rel.count("/")


def _is_link(entry: os.DirEntry) -> bool:
    """A symlink or, on Windows, any reparse point (junctions included)."""
    if entry.is_symlink():
        return True
    attributes = getattr(entry.stat(follow_symlinks=False),
                         "st_file_attributes", 0)
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


class _Tree:
    """The files of one checkout, listed and read within the bounds above."""

    def __init__(self, root: Path) -> None:
        self.real_root = Path(os.path.realpath(root))
        self.files: list[_File] = []
        self.notes: set[str] = set()
        self.truncated = False
        self._dirs = 0
        self._walk(str(root), "", 0)

    def _walk(self, directory: str, rel: str, depth: int) -> None:
        self._dirs += 1
        if self._dirs > MAX_DIRS:
            self.truncated = True
            self.notes.add(f"directory cap ({MAX_DIRS}) reached")
            return
        try:
            with os.scandir(directory) as handle:
                entries = sorted(handle, key=lambda entry: entry.name)
        except OSError as error:
            self.notes.add(f"unreadable directory: {rel or '.'} ({type(error).__name__})")
            return
        for entry in entries:
            if self.truncated:
                return
            child = f"{rel}/{entry.name}" if rel else entry.name
            try:
                if _is_link(entry):
                    self.notes.add(f"link not followed: {child}")
                elif entry.is_dir(follow_symlinks=False):
                    if entry.name in IGNORED_DIRS:
                        continue
                    if depth >= MAX_DEPTH:
                        self.notes.add(f"depth cap ({MAX_DEPTH}): not entered {child}")
                        continue
                    self._walk(entry.path, child, depth + 1)
                elif entry.is_file(follow_symlinks=False):
                    if len(self.files) >= MAX_FILES:
                        self.truncated = True
                        self.notes.add(f"file cap ({MAX_FILES}) reached")
                        return
                    size = entry.stat(follow_symlinks=False).st_size
                    self.files.append(_File(child, size, entry.path))
                # Sockets, devices and fifos are never opened: a fifo read blocks.
            except OSError as error:
                self.notes.add(f"unreadable entry: {child} ({type(error).__name__})")

    def read(self, file: _File, limit: int = READ_BYTES) -> str | None:
        """
        The head of a text file, or None when it is oversized, binary, outside
        the tree (a link swapped in after the listing) or unreadable. The
        reason is recorded either way.
        """
        if file.size > MAX_FILE_BYTES:
            self.notes.add(f"oversized, not read: {file.rel}")
            return None
        try:
            if not Path(os.path.realpath(file.path)).is_relative_to(self.real_root):
                self.notes.add(f"outside the tree, not read: {file.rel}")
                return None
            with open(file.path, "rb") as handle:
                chunk = handle.read(limit)
        except OSError as error:
            self.notes.add(f"unreadable: {file.rel} ({type(error).__name__})")
            return None
        if b"\0" in chunk:
            self.notes.add(f"binary, not read: {file.rel}")
            return None
        return chunk.decode("utf-8", errors="replace")


class _Facts:
    """What the readers found, before it is frozen into a Dossier."""

    def __init__(self, tree: _Tree) -> None:
        self.tree = tree
        self.rels = frozenset(f.rel for f in tree.files)
        self.build_systems: set[str] = set()
        self.manifests: set[str] = set()
        self.entries: set[str] = set()
        self.deps: set[str] = set()
        self.licenses: list[tuple[str, str]] = []
        self.mcp: set[str] = set()
        self.skills: set[str] = set()
        self.model: set[str] = set()
        self.service: set[str] = set()
        self.app: set[str] = set()
        self.permissions: set[str] = set()
        self.skill_count = 0
        self.mcp_servers = 0
        self.index_html = ""

    def note(self, text: str) -> None:
        self.tree.notes.add(text)

    def python_entry(self, kind: str, name: str, target: str, source: str) -> None:
        module = target.split(":")[0].strip().replace(".", "/")
        candidates = (f"{module}.py", f"{module}/__init__.py")
        if any(rel == c or rel.endswith("/" + c)
               for rel in self.rels for c in candidates):
            self.entries.add(f"{kind}:{name}->{target}")
        else:
            self.note(f"unresolved entry point in {source}: {name} -> {target}")

    def bin_entry(self, name: str, target: str, base: str, source: str) -> None:
        resolved = posixpath.normpath(posixpath.join(base, target))
        if resolved in self.rels:
            self.entries.add(f"bin:{name}->{resolved}")
        else:
            self.note(f"unresolved entry point in {source}: {name} -> {target}")

    def add_deps(self, names) -> None:
        self.deps.update(_norm(str(n)) for n in names if n)

    def has_dep(self, names: frozenset[str]) -> list[str]:
        # A go module path ends in the short name ("github.com/gin-gonic/gin");
        # an npm scope ("@scope/mcp") must not match a bare "mcp".
        return sorted(name for name in names
                      if name in self.deps
                      or ("/" in name and not name.startswith("@")
                          and any(dep.endswith("/" + name) for dep in self.deps)))


def _identify(text: str) -> str:
    for label, pattern in LICENSE_SIGNATURES:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return UNIDENTIFIED_LICENSE


def _frontmatter(text: str) -> dict[str, str]:
    """Top-level `key: value` pairs of a leading `---` block, else {}."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    key = ""
    for line in lines[1:120]:
        if line.strip() == "---":
            return fields
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if match:
            key = match.group(1)
            fields[key] = match.group(2).strip()
        elif key and re.match(r"^\s+-\s+\S", line):
            fields[key] = (fields[key] + "," + line.strip()[1:].strip()).strip(",")
    return {}                      # never closed: not frontmatter


def _loads(facts: _Facts, file: _File, text: str, loader) -> dict | None:
    try:
        data = loader(text)
    except (ValueError, RecursionError) as error:
        facts.note(f"unparseable manifest: {file.rel} ({type(error).__name__})")
        return None
    return data if isinstance(data, dict) else None


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _requirement_name(line: str) -> str:
    return re.split(r"[<>=!~;\[@ ]", line.strip(), maxsplit=1)[0]


def _pyproject(facts: _Facts, file: _File, text: str) -> None:
    data = _loads(facts, file, text, tomllib.loads)
    if data is None:
        return
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    # Without these tables the file is tool configuration, not a build.
    if project or poetry or "build-system" in data:
        facts.build_systems.add("python-pyproject")
    for table, kind in ((project.get("scripts"), "console_script"),
                        (poetry.get("scripts"), "console_script"),
                        (project.get("gui-scripts"), "gui_script")):
        for name, target in sorted((table or {}).items()):
            if isinstance(target, str):
                facts.python_entry(kind, str(name), target, file.rel)
                if kind == "gui_script":
                    facts.app.add(f"gui:gui-scripts({name})")
    facts.add_deps(_requirement_name(str(d)) for d in _as_list(project.get("dependencies")))
    optional = project.get("optional-dependencies")
    for group in (optional.values() if isinstance(optional, dict) else ()):
        facts.add_deps(_requirement_name(str(d)) for d in _as_list(group))
    if isinstance(poetry.get("dependencies"), dict):
        facts.add_deps(poetry["dependencies"])
    for declared in (project.get("license"), poetry.get("license")):
        if isinstance(declared, dict):
            declared = declared.get("text")
        if isinstance(declared, str) and declared.strip():
            facts.licenses.append((declared.strip(), f"manifest:{file.rel}"))


def _setup_py(facts: _Facts, file: _File, text: str) -> None:
    """Read as text. Running setup.py is exactly the thing this module refuses."""
    facts.build_systems.add("python-setuptools")
    for block in _SCRIPT_BLOCK.findall(text):
        for name, target in _SCRIPT_ITEM.findall(block):
            facts.python_entry("console_script", name, target, file.rel)


def _setup_cfg(facts: _Facts, file: _File, text: str) -> None:
    facts.build_systems.add("python-setuptools")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error as error:
        facts.note(f"unparseable manifest: {file.rel} ({type(error).__name__})")
        return
    scripts = parser.get("options.entry_points", "console_scripts", fallback="")
    for line in scripts.splitlines():
        name, _, target = line.partition("=")
        if name.strip() and target.strip():
            facts.python_entry("console_script", name.strip(), target.strip(), file.rel)
    declared = parser.get("metadata", "license", fallback="").strip()
    if declared:
        facts.licenses.append((declared, f"manifest:{file.rel}"))


def _requirements(facts: _Facts, file: _File, text: str) -> None:
    facts.add_deps(_requirement_name(line) for line in text.splitlines()
                   if line.strip() and not line.lstrip().startswith(("#", "-")))


def _package_json(facts: _Facts, file: _File, text: str) -> None:
    data = _loads(facts, file, text, json.loads)
    if data is None:
        return
    facts.build_systems.add("npm")
    base = posixpath.dirname(file.rel)
    package = str(data.get("name") or "bin")
    declared = data.get("bin")
    bins: dict[str, str] = {}
    if isinstance(declared, str):
        bins[package] = declared
    elif isinstance(declared, dict):
        bins = {str(k): v for k, v in declared.items() if isinstance(v, str)}
    for name, target in sorted(bins.items()):
        facts.bin_entry(name, target, base, file.rel)
    scripts = data.get("scripts")
    if isinstance(scripts, dict) and LIFECYCLE_SCRIPTS & set(scripts):
        # Install-time scripts run on the installing machine.
        facts.permissions.add("subprocess")
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(data.get(section), dict):
            facts.add_deps(data[section])
    licence = data.get("license")
    if isinstance(licence, dict):
        licence = licence.get("type")
    if isinstance(licence, str) and licence.strip():
        facts.licenses.append((licence.strip(), f"manifest:{file.rel}"))


def _cargo(facts: _Facts, file: _File, text: str) -> None:
    data = _loads(facts, file, text, tomllib.loads)
    if data is None:
        return
    if "package" in data or "workspace" in data:
        facts.build_systems.add("cargo")
    if isinstance(data.get("dependencies"), dict):
        facts.add_deps(data["dependencies"])
    package = data.get("package")
    declared = package.get("license") if isinstance(package, dict) else None
    if isinstance(declared, str) and declared.strip():
        facts.licenses.append((declared.strip(), f"manifest:{file.rel}"))


def _go_mod(facts: _Facts, file: _File, text: str) -> None:
    facts.build_systems.add("go")
    facts.add_deps(re.findall(r"^\s*(?:require\s+)?([\w.~/-]+\.[\w.~/-]+)\s+v[\w.+-]+",
                              text, re.M))


def _dockerfile(facts: _Facts, file: _File, text: str) -> None:
    if re.search(r"^\s*EXPOSE\s+\d+", text, re.M | re.I):
        facts.service.add(f"dockerfile:EXPOSE({file.rel})")


def _compose(facts: _Facts, file: _File, text: str) -> None:
    if re.search(r"^\s*ports\s*:", text, re.M):
        facts.service.add(f"compose:ports({file.rel})")


def _procfile(facts: _Facts, file: _File, text: str) -> None:
    if re.search(r"^web\s*:", text, re.M):
        facts.service.add(f"procfile:web({file.rel})")


def _pubspec(facts: _Facts, file: _File, text: str) -> None:
    facts.build_systems.add("pub")
    if re.search(r"\bflutter\b", text):
        facts.app.add(f"mobile:flutter({file.rel})")


_MANIFEST_READERS = {
    "pyproject.toml": _pyproject, "setup.py": _setup_py, "setup.cfg": _setup_cfg,
    "requirements.txt": _requirements, "package.json": _package_json,
    "cargo.toml": _cargo, "go.mod": _go_mod, "dockerfile": _dockerfile,
    "docker-compose.yml": _compose, "docker-compose.yaml": _compose,
    "compose.yml": _compose, "compose.yaml": _compose, "procfile": _procfile,
    "pubspec.yaml": _pubspec,
}
#: Build systems whose manifest needs no parsing to be believed.
_PLAIN_BUILD = {"pom.xml", "build.gradle", "build.gradle.kts", "gemfile",
                "composer.json", "cmakelists.txt", "makefile"}


def _read_manifests(facts: _Facts) -> None:
    tree = facts.tree
    parsed = 0
    for file in tree.files:
        key = file.name.lower()
        if key not in MANIFEST_NAMES:
            continue
        if len(facts.manifests) < MAX_LISTED:
            facts.manifests.add(file.rel)
        if file.depth > MANIFEST_DEPTH:
            continue
        if key in _PLAIN_BUILD:
            facts.build_systems.add(BUILD_SYSTEM_BY_MANIFEST[key])
            continue
        reader = _MANIFEST_READERS.get(key)
        if reader is None:
            continue
        parsed += 1
        if parsed > MAX_MANIFESTS:
            tree.notes.add(f"manifest cap ({MAX_MANIFESTS}) reached")
            return
        text = tree.read(file, limit=MAX_FILE_BYTES)
        if text is not None:
            reader(facts, file, text)
        if key == "dockerfile":
            facts.build_systems.add("docker")


def _read_markers(facts: _Facts) -> None:
    """Skill, plugin, MCP config, model and entry-point files, by name."""
    tree = facts.tree
    top_level = {f.rel.split("/")[0] for f in tree.files if "/" in f.rel}
    skills_read = 0
    for file in tree.files:
        name = file.name.lower()
        if name == "skill.md" and skills_read < MAX_SKILL_FILES:
            skills_read += 1
            text = tree.read(file)
            fields = _frontmatter(text) if text else {}
            if "name" in fields and "description" in fields:
                facts.skill_count += 1
                if len(facts.skills) < MAX_LISTED:
                    facts.skills.add(f"skill:{file.rel}")
                for tool in re.split(r"[,\s\[\]]+", fields.get("allowed-tools", "")):
                    if tool:
                        facts.permissions.add(f"tool:{tool}")
        elif name in ("plugin.json", "marketplace.json") \
                and "/.claude-plugin/" in "/" + file.rel:
            facts.skills.add(f"plugin:{file.rel}")
        elif name == ".mcp.json":
            facts.manifests.add(file.rel)
            text = tree.read(file)
            data = _loads(facts, file, text, json.loads) if text else None
            servers = (data or {}).get("mcpServers", data or {})
            count = len(servers) if isinstance(servers, dict) else 0
            facts.mcp_servers += count
            facts.mcp.add(f"config:{file.rel}({count} servers)")
        elif name == "__main__.py" and file.depth <= 4:
            facts.entries.add(f"main:{file.name}->{file.rel}")
        elif name == "main.go" and file.depth <= 3:
            text = tree.read(file)
            if text and re.search(r"^package main\b", text, re.M):
                facts.entries.add(f"main:main.go->{file.rel}")
        elif name == "main.rs" and file.rel.endswith("src/main.rs"):
            facts.entries.add(f"main:main.rs->{file.rel}")
        elif name == "readme.md" and file.depth == 0:
            text = tree.read(file)
            fields = _frontmatter(text) if text else {}
            if MODEL_CARD_KEYS & set(fields):
                facts.model.add(f"model_card:{file.rel}")
            if fields.get("license"):
                facts.licenses.append((fields["license"], f"manifest:{file.rel}"))
        elif name == "config.json" and file.depth == 0:
            text = tree.read(file)
            data = _loads(facts, file, text, json.loads) if text else None
            if data and ("architectures" in data or "model_type" in data):
                facts.model.add(f"hf_config:{file.rel}")
        elif name == "index.html" and not facts.index_html:
            facts.index_html = file.rel
        if file.suffix in WEIGHT_SUFFIXES:
            if file.suffix in PICKLE_SUFFIXES:
                facts.permissions.add("unsafe_deserialization")
            if len(facts.model) < MAX_LISTED:
                facts.model.add(f"weights:{file.rel}")
    if "src-tauri" in top_level:
        facts.app.add("gui:src-tauri")
    if {"android", "ios"} <= top_level:
        facts.app.add("mobile:android+ios")


def _read_sources(facts: _Facts) -> None:
    """Permission, MCP-server and service hints from the head of source files."""
    tree = facts.tree
    sources = sorted((f for f in tree.files if f.suffix in LANGUAGE_BY_SUFFIX),
                     key=lambda f: (f.depth, f.rel))
    if len(sources) > MAX_CONTENT_SCANS:
        tree.notes.add(f"source scan cap ({MAX_CONTENT_SCANS}) reached")
    for file in sources[:MAX_CONTENT_SCANS]:
        text = tree.read(file)
        if text is None:
            continue
        for permission, pattern in PERMISSION_PATTERNS:
            if pattern.search(text):
                facts.permissions.add(permission)
        if MCP_SERVER_CODE.search(text) and len(facts.mcp) < MAX_LISTED:
            facts.mcp.add(f"server_code:{file.rel}")
        if SERVICE_CONSTRUCT.search(text) and len(facts.service) < MAX_LISTED:
            facts.service.add(f"construct:{file.rel}")
        if re.search(r"QApplication\(|\btk\.Tk\(|BrowserWindow\(", text):
            facts.app.add(f"gui:{file.rel}")


def _license_of(facts: _Facts) -> tuple[str, str]:
    """The licence file in the root wins; a manifest's declaration is the fallback."""
    tree = facts.tree
    for file in tree.files:
        stem = file.name.upper().split(".")[0]
        if file.depth == 0 and (stem in ("LICENSE", "LICENCE", "COPYING", "UNLICENSE")
                                or stem.startswith("LICENSE-")):
            text = tree.read(file, limit=4000)
            return (_identify(text) if text is not None else UNIDENTIFIED_LICENSE,
                    f"file:{file.name}")
    if facts.licenses:
        return sorted(facts.licenses)[0]
    return NO_LICENSE, ""


def build_dossier(path: str | os.PathLike) -> Dossier:
    """
    Read a local directory into a Dossier. Never imports, executes or installs
    anything from it, and never reaches the network; fetching is CAP-FORGE-003.
    """
    root = Path(path)
    if not root.is_dir():
        raise ClassifierError(f"not a directory: {path}")
    tree = _Tree(root)
    facts = _Facts(tree)

    languages: dict[str, int] = {}
    prose = 0
    for file in tree.files:
        language = LANGUAGE_BY_SUFFIX.get(file.suffix)
        if language:
            languages[language] = languages.get(language, 0) + 1
        elif file.suffix in PROSE_SUFFIXES and file.name.lower() not in MANIFEST_NAMES:
            prose += 1                 # a manifest is configuration, not prose
    code = sum(languages.values())

    _read_manifests(facts)
    _read_markers(facts)
    _read_sources(facts)

    for dependency in facts.has_dep(MCP_DEPENDENCIES):
        facts.mcp.add(f"dependency:{dependency}")
    for framework in facts.has_dep(SERVICE_FRAMEWORKS):
        facts.service.add(f"framework:{framework}")
    for framework in facts.has_dep(GUI_FRAMEWORKS):
        facts.app.add(f"gui:{framework}")
    # An index.html alone is just a page; it counts only beside a framework.
    if facts.index_html:
        for framework in facts.has_dep(FRONTEND_FRAMEWORKS):
            facts.app.add(f"frontend:{framework}({facts.index_html})")

    license_label, license_source = _license_of(facts)
    weights = sum(1 for f in tree.files if f.suffix in WEIGHT_SUFFIXES)
    total = prose + code
    return Dossier(
        name=root.name or str(root),
        license=license_label,
        license_source=license_source,
        languages=tuple(sorted(languages.items(), key=lambda kv: (-kv[1], kv[0]))),
        build_systems=tuple(sorted(facts.build_systems)),
        entry_points=tuple(sorted(facts.entries)),
        manifests=tuple(sorted(facts.manifests)[:MAX_LISTED]),
        mcp_signals=tuple(sorted(facts.mcp)),
        skill_signals=tuple(sorted(facts.skills)),
        model_signals=tuple(sorted(facts.model)),
        service_signals=tuple(sorted(facts.service)),
        app_signals=tuple(sorted(facts.app)),
        skill_count=facts.skill_count,
        mcp_config_servers=facts.mcp_servers,
        weight_files=weights,
        permissions=tuple(sorted(facts.permissions)),
        file_count=len(tree.files),
        code_files=code,
        prose_files=prose,
        prose_ratio=round(prose / total, 3) if total else 0.0,
        truncated=tree.truncated,
        notes=tuple(sorted(tree.notes)),
    )


# ---------------------------------------------------------------------------
# Stage 2: the rule table. Ordered; the first rule that holds decides.
# ---------------------------------------------------------------------------

def _prefixed(signals: tuple[str, ...], prefix: str) -> list[str]:
    return [s for s in signals if s.startswith(prefix)]


def _structural(d: Dossier) -> tuple[str, float, str, list[str]]:
    server_code = _prefixed(d.mcp_signals, "server_code:")
    mcp_deps = _prefixed(d.mcp_signals, "dependency:")
    mcp_config = _prefixed(d.mcp_signals, "config:")
    plugins = _prefixed(d.skill_signals, "plugin:")
    typed = d.skill_signals or d.mcp_signals or d.model_signals
    runnable = bool(d.entry_points or server_code)
    strong_service = [s for s in d.service_signals if not s.startswith("framework:")]
    framework = _prefixed(d.service_signals, "framework:")
    strong_app = [s for s in d.app_signals if not s.startswith("frontend:")]
    weak_app = _prefixed(d.app_signals, "frontend:")

    # 1. The non-executable guard. Manifests alone never defeat it: only a
    # declared entry point that the tree really contains does.
    if (d.prose_ratio >= PROSE_DOMINANT and d.prose_files >= MIN_CORPUS_FILES
            and not d.entry_points and not typed):
        stray = [f"stray manifests ignored: {', '.join(d.manifests)}"] if d.manifests else []
        return (RESEARCH_CORPUS, 0.9,
                "prose-dominant tree with no resolvable entry point and no skill, "
                "MCP or model signal",
                [f"prose_ratio={d.prose_ratio} ({d.prose_files} prose/data, "
                 f"{d.code_files} code)", "no resolved entry point"] + stray)

    # 2. Capability pack: a bundle of skills or MCP entries, nothing to run itself.
    members = (d.skill_count >= 2
               or (d.skill_count >= 1 and d.mcp_config_servers >= 1)
               or (d.mcp_config_servers >= 2 and not server_code)
               or bool(plugins))
    if members and not runnable:
        return (CAPABILITY_PACK, 0.9,
                f"{d.skill_count} skills and {d.mcp_config_servers} MCP entries "
                "bundled, with no entry point of its own",
                list(d.skill_signals[:5]) + mcp_config)

    # 3. A single skill is a recipe, not a program.
    if d.skill_count == 1 and not runnable:
        return (SKILL, 0.9, "exactly one SKILL.md and no entry point",
                list(d.skill_signals))

    # 4. MCP server: it contains server code, or depends on an MCP SDK and is runnable.
    if server_code or (mcp_deps and d.entry_points):
        confidence = 0.9 if server_code and mcp_deps else 0.8 if server_code else 0.7
        return (MCP, confidence, "contains an MCP server implementation"
                if server_code else "depends on an MCP SDK and has an entry point",
                server_code[:5] + mcp_deps + list(d.entry_points[:3]))

    # 5. Model: weights, and no code to run them with. Weights beside a build
    # system but with no model card are test fixtures of some library.
    documented = (_prefixed(d.model_signals, "model_card:")
                  or _prefixed(d.model_signals, "hf_config:"))
    if d.weight_files and not d.entry_points and (documented or not d.build_systems):
        return (MODEL, 0.85, f"{d.weight_files} weight file(s) and no entry point",
                list(d.model_signals[:5]))

    # 6. Service: it is built to listen.
    if strong_service or (framework and not d.entry_points):
        return (SERVICE, 0.8, "listens for requests or ships to be run as a daemon",
                strong_service[:5] + framework)

    # 7. Application: a GUI, mobile or front-end app (a CLI wins over a bare front end).
    if strong_app or (weak_app and not d.entry_points):
        return (APPLICATION, 0.7, "builds a user-facing application",
                strong_app[:5] + weak_app[:3])

    # 8. CLI: a command the tree really provides.
    if d.entry_points:
        return (CLI, 0.8, "declares an entry point the tree contains",
                list(d.entry_points[:5]))

    # 9. Library: something importable is built, with nothing to launch.
    builds = [b for b in d.build_systems if b in LIBRARY_BUILD_SYSTEMS]
    if builds and d.code_files:
        return (LIBRARY, 0.7, "a build manifest and source with no entry point",
                [f"build: {', '.join(builds)}",
                 f"languages: {', '.join(l for l, _ in d.languages[:3])}"])

    # 10. Nothing fits.
    return (REFERENCE_ONLY, 0.3,
            "ambiguous: no entry point, build manifest or typed signal "
            f"({d.code_files} code, {d.prose_files} prose/data files)",
            [f"languages: {', '.join(l for l, _ in d.languages[:3]) or 'none'}"])


def classify(dossier: Dossier) -> Classification:
    """
    Pure and deterministic: the same Dossier always gives the same
    Classification, and nothing is read, run or fetched. See `_structural` for
    the ordered rule table.

    The licence gate comes last. A tree with no licence at all may be read for
    patterns but not used, so it is held at REFERENCE_ONLY whatever it is
    structurally; `structural_class` keeps what it would have been. An
    unrecognised licence text is not treated as no licence: it keeps its class
    at lower confidence and says so, because a human has to read it.
    """
    structural, confidence, reason, evidence = _structural(dossier)
    evidence = list(evidence)
    result_class = structural
    if dossier.license == NO_LICENSE and structural != REFERENCE_ONLY:
        result_class = REFERENCE_ONLY
        confidence = min(confidence, 0.6)
        reason = (f"unlicensed: no licence file or declared licence, so held at "
                  f"reference-only; structurally a {structural} ({reason})")
    elif dossier.license == UNIDENTIFIED_LICENSE:
        confidence -= 0.1
        evidence.append("licence text not recognised: needs a human reading")
    if dossier.truncated:
        confidence -= 0.1
        evidence.append("the read was cut off by a size cap")
    return Classification(repo_class=result_class,
                          confidence=round(max(confidence, 0.0), 2),
                          reason=reason, evidence=tuple(evidence),
                          structural_class=structural)
