"""
Generate the smallest fabric adapter a forge recipe calls for.

CAP-FORGE-004 decides the integration path. This module consumes that
IntegrationPlan and writes three caller-owned artifacts:

* a fabric adapter module with DESCRIPTOR + start/stop/health/call
* a stdlib unittest file that validates that adapter contract
* a manifest tying the generated source back to the recipe and pinned commit

Generated source is not admitted by generation alone. `run_generated_tests`
runs only the generated unittest file (executed with python) through the repository quarantine and
returns the evidence the caller can use for the later admission step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from friday import forge_recipes, provider_states

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_REPO_ROOT = Path(__file__).resolve().parent.parent
_FABRIC_ADAPTERS = (_REPO_ROOT / "friday" / "fabric_adapters").resolve()
_GENERATED_BY = "CAP-FORGE-005"


class ForgeAdapterError(ValueError):
    """The recipe cannot be turned into a generated adapter safely."""


@dataclass(frozen=True)
class GeneratedAdapter:
    """The three files written for one generated adapter."""

    provider_id: str
    root: Path
    adapter: Path
    tests: Path
    manifest: Path


@dataclass(frozen=True)
class AdmissionTestResult:
    """The quarantine evidence from running the generated tests."""

    admitted: bool
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    denials: tuple[dict, ...]
    strength: str


@dataclass(frozen=True)
class LibraryEntryPoint:
    """The import target selected from a CAP-FORGE-004 library recipe."""

    module: str
    attribute: str
    source: str


def generate(plan: forge_recipes.IntegrationPlan, output_dir: str | Path, *,
             upstream_commit: str) -> GeneratedAdapter:
    """
    Write a minimal adapter, its generated tests and manifest to `output_dir`.

    The directory is supplied by the caller so tests and dry runs never write
    into `friday/fabric_adapters/`. For a native library recipe the generated
    adapter imports the recipe entry point, exposes one representative call,
    and reports UNAVAILABLE instead of raising when that import is absent.
    """
    provider_id = str(plan.manifest.get("id", ""))
    _validate_provider_id(provider_id)
    if plan.path == forge_recipes.REFERENCE_ONLY:
        raise ForgeAdapterError("reference-only plans do not get runnable adapters")
    if not upstream_commit:
        raise ForgeAdapterError("a generated adapter needs a pinned upstream commit")

    root = Path(output_dir).resolve()
    _refuse_product_adapter_dir(root)
    root.mkdir(parents=True, exist_ok=True)

    generated = GeneratedAdapter(
        provider_id=provider_id,
        root=root,
        adapter=root / f"{provider_id}_adapter.py",
        tests=root / f"test_{provider_id}_adapter.py",
        manifest=root / f"{provider_id}_manifest.json",
    )
    generated.adapter.write_text(
        _adapter_source(plan, upstream_commit), encoding="utf-8")
    generated.tests.write_text(
        _test_source(provider_id, _library_entry_point(plan)), encoding="utf-8")
    generated.manifest.write_text(
        json.dumps(_manifest(plan, upstream_commit, generated),
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return generated


def run_generated_tests(generated: GeneratedAdapter, *, store, run_id: str,
                        quarantine_root: str | Path, quarantine_factory=None,
                        isolation: str = "os",
                        timeout: float = 60.0) -> AdmissionTestResult:
    """
    Run the generated unittest file with python in a quarantine and return admission evidence.

    The only success condition is strict: the unittest run exits 0 and the quarantine
    records no denied actions. Anything else is returned as non-admitted data.
    """
    if quarantine_factory is None:
        from friday.quarantine import Quarantine
        quarantine_factory = Quarantine
    command = ("python", generated.tests.name)
    with quarantine_factory(store=store, run_id=run_id,
                            base=quarantine_root,
                            isolation=isolation) as q:
        q.acquire(generated.root)
        analysis = q.run_python(generated.tests.name, timeout=timeout)
    result = analysis.result
    denials = tuple(_denial_dict(d) for d in analysis.denials)
    return AdmissionTestResult(
        admitted=result.exit_code == 0 and not denials,
        command=command,
        exit_code=int(result.exit_code),
        stdout=str(getattr(result, "stdout", "")),
        stderr=str(getattr(result, "stderr", "")),
        denials=denials,
        strength=str(analysis.strength),
    )


def _validate_provider_id(provider_id: str) -> None:
    if not _NAME_RE.fullmatch(provider_id):
        raise ForgeAdapterError(f"invalid provider id {provider_id!r}")


def _refuse_product_adapter_dir(root: Path) -> None:
    if root == _FABRIC_ADAPTERS or _FABRIC_ADAPTERS in root.parents:
        raise ForgeAdapterError(
            "generated adapters must be written to a caller-owned directory")


def _license_mode(license_label: str) -> str:
    if "gpl" in license_label.casefold():
        return provider_states.COPYLEFT
    return provider_states.PERMISSIVE


def _risk(permissions: tuple[str, ...]) -> str:
    return "medium" if permissions else "low"


_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_ATTR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _library_entry_point(plan: forge_recipes.IntegrationPlan) -> LibraryEntryPoint:
    if plan.path != forge_recipes.NATIVE_CALL:
        raise ForgeAdapterError(
            f"CAP-FORGE-005 only generates native library adapters, not {plan.path!r}")
    for raw in plan.manifest.get("entry_points", ()):
        source = str(raw)
        target = source.split("->", 1)[1] if "->" in source else source
        if target.startswith("module:"):
            target = target.split(":", 1)[1]
        module, sep, attribute = target.partition(":")
        module = module.strip()
        attribute = attribute.strip()
        if _MODULE_RE.fullmatch(module) and (not attribute or _ATTR_RE.fullmatch(attribute)):
            return LibraryEntryPoint(module=module, attribute=attribute, source=source)
    raise ForgeAdapterError("native library plans need an importable entry point")


def _adapter_source(plan: forge_recipes.IntegrationPlan,
                    upstream_commit: str) -> str:
    provider_id = str(plan.manifest["id"])
    upstream = str(plan.manifest["name"])
    permissions = tuple(str(p) for p in plan.permissions)
    entry = _library_entry_point(plan)
    notes = (f"generated by {_GENERATED_BY} from forge recipe {str(plan.path)}; "
             f"imports {entry.source} and exposes one representative call")
    source = f'''"""
Generated fabric adapter.

The upstream name and entry point are untrusted dossier text, so they appear
below only as repr()-encoded values, never inside this docstring.

Generated by {_GENERATED_BY}; do not hand-edit this file. Regenerate it from
the forge recipe so the manifest and tests stay in lockstep.
"""

from __future__ import annotations

import importlib

from friday import fabric


_ENTRY_MODULE = {entry.module!r}
_ENTRY_ATTRIBUTE = {entry.attribute!r}
_ENTRY_SOURCE = {entry.source!r}


DESCRIPTOR = fabric.Provider(
    id={provider_id!r},
    family="diagnostic",
    upstream={upstream!r},
    operations=("representative_call",),
    risk={_risk(permissions)!r},
    license_mode={_license_mode(str(plan.manifest.get("license", "")))!r},
    integration_mode={str(plan.fabric_mode)!r},
    permissions={permissions!r},
    commit={upstream_commit!r},
    notes={notes!r},
)


def _load_entry():
    module = importlib.import_module(_ENTRY_MODULE)
    if not _ENTRY_ATTRIBUTE:
        return module
    return getattr(module, _ENTRY_ATTRIBUTE)


def start():
    return {{"entry_point": _ENTRY_SOURCE}}


def stop(handle):
    return None


def health(handle):
    try:
        _load_entry()
    except Exception as exc:  # noqa: BLE001 - health reports, never raises
        return {{"state": fabric.UNAVAILABLE,
                 "detail": f"{{_ENTRY_SOURCE}} unavailable: {{exc}}"}}
    return {{"state": fabric.READY,
             "detail": f"{{_ENTRY_SOURCE}} imported"}}


def call(operation, handle, **arguments):
    if operation != "representative_call":
        raise ValueError(f"{{DESCRIPTOR.id}} does not implement {{operation!r}}")
    entry = _load_entry()
    result = entry() if callable(entry) else {{"module": getattr(entry, "__name__", str(entry))}}
    return {{"provider": DESCRIPTOR.id, "operation": operation,
            "entry_point": _ENTRY_SOURCE, "result": result}}
'''
    return source


def _test_source(provider_id: str, entry: LibraryEntryPoint) -> str:
    module_name = f"{provider_id}_adapter"
    return f'''from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types
import unittest


READY = "READY"
UNAVAILABLE = "UNAVAILABLE"


class Provider:
    def __init__(self, **values):
        self.__dict__.update(values)


def install_fabric_harness():
    fabric = types.ModuleType("friday.fabric")
    fabric.Provider = Provider
    fabric.READY = READY
    fabric.UNAVAILABLE = UNAVAILABLE
    fabric.ADAPTER = "ADAPTER"
    fabric.COPYLEFT = "COPYLEFT"
    fabric.PERMISSIVE = "PERMISSIVE"
    friday = types.ModuleType("friday")
    friday.fabric = fabric
    sys.modules["friday"] = friday
    sys.modules["friday.fabric"] = fabric


ENTRY_MODULE = {entry.module!r}
ENTRY_ATTRIBUTE = {entry.attribute!r}


def write_library():
    """A stand-in for the upstream library, so the contract is proven offline."""
    parts = ENTRY_MODULE.split(".")
    package = Path()
    for part in parts[:-1]:
        package = package / part
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    body = "def " + ENTRY_ATTRIBUTE + "():\\n    return 7\\n" if ENTRY_ATTRIBUTE else ""
    (package / (parts[-1] + ".py")).write_text(body, encoding="utf-8")
    importlib.invalidate_caches()


install_fabric_harness()
write_library()
import {module_name} as adapter


class GeneratedAdapterContract(unittest.TestCase):
    def test_descriptor_is_a_fabric_provider(self):
        self.assertIsInstance(adapter.DESCRIPTOR, Provider)
        self.assertEqual(adapter.DESCRIPTOR.id, {provider_id!r})
        self.assertEqual(adapter.DESCRIPTOR.operations, ("representative_call",))

    def test_health_and_representative_call_use_the_entry_point(self):
        self.assertEqual(adapter.health(None)["state"], READY)
        result = adapter.call("representative_call", None)
        self.assertEqual(result["provider"], {provider_id!r})
        if ENTRY_ATTRIBUTE:
            self.assertEqual(result["result"], 7)
        else:
            self.assertEqual(result["result"], {{"module": ENTRY_MODULE}})

    def test_health_reports_unavailable_when_the_import_fails(self):
        original = adapter._ENTRY_MODULE
        try:
            adapter._ENTRY_MODULE = "__missing_" + ENTRY_MODULE.replace(".", "_")
            health = adapter.health(None)
        finally:
            adapter._ENTRY_MODULE = original
        self.assertEqual(health["state"], UNAVAILABLE)
        self.assertIn("__missing_", health["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
'''


def _manifest(plan: forge_recipes.IntegrationPlan, upstream_commit: str,
              generated: GeneratedAdapter) -> dict:
    return {
        "generated_by": _GENERATED_BY,
        "provider_id": generated.provider_id,
        "adapter": generated.adapter.name,
        "tests": generated.tests.name,
        "upstream_commit": upstream_commit,
        "recipe": plan.to_dict(),
    }


def _denial_dict(denial) -> dict:
    if hasattr(denial, "as_dict"):
        return denial.as_dict()
    return dict(denial)
