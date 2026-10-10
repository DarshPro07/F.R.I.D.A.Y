"""
CAP-FORGE-004: FRIDAY derives the minimum viable integration.

Each plan names the simplest reliable path from a fixed set of recipe
templates and records why the simpler paths were rejected; nothing here
involves a model, so the same dossier always gives the same plan.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from friday import forge, forge_recipes as fr, provider_states
from friday import repo_classifier as rc

MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person\n"
GPL = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n"
APACHE = "Apache License\nVersion 2.0, January 2004\n"

SKILL_MD = ("---\nname: only\ndescription: Does only.\nallowed-tools: Bash, Read\n"
            "---\n# only\nUse it.\n")
PYPROJECT_LIBRARY = '[project]\nname = "lib"\nversion = "1"\n'
PYPROJECT_CLI = ('[project]\nname = "tool"\nversion = "1"\n'
                 '[project.scripts]\ntool = "tool.cli:main"\n')

TREES = {
    "skill": {
        "LICENSE": MIT, "SKILL.md": SKILL_MD, "scripts/helper.py": "print('hi')\n",
    },
    "capability_pack": {
        "LICENSE": MIT, "README.md": "# a pack of skills\n",
        "skills/a/SKILL.md": SKILL_MD, "skills/b/SKILL.md": SKILL_MD,
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
    "web_application": {
        "LICENSE": MIT, "index.html": "<html></html>\n",
        "package.json": json.dumps({"name": "site",
                                   "dependencies": {"react": "^18.0.0"}}),
    },
    "desktop_application": {
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

EXPECTED_PATH = {
    "library": fr.NATIVE_CALL,
    "cli": fr.CLI_WRAPPER,
    "mcp": fr.MCP_SERVER,
    "service": fr.SERVICE_ADAPTER,
    "web_application": fr.BROWSER_PATH,
    "desktop_application": fr.REFERENCE_ONLY,
    "skill": fr.REFERENCE_ONLY,
    "capability_pack": fr.REFERENCE_ONLY,
    "model": fr.REFERENCE_ONLY,
    "research_corpus": fr.REFERENCE_ONLY,
    "reference_only": fr.REFERENCE_ONLY,
}


def write_tree(root: Path, files: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return root


def plan_tree(tmp_path: Path, files: dict, name: str = "repo"):
    dossier = rc.build_dossier(write_tree(tmp_path / name, files))
    classification = rc.classify(dossier)
    return fr.plan(dossier, classification), dossier, classification


@pytest.mark.parametrize("label", sorted(TREES))
def test_each_labelled_repository_gets_its_simplest_path(tmp_path, label):
    result, _, classification = plan_tree(tmp_path, TREES[label])
    assert result.path == EXPECTED_PATH[label], (result, classification)
    assert result.repo_class == classification.repo_class
    assert result.reason


def test_the_labelled_set_covers_every_path_and_every_class():
    assert set(EXPECTED_PATH.values()) == set(fr.PATHS)


@pytest.mark.parametrize("label", sorted(TREES))
def test_a_plan_records_why_every_simpler_path_was_rejected(tmp_path, label):
    result, _, _ = plan_tree(tmp_path, TREES[label])
    ladder = [p for p in fr.PATHS if p != fr.REFERENCE_ONLY]
    if result.path == fr.REFERENCE_ONLY:
        simpler = ladder
    else:
        simpler = ladder[:ladder.index(result.path)]
    assert [path for path, _ in result.rejected] == simpler
    assert all(reason.strip() for _, reason in result.rejected)
    assert result.path not in {path for path, _ in result.rejected}


def test_the_rejection_names_the_classification_that_ruled_the_path_out(tmp_path):
    result, _, _ = plan_tree(tmp_path, TREES["mcp"])
    reasons = dict(result.rejected)
    assert "mcp" in reasons[fr.NATIVE_CALL]
    assert "mcp" in reasons[fr.CLI_WRAPPER]


@pytest.mark.parametrize("label", sorted(TREES))
def test_every_plan_carries_manifest_permissions_tests_fallback_and_exposure(tmp_path, label):
    result, dossier, _ = plan_tree(tmp_path, TREES[label])
    assert result.fabric_mode in provider_states.INTEGRATION_MODES
    assert result.manifest["integration_path"] == result.path
    assert result.manifest["fabric_mode"] == result.fabric_mode
    assert result.manifest["license"] == dossier.license
    assert forge.NAME_RE.match(result.manifest["id"])
    assert result.tests and all(t.strip() for t in result.tests)
    assert result.fallback.strip() and result.exposure.strip()
    assert isinstance(result.permissions, tuple)


def test_the_recipe_is_a_fixed_template_not_a_per_repository_invention(tmp_path):
    first, _, _ = plan_tree(tmp_path, TREES["library"], "one")
    second, _, _ = plan_tree(tmp_path, {
        **TREES["library"], "src/lib/extra.py": "def g():\n    return 2\n"}, "two")
    for field in ("path", "fabric_mode", "tests", "fallback", "exposure", "rejected"):
        assert getattr(first, field) == getattr(second, field)
    assert set(fr.RECIPES) == set(fr.PATHS)


def test_planning_is_deterministic(tmp_path):
    first, dossier, classification = plan_tree(tmp_path, TREES["service"])
    assert fr.plan(dossier, classification) == first
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(
        fr.plan(dossier, classification).to_dict(), sort_keys=True)


def test_permissions_include_what_the_dossier_saw_in_the_source(tmp_path):
    files = {**TREES["library"],
             "src/lib/net.py": "import requests\n\ndef get():\n    return requests.get('x')\n"}
    result, dossier, _ = plan_tree(tmp_path, files)
    assert "network" in dossier.permissions
    assert "network" in result.permissions
    quiet, _, _ = plan_tree(tmp_path, TREES["library"], "quiet")
    assert "network" not in quiet.permissions


def test_a_copyleft_library_is_never_linked_into_the_process(tmp_path):
    files = {**TREES["library"], "LICENSE": GPL}
    result, dossier, classification = plan_tree(tmp_path, files)
    assert classification.repo_class == rc.LIBRARY
    assert result.path == fr.REFERENCE_ONLY
    assert result.fabric_mode in provider_states.ISOLATED_MODES
    assert "copyleft" in dict(result.rejected)[fr.NATIVE_CALL]


@pytest.mark.parametrize("label", sorted(TREES))
def test_a_copyleft_licence_never_gets_a_non_isolated_mode(tmp_path, label):
    files = {**TREES[label], "LICENSE": GPL}
    result, _, _ = plan_tree(tmp_path, files)
    assert result.fabric_mode in provider_states.ISOLATED_MODES


@pytest.mark.parametrize("declared", [
    "GPL-3.0-or-later", "AGPL-3.0-only", "GPL-2.0", "LGPL-3.0",
    "MIT OR GPL-3.0", "Apache-2.0 WITH LLVM-exception", "PolyForm-Noncommercial-1.0.0",
    "Proprietary",
])
def test_a_licence_declared_in_a_manifest_is_never_linked_unless_known_permissive(tmp_path, declared):
    files = {k: v for k, v in TREES["library"].items() if k != "LICENSE"}
    files["pyproject.toml"] = f'[project]\nname = "lib"\nversion = "1"\nlicense = "{declared}"\n'
    result, dossier, classification = plan_tree(tmp_path, files)
    assert dossier.license == declared
    assert classification.repo_class == rc.LIBRARY
    assert result.path != fr.NATIVE_CALL
    assert result.fabric_mode in provider_states.ISOLATED_MODES
    assert fr.NATIVE_CALL in dict(result.rejected)


@pytest.mark.parametrize("text", [
    "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 3\n",
    "All rights reserved. Do whatever the author says.\n",
])
def test_a_library_with_an_unrecognised_or_weak_copyleft_licence_text_is_not_linked(tmp_path, text):
    files = {**TREES["library"], "LICENSE": text}
    result, dossier, _ = plan_tree(tmp_path, files)
    assert result.path != fr.NATIVE_CALL, dossier.license
    assert result.fabric_mode in provider_states.ISOLATED_MODES


@pytest.mark.parametrize("declared", ["MIT", "Apache-2.0", "BSD-3-Clause", "MIT OR Apache-2.0", "mit"])
def test_a_known_permissive_library_is_still_linked(tmp_path, declared):
    files = {k: v for k, v in TREES["library"].items() if k != "LICENSE"}
    files["pyproject.toml"] = f'[project]\nname = "lib"\nversion = "1"\nlicense = "{declared}"\n'
    result, _, _ = plan_tree(tmp_path, files)
    assert result.path == fr.NATIVE_CALL


def test_an_unlicensed_repository_is_reference_only_whatever_it_is(tmp_path):
    files = {k: v for k, v in TREES["cli"].items() if k != "LICENSE"}
    result, _, classification = plan_tree(tmp_path, files)
    assert classification.repo_class == rc.REFERENCE_ONLY
    assert result.path == fr.REFERENCE_ONLY
    assert result.fabric_mode == provider_states.REFERENCE_ONLY
    assert result.permissions == ()


def test_a_hostile_repository_name_stays_data_and_cannot_shape_the_manifest_id(tmp_path):
    _, dossier, classification = plan_tree(tmp_path, TREES["cli"])
    for hostile in ("../../etc; rm -rf /", "A", "9lives", "x" * 200, "résumé tool"):
        result = fr.plan(dataclasses.replace(dossier, name=hostile), classification)
        assert forge.NAME_RE.match(result.manifest["id"]), hostile
        assert result.manifest["name"] == hostile


def test_a_classification_outside_the_ten_classes_is_refused(tmp_path):
    _, dossier, classification = plan_tree(tmp_path, TREES["cli"])
    bad = dataclasses.replace(classification, repo_class="invented")
    with pytest.raises(fr.ForgeRecipeError):
        fr.plan(dossier, bad)


def test_the_module_does_no_io_and_calls_no_model():
    import inspect
    source = inspect.getsource(fr)
    for needle in ("import subprocess", "import socket", "urllib", "import requests", "open(", "import os"):
        assert needle not in source, needle
