"""
CAP-FORGE-005: FRIDAY writes the first adapter itself.

The recipes from CAP-FORGE-004 decide that a library needs a fabric adapter.
This slice turns that plan into generated source, generated tests and a
manifest in a caller-owned directory, then runs those generated tests inside
the quarantine boundary before the caller can admit it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from friday import appcontainer
from friday import forge_adapters as fa
from friday import forge_recipes as recipes
from friday import provider_states
from friday.quarantine import Quarantine
from friday.store import Store


class FakeResult:
    exit_code = 0
    stdout = "1 passed\n"
    stderr = ""


class FakeAnalysis:
    result = FakeResult()
    denials = ()
    strength = "TEST_QUARANTINE"


class FakeQuarantine:
    instances = []

    def __init__(self, *, store, run_id, base, isolation):
        self.store = store
        self.run_id = run_id
        self.base = Path(base)
        self.isolation = isolation
        self.acquired = None
        self.runs = []
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def acquire(self, source):
        self.acquired = Path(source)
        return self.acquired

    def run_python(self, target, *args, module=False, timeout=None):
        self.runs.append((target, args, module, timeout))
        return FakeAnalysis()


def library_plan():
    return recipes.IntegrationPlan(
        repo_class="library",
        path=recipes.NATIVE_CALL,
        fabric_mode=provider_states.ADAPTER,
        reason="an importable library with a licence that may be linked",
        rejected=(),
        manifest={
            "id": "tiny_math",
            "name": "tiny-math",
            "origin_class": "library",
            "integration_path": recipes.NATIVE_CALL,
            "fabric_mode": provider_states.ADAPTER,
            "license": "MIT",
            "entry_points": ["console_script:answer->tiny_math:answer"],
        },
        permissions=(),
        tests=("manifest: the capability manifest validates",),
        fallback="report UNAVAILABLE when the import fails",
        exposure="a fabric adapter module (DESCRIPTOR + start/stop/health/call)",
    )


def load(path: Path):
    spec = importlib.util.spec_from_file_location("generated_tiny_math", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def write_tiny_math(root: Path, body: str = "def answer():\n    return 7\n") -> None:
    (root / "tiny_math.py").write_text(body, encoding="utf-8")


def test_a_library_recipe_generates_adapter_tests_and_manifest_in_the_named_directory(tmp_path):
    generated = fa.generate(library_plan(), tmp_path / "out",
                            upstream_commit="abc123")

    assert generated.adapter == tmp_path / "out" / "tiny_math_adapter.py"
    assert generated.tests == tmp_path / "out" / "test_tiny_math_adapter.py"
    assert generated.manifest == tmp_path / "out" / "tiny_math_manifest.json"
    assert generated.adapter.exists()
    assert generated.tests.exists()

    manifest = json.loads(generated.manifest.read_text(encoding="utf-8"))
    assert manifest["provider_id"] == "tiny_math"
    assert manifest["upstream_commit"] == "abc123"
    assert manifest["generated_by"] == "CAP-FORGE-005"


def test_the_generated_adapter_has_the_fabric_contract(tmp_path):
    generated = fa.generate(library_plan(), tmp_path / "out",
                            upstream_commit="abc123")
    write_tiny_math(generated.root)
    adapter = load(generated.adapter)

    assert adapter.DESCRIPTOR.id == "tiny_math"
    assert adapter.DESCRIPTOR.integration_mode == provider_states.ADAPTER
    assert adapter.DESCRIPTOR.commit == "abc123"
    sys.path.insert(0, str(generated.root))
    try:
        assert adapter.health(None)["state"] == provider_states.READY
        assert adapter.DESCRIPTOR.operations == ("representative_call",)
        assert adapter.call("representative_call", None)["result"] == 7
    finally:
        sys.path.remove(str(generated.root))


def test_the_generated_adapter_reports_unavailable_when_the_import_fails(tmp_path):
    generated = fa.generate(library_plan(), tmp_path / "out",
                            upstream_commit="abc123")
    sys.modules.pop("tiny_math", None)
    adapter = load(generated.adapter)

    health = adapter.health(None)

    assert health["state"] == provider_states.UNAVAILABLE
    assert "tiny_math" in health["detail"]


def test_generated_tests_run_in_quarantine_before_admission(tmp_path):
    FakeQuarantine.instances.clear()
    generated = fa.generate(library_plan(), tmp_path / "out",
                            upstream_commit="abc123")
    decision = fa.run_generated_tests(
        generated,
        store=object(),
        run_id="run-1",
        quarantine_root=tmp_path / "quarantine",
        quarantine_factory=FakeQuarantine,
        isolation="tripwire",
    )

    assert decision.admitted is True
    assert decision.strength == "TEST_QUARANTINE"
    assert decision.exit_code == 0
    assert decision.command == ("python", "test_tiny_math_adapter.py")
    assert decision.denials == ()
    [quarantine] = FakeQuarantine.instances
    assert quarantine.acquired == generated.root
    assert quarantine.runs == [
        ("test_tiny_math_adapter.py", (), False, 60.0)]


def test_generated_tests_run_in_the_real_quarantine_without_pytest(tmp_path):
    generated = fa.generate(library_plan(), tmp_path / "out",
                            upstream_commit="abc123")
    store = Store(tmp_path / "ada.sqlite3")
    isolation = "os" if appcontainer.available() else "tripwire"
    decision = fa.run_generated_tests(
        generated,
        store=store,
        run_id="run-real",
        quarantine_root=tmp_path / "quarantine",
        quarantine_factory=Quarantine,
        isolation=isolation,
    )

    assert decision.admitted is True
    assert decision.exit_code == 0
    assert decision.denials == ()
    assert "No module named pytest" not in decision.stderr


def dotted_plan():
    plan = library_plan()
    manifest = dict(plan.manifest, id="mini_text", name="mini-text",
                    entry_points=["module:mini_text.core:shout"])
    return recipes.IntegrationPlan(
        repo_class=plan.repo_class, path=plan.path,
        fabric_mode=plan.fabric_mode, reason=plan.reason,
        rejected=plan.rejected, manifest=manifest,
        permissions=plan.permissions, tests=plan.tests,
        fallback=plan.fallback, exposure=plan.exposure)


def test_generated_tests_follow_the_recipe_entry_point_not_a_fixed_library_name(tmp_path):
    generated = fa.generate(dotted_plan(), tmp_path / "out",
                            upstream_commit="abc123")
    store = Store(tmp_path / "ada.sqlite3")
    isolation = "os" if appcontainer.available() else "tripwire"

    decision = fa.run_generated_tests(
        generated,
        store=store,
        run_id="run-dotted",
        quarantine_root=tmp_path / "quarantine",
        quarantine_factory=Quarantine,
        isolation=isolation,
    )

    assert decision.admitted is True, decision.stderr
    assert "mini_text.core:shout" in generated.adapter.read_text(encoding="utf-8")


HOSTILE_NAME = 'x"""\nimport os\nMARK1 = 1\n"""'
HOSTILE_ENTRY = 'module:tiny_math:answer"+str(MARK2:=1)+"b->tiny_math:answer'


def hostile_plan(name=HOSTILE_NAME, entry=HOSTILE_ENTRY):
    plan = library_plan()
    manifest = dict(plan.manifest, name=name, entry_points=[entry])
    return recipes.IntegrationPlan(
        repo_class=plan.repo_class, path=plan.path,
        fabric_mode=plan.fabric_mode, reason=plan.reason,
        rejected=plan.rejected, manifest=manifest,
        permissions=plan.permissions, tests=plan.tests,
        fallback=plan.fallback, exposure=plan.exposure)


def test_untrusted_dossier_text_never_becomes_generated_code(tmp_path):
    import ast

    generated = fa.generate(hostile_plan(), tmp_path / "out",
                            upstream_commit="abc123")
    source = generated.adapter.read_text(encoding="utf-8")
    tree = ast.parse(source)

    names = {t.id for node in tree.body if isinstance(node, ast.Assign)
             for t in node.targets if isinstance(t, ast.Name)}
    assert "MARK1" not in names
    assert not any(isinstance(n, ast.NamedExpr) for n in ast.walk(tree))
    top_level = [type(n).__name__ for n in tree.body]
    assert top_level == ["Expr", "ImportFrom", "Import", "ImportFrom",
                         "Assign", "Assign", "Assign", "Assign",
                         "FunctionDef", "FunctionDef", "FunctionDef",
                         "FunctionDef", "FunctionDef"]

    write_tiny_math(generated.root)
    adapter = load(generated.adapter)
    assert adapter.DESCRIPTOR.upstream == HOSTILE_NAME
    assert HOSTILE_ENTRY in adapter.DESCRIPTOR.notes
    assert adapter._ENTRY_SOURCE == HOSTILE_ENTRY
    assert not hasattr(adapter, "MARK1")


def test_a_trailing_newline_in_an_identifier_is_refused(tmp_path):
    import pytest

    plan = library_plan()
    bad_id = recipes.IntegrationPlan(
        repo_class=plan.repo_class, path=plan.path,
        fabric_mode=plan.fabric_mode, reason=plan.reason,
        rejected=plan.rejected, manifest=dict(plan.manifest, id="tiny_math\n"),
        permissions=plan.permissions, tests=plan.tests,
        fallback=plan.fallback, exposure=plan.exposure)
    with pytest.raises(fa.ForgeAdapterError):
        fa.generate(bad_id, tmp_path / "out", upstream_commit="abc123")
