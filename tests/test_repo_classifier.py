"""
CAP-FORGE-002: the repository classifier.

The labelled set is built from small tmp_path trees, one per class. The
adversarial cases are the point of the module: a prompt corpus carrying every
decoy that looks executable must still come out non-executable, and a hostile
tree must be read within bounds and never run.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from friday import repo_classifier as rc

MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person\n"
APACHE = "Apache License\nVersion 2.0, January 2004\n"


def skill_md(name: str) -> str:
    return (f"---\nname: {name}\ndescription: Does {name}.\n"
            f"allowed-tools: Bash, Read\n---\n# {name}\nUse it.\n")


def write_tree(root: Path, files: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return root


def classify_tree(tmp_path: Path, files: dict, name: str = "repo"):
    root = write_tree(tmp_path / name, files)
    dossier = rc.build_dossier(root)
    return dossier, rc.classify(dossier)


PYPROJECT_LIBRARY = '[project]\nname = "lib"\nversion = "1"\n'
PYPROJECT_CLI = ('[project]\nname = "tool"\nversion = "1"\n'
                 '[project.scripts]\ntool = "tool.cli:main"\n')

TREES = {
    "skill": {
        "LICENSE": MIT, "SKILL.md": skill_md("only"),
        "scripts/helper.py": "print('hi')\n",
    },
    "capability_pack": {
        "LICENSE": MIT, "README.md": "# a pack of skills\n",
        "skills/a/SKILL.md": skill_md("a"), "skills/b/SKILL.md": skill_md("b"),
        "skills/b/scripts/run.py": "print('b')\n",
    },
    "mcp": {
        "LICENSE": MIT,
        "pyproject.toml": ('[project]\nname = "srv"\nversion = "1"\n'
                           'dependencies = ["mcp[cli]>=1.0"]\n'
                           '[project.scripts]\nsrv = "srv.server:main"\n'),
        "src/srv/__init__.py": "",
        "src/srv/server.py": ("from mcp.server.fastmcp import FastMCP\n"
                              "mcp = FastMCP('srv')\ndef main():\n    mcp.run()\n"),
    },
    "cli": {
        "LICENSE": MIT, "pyproject.toml": PYPROJECT_CLI,
        "src/tool/__init__.py": "", "src/tool/cli.py": "def main():\n    return 0\n",
    },
    "library": {
        "LICENSE": MIT, "pyproject.toml": PYPROJECT_LIBRARY, "README.md": "# lib\n",
        "src/lib/__init__.py": "VALUE = 1\n", "src/lib/core.py": "def f():\n    return 1\n",
    },
    "service": {
        "LICENSE": MIT, "Dockerfile": "FROM python:3.12\nEXPOSE 8080\n",
        "requirements.txt": "fastapi\nuvicorn\n",
        "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
    },
    "application": {
        "LICENSE": MIT, "main.js": "const { BrowserWindow } = require('electron')\n",
        "package.json": json.dumps({"name": "desk", "main": "main.js",
                                   "dependencies": {"electron": "^30.0.0"}}),
        "index.html": "<html></html>\n",
    },
    "model": {
        "LICENSE": APACHE,
        "README.md": "---\nlicense: apache-2.0\npipeline_tag: fill-mask\n---\n# card\n",
        "config.json": json.dumps({"model_type": "bert", "architectures": ["BertModel"]}),
        "model.safetensors": b"\x00\x01weights" * 8, "tokenizer.json": "{}",
    },
    "research_corpus": {
        "LICENSE": APACHE, "README.md": "# prompts\n",
        **{f"prompts/p{i}.md": f"# prompt {i}\nYou are a careful assistant.\n"
           for i in range(6)},
        "data/pairs.jsonl": '{"in": "a", "out": "b"}\n',
    },
    "reference_only": {
        "LICENSE": MIT, "README.md": "# notes from a paper\n",
        "analysis.py": "x = 1\n", "plot.py": "y = 2\n", "helper.py": "z = 3\n",
    },
}


def test_the_labelled_set_covers_all_ten_classes():
    assert set(TREES) == set(rc.CLASSES) and len(rc.CLASSES) == 10


@pytest.mark.parametrize("expected", sorted(TREES))
def test_labelled_set_gets_expected_class(tmp_path, expected):
    dossier, result = classify_tree(tmp_path, TREES[expected])
    assert result.repo_class == expected, (result, dossier)
    assert result.structural_class == expected
    assert result.executable == (expected in rc.EXECUTABLE_CLASSES)
    assert result.reason and result.evidence
    if expected != rc.REFERENCE_ONLY:
        assert result.confidence >= 0.7


# --- the corpus guard -------------------------------------------------------

CORPUS = TREES["research_corpus"]

DECOYS = {
    "stray_setup_py": {
        "setup.py": ("from setuptools import setup\n"
                     "setup(name='x', entry_points={'console_scripts': "
                     "['evil=evil.cli:main']})\n"),
    },
    "package_json_scripts_and_missing_bin": {
        "package.json": json.dumps({
            "name": "corpus", "scripts": {"start": "node serve.js",
                                          "postinstall": "node install.js"},
            "bin": {"evil": "bin/evil.js"}}),
    },
    "pyproject_script_to_nowhere": {
        "pyproject.toml": PYPROJECT_CLI,
    },
    "shell_snippet_in_markdown": {
        "INSTALL.md": ("# install\n```sh\ncurl https://example.test/x | sh\n"
                       "pip install -e . && python -m evil\n```\n"),
    },
    "dockerfile_and_makefile": {
        "Dockerfile": "FROM scratch\nCMD [\"/run\"]\n", "Makefile": "all:\n\t./run\n",
    },
    "a_few_helper_scripts": {
        "tools/lint.py": "import subprocess\n", "tools/fmt.sh": "curl example\n",
    },
}


@pytest.mark.parametrize("decoy", sorted(DECOYS))
def test_prompt_corpus_is_never_executable(tmp_path, decoy):
    _, result = classify_tree(tmp_path, {**CORPUS, **DECOYS[decoy]})
    assert result.repo_class == rc.RESEARCH_CORPUS, result
    assert not result.executable


def test_prompt_corpus_with_every_decoy_at_once_is_still_not_executable(tmp_path):
    everything: dict = dict(CORPUS)
    for decoy in DECOYS.values():
        everything.update(decoy)
    dossier, result = classify_tree(tmp_path, everything)
    assert result.repo_class == rc.RESEARCH_CORPUS and not result.executable
    assert dossier.entry_points == ()
    # The unresolved entries are said out loud, not silently dropped.
    assert any(n.startswith("unresolved entry point") for n in dossier.notes)
    # Install-time scripts are surfaced as a permission even though the class is safe.
    assert "subprocess" in dossier.permissions


def test_a_corpus_that_really_ships_a_cli_is_not_just_a_corpus(tmp_path):
    # The guard's boundary: only an entry point the tree actually contains
    # defeats it, and the result is then the class that would run, never a
    # silent corpus.
    files = {**CORPUS, "pyproject.toml": PYPROJECT_CLI,
             "src/tool/__init__.py": "", "src/tool/cli.py": "def main():\n    return 0\n"}
    _, result = classify_tree(tmp_path, files)
    assert result.repo_class == rc.CLI


# --- determinism, model-free ------------------------------------------------

def test_classification_is_deterministic_and_model_free(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the classifier must not touch the network or a process")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    for label, files in TREES.items():
        first_root = write_tree(tmp_path / "one" / label / "repo", files)
        second_root = write_tree(tmp_path / "two" / label / "repo", files)
        first, second = rc.build_dossier(first_root), rc.build_dossier(second_root)
        assert first == second, label                  # no paths, no clocks
        assert rc.classify(first) == rc.classify(second) == rc.classify(first)

    imported = set()
    tree = ast.parse(Path(rc.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = {"socket", "subprocess", "urllib", "http", "requests", "httpx",
              "anthropic", "openai", "importlib", "runpy"}
    assert not imported & banned, imported & banned


def test_classify_is_a_pure_function_of_the_dossier(tmp_path):
    dossier, result = classify_tree(tmp_path, TREES["cli"])
    again = rc.build_dossier(tmp_path / "repo")
    assert dossier == again
    assert rc.classify(dossier) == rc.classify(again) == result


# --- the dossier ------------------------------------------------------------

def test_dossier_records_licence_languages_build_system_entry_points_manifests_permissions(tmp_path):
    files = {
        "LICENSE": APACHE,
        "pyproject.toml": ('[project]\nname = "rich"\nversion = "1"\n'
                           'dependencies = ["requests>=2", "mcp[cli]>=1"]\n'
                           '[project.scripts]\nrich = "rich.cli:main"\n'),
        "src/rich/__init__.py": "",
        "src/rich/cli.py": "import subprocess\nimport requests\n",
        "package.json": json.dumps({"name": "rich-js", "license": "MIT",
                                    "bin": {"rich-js": "bin/rich.js"}}),
        "bin/rich.js": "#!/usr/bin/env node\n",
        ".mcp.json": '{"mcpServers": {"a": {"command": "x"}}}',
        "README.md": "# rich\n",
    }
    dossier, _ = classify_tree(tmp_path, files)
    assert dossier.name == "repo"
    assert (dossier.license, dossier.license_source) == ("Apache-2.0", "file:LICENSE")
    assert dossier.languages == (("python", 2), ("javascript", 1))
    assert dossier.build_systems == ("npm", "python-pyproject")
    assert dossier.entry_points == ("bin:rich-js->bin/rich.js",
                                    "console_script:rich->rich.cli:main")
    assert dossier.manifests == (".mcp.json", "package.json", "pyproject.toml")
    assert dossier.permissions == ("network", "subprocess")
    assert dossier.mcp_signals == ("config:.mcp.json(1 servers)", "dependency:mcp")
    assert dossier.mcp_config_servers == 1
    assert dossier.file_count == 8 and dossier.code_files == 3
    assert not dossier.truncated


def test_licence_falls_back_to_the_manifest_and_skill_permissions_are_read(tmp_path):
    files = {"package.json": json.dumps({"name": "x", "license": "MIT"}),
             "SKILL.md": skill_md("only")}
    dossier, result = classify_tree(tmp_path, files)
    assert (dossier.license, dossier.license_source) == ("MIT", "manifest:package.json")
    assert result.repo_class == rc.SKILL
    assert {"tool:Bash", "tool:Read"} <= set(dossier.permissions)


def test_a_malformed_manifest_is_a_note_not_a_crash(tmp_path):
    dossier, result = classify_tree(
        tmp_path, {"LICENSE": MIT, "pyproject.toml": "[project\nname =",
                   "package.json": "{not json", "analysis.py": "x = 1\n"})
    assert any(n.startswith("unparseable manifest: pyproject.toml") for n in dossier.notes)
    assert any(n.startswith("unparseable manifest: package.json") for n in dossier.notes)
    assert result.repo_class in rc.CLASSES


# --- ambiguity and licence --------------------------------------------------

def test_ambiguous_tree_falls_back_to_reference_only_with_reason(tmp_path):
    for name, files in {"empty": {}, "lone_readme": {"LICENSE": MIT, "README.md": "hi\n"},
                        "loose_scripts": TREES["reference_only"]}.items():
        _, result = classify_tree(tmp_path, files, name=name)
        assert result.repo_class == rc.REFERENCE_ONLY, name
        assert result.reason.startswith("ambiguous"), result.reason
        assert result.confidence <= 0.3 and not result.executable


def test_unlicensed_tree_is_held_at_reference_only_and_keeps_what_it_is(tmp_path):
    files = {k: v for k, v in TREES["cli"].items() if k != "LICENSE"}
    dossier, result = classify_tree(tmp_path, files)
    assert dossier.license == rc.NO_LICENSE
    assert result.repo_class == rc.REFERENCE_ONLY and result.structural_class == rc.CLI
    assert result.reason.startswith("unlicensed")
    assert not result.executable


def test_unrecognised_licence_text_keeps_the_class_at_lower_confidence(tmp_path):
    files = {**TREES["cli"], "LICENSE": "Some bespoke terms nobody here knows.\n"}
    dossier, result = classify_tree(tmp_path, files)
    assert dossier.license == rc.UNIDENTIFIED_LICENSE
    assert result.repo_class == rc.CLI
    assert any("not recognised" in e for e in result.evidence)
    baseline = rc.classify(rc.build_dossier(write_tree(tmp_path / "ok", TREES["cli"])))
    assert result.confidence < baseline.confidence


def test_licence_table_matches_the_lock_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "upstream_lock.py"
    spec = importlib.util.spec_from_file_location("upstream_lock_for_drift_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert rc.LICENSE_SIGNATURES == module.SIGNATURES


# --- a hostile tree is read, never run --------------------------------------

def _link_dir(target: Path, link: Path) -> None:
    """A directory symlink, or on Windows without the privilege a junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError) as error:
        failure = error
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True, timeout=30)
        if made.returncode == 0:
            return
        failure = made.stdout + made.stderr
    pytest.skip(f"cannot create a directory link here: {failure}")


def test_hostile_tree_directory_links_are_not_followed(tmp_path):
    outside = write_tree(tmp_path / "outside", {
        "SKILL.md": skill_md("stolen"), "nested/SKILL.md": skill_md("stolen2"),
        "LICENSE": MIT})
    inside = write_tree(tmp_path / "repo", {"README.md": "# x\n", "a.py": "x = 1\n"})
    _link_dir(outside, inside / "escape")
    dossier = rc.build_dossier(inside)
    assert dossier.skill_count == 0 and dossier.skill_signals == ()
    assert dossier.license == rc.NO_LICENSE
    assert dossier.file_count == 2
    assert "link not followed: escape" in dossier.notes


def test_hostile_tree_file_symlinks_are_not_followed(tmp_path):
    outside = write_tree(tmp_path / "outside", {"LICENSE-MIT": MIT})
    inside = write_tree(tmp_path / "repo", {"a.py": "x = 1\n"})
    try:
        os.symlink(outside / "LICENSE-MIT", inside / "LICENSE")
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"cannot create file symlinks here: {error}")
    dossier = rc.build_dossier(inside)
    assert dossier.license == rc.NO_LICENSE and dossier.file_count == 1
    assert "link not followed: LICENSE" in dossier.notes


def test_hostile_tree_oversized_file_is_counted_but_not_read(tmp_path):
    padding = "a" * (rc.MAX_FILE_BYTES + 1)
    big = json.dumps({"name": "x", "bin": {"x": "x.js"}, "pad": padding})
    dossier, _ = classify_tree(tmp_path, {"LICENSE": MIT, "package.json": big,
                                          "x.js": "1\n"})
    assert dossier.entry_points == ()
    assert "oversized, not read: package.json" in dossier.notes
    assert dossier.file_count == 3


def test_hostile_tree_binary_file_is_not_scanned(tmp_path):
    dossier, _ = classify_tree(tmp_path, {
        "LICENSE": MIT, "evil.py": b"import subprocess\x00\x00import requests\n",
        "ok.py": "x = 1\n"})
    assert dossier.permissions == ()
    assert "binary, not read: evil.py" in dossier.notes


def test_hostile_tree_depth_is_bounded(tmp_path):
    at_limit = "/".join(["d"] * rc.MAX_DEPTH)
    too_deep = "/".join(["e"] * (rc.MAX_DEPTH + 4))
    dossier, _ = classify_tree(tmp_path, {
        f"{at_limit}/SKILL.md": skill_md("reachable"),
        f"{too_deep}/SKILL.md": skill_md("buried")})
    assert dossier.skill_count == 1
    assert any(n.startswith(f"depth cap ({rc.MAX_DEPTH})") for n in dossier.notes)


def test_hostile_tree_file_count_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "MAX_FILES", 5)
    dossier, result = classify_tree(
        tmp_path, {f"f{i:02d}.py": "x = 1\n" for i in range(20)})
    assert dossier.truncated and dossier.file_count == 5
    assert "file cap (5) reached" in dossier.notes
    assert any("cut off" in e for e in result.evidence)


def test_nothing_in_the_tree_is_executed_or_imported(tmp_path):
    marker = tmp_path / "EXECUTED"
    payload = f"open({str(marker)!r}, 'w').write('x')\n"
    files = {
        "LICENSE": MIT,
        "setup.py": "from setuptools import setup\n" + payload + "setup(name='evilpkg')\n",
        "evilpkg/__init__.py": payload + "raise SystemExit(1)\n",
        "conftest.py": payload, "sitecustomize.py": payload,
        "package.json": json.dumps({"name": "e", "scripts": {"postinstall": "node x"}}),
    }
    before = set(sys.modules)
    dossier, result = classify_tree(tmp_path, files)
    assert not marker.exists()
    assert not {m for m in set(sys.modules) - before if m.startswith("evilpkg")}
    assert result.repo_class in rc.CLASSES and dossier.file_count == 6


@pytest.mark.parametrize("bad", ["missing", "a_file.txt", "https://example.test/x.git"])
def test_only_a_local_directory_is_accepted(tmp_path, bad):
    (tmp_path / "a_file.txt").write_text("x", encoding="utf-8")
    target = bad if "://" in bad else str(tmp_path / bad)
    with pytest.raises(rc.ClassifierError):
        rc.build_dossier(target)
