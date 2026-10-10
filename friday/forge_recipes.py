"""
Integration recipes: the minimum viable way to bring a repository in.

CAP-FORGE-004. `plan(dossier, classification)` takes what
`repo_classifier` already established about a repository and returns an
`IntegrationPlan`: one path from a fixed ladder, the recipe template for it
(capability manifest, permissions, tests, fallback, exposure) and the reason
every simpler path on the ladder was rejected.

Nothing here is a model call, a read of the tree or a network touch. The
templates are fixed in this file, so a repository can choose among them but
cannot make FRIDAY design a new architecture for it: the same dossier always
gives the same plan. The dossier and classification are untrusted data from a
third-party checkout; the only things copied out of them are strings placed in
the plan as values, and the manifest id is rebuilt from the name to fit
`forge.NAME_RE`.

The ladder runs from the least invasive path that can deliver the capability
to the most: call it natively, wrap its command, speak MCP to it, adapt its
service, drive its web page. `REFERENCE_ONLY` is the floor under all of them:
nothing runs, the tree is only read for patterns (CAP-FORGE-012). The first
path whose condition holds wins, which is what "simplest reliable path" means
here, and the paths before it are the recorded rejections.

Generating the adapter (CAP-FORGE-005), the journey (CAP-FORGE-007) and
admission (CAP-FORGE-008) consume these plans and are not this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from friday import provider_states as ps
from friday import repo_classifier as rc

NATIVE_CALL = "native_call"
CLI_WRAPPER = "cli_wrapper"
MCP_SERVER = "mcp_server"
SERVICE_ADAPTER = "service_adapter"
BROWSER_PATH = "browser_path"
REFERENCE_ONLY = "reference_only"

#: Simplest first; REFERENCE_ONLY is the terminal fallback, not a rung.
PATHS = (NATIVE_CALL, CLI_WRAPPER, MCP_SERVER, SERVICE_ADAPTER, BROWSER_PATH,
         REFERENCE_ONLY)

#: Licences known to permit linking into FRIDAY's process. Native call fails
#: closed: any other label (copyleft or weak-copyleft variants, PolyForm,
#: UNIDENTIFIED, NONE, an unrecognised manifest string) is refused.
LINKABLE_LICENSES = frozenset({"mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause",
                               "isc", "0bsd"})

_SPDX_SPLIT = re.compile(r"\s+(?:or|and)\s+|[()]", re.IGNORECASE)


def may_link_in_process(license_label: str) -> bool:
    """True only when every operand of the (SPDX-style) label is a known permissive licence."""
    operands = [part.strip().casefold() for part in _SPDX_SPLIT.split(license_label or "")]
    operands = [part for part in operands if part]
    return bool(operands) and all(part in LINKABLE_LICENSES for part in operands)

MAX_ENTRY_POINTS = 10
_ID_LENGTH = 40


class ForgeRecipeError(ValueError):
    """The classification given to `plan` is not one this module can plan for."""


@dataclass(frozen=True)
class Recipe:
    """A fixed template. Only the fields a repository can change are filled
    from its dossier (permissions, manifest values); the rest never vary."""
    path: str
    fabric_mode: str
    base_permissions: tuple[str, ...]
    tests: tuple[str, ...]
    fallback: str
    exposure: str


RECIPES: dict[str, Recipe] = {
    NATIVE_CALL: Recipe(
        NATIVE_CALL, ps.ADAPTER, (),
        ("manifest: the capability manifest validates and names its origin class",
         "import: the library imports inside the quarantine sandbox, never in FRIDAY's process first",
         "call: one representative function returns the documented value",
         "health: health() reports UNAVAILABLE, never raises, when the import fails"),
        "report UNAVAILABLE when the import fails; boot and the parent objective carry on",
        "a fabric adapter module (DESCRIPTOR + start/stop/health/call)"),
    CLI_WRAPPER: Recipe(
        CLI_WRAPPER, ps.CLI, ("subprocess",),
        ("manifest: the capability manifest validates and names its origin class",
         "argv: arguments are passed as a list, never through a shell string",
         "run: one representative command exits 0 inside the quarantine sandbox",
         "timeout: a hung command is killed at its timeout and reported",
         "health: health() reports UNAVAILABLE when the executable is absent"),
        "report UNAVAILABLE when the executable is missing; no partial output is trusted after a timeout",
        "a one-shot subprocess provider behind the fabric (mode CLI)"),
    MCP_SERVER: Recipe(
        MCP_SERVER, ps.MCP, ("subprocess",),
        ("manifest: the capability manifest validates and names its origin class",
         "handshake: the server completes an MCP initialize and lists its tools",
         "call: one representative tool returns the documented result",
         "health: health() reports UNAVAILABLE when the server will not start"),
        "report UNAVAILABLE when the server does not start; tools are not offered to planning",
        "a fabric MCP provider; its tools surface only through the capability router"),
    SERVICE_ADAPTER: Recipe(
        SERVICE_ADAPTER, ps.SIDECAR, ("subprocess", "network"),
        ("manifest: the capability manifest validates and names its origin class",
         "bind: the service listens on loopback only",
         "request: one representative request returns the documented response",
         "health: health() reports UNAVAILABLE when the port never opens",
         "stop: stop() ends the process and leaves nothing listening"),
        "report UNAVAILABLE when the port never opens; the process is reaped, not left running",
        "a fabric sidecar provider reached over loopback"),
    BROWSER_PATH: Recipe(
        BROWSER_PATH, ps.SIDECAR, ("subprocess", "network"),
        ("manifest: the capability manifest validates and names its origin class",
         "serve: the app builds and serves on loopback from the quarantine sandbox",
         "journey: one representative flow completes in the browser with the post-state read back independently",
         "health: health() reports UNAVAILABLE when the page does not load"),
        "report UNAVAILABLE when the page does not load; no flow is attempted against a half-loaded page",
        "a fabric sidecar provider driven through the browser capability"),
    REFERENCE_ONLY: Recipe(
        REFERENCE_ONLY, ps.REFERENCE_ONLY, (),
        ("manifest: the record names the repository, its class and why nothing runs",
         "inert: nothing from the tree is imported, executed or installed"),
        "none needed: nothing runs; patterns may be extracted without installing (CAP-FORGE-012)",
        "none: recorded for reading only, never offered to planning as a tool"),
}


@dataclass(frozen=True)
class IntegrationPlan:
    """`rejected` lists, in ladder order, every simpler path and why it does
    not apply; the chosen path is never in it."""
    repo_class: str
    path: str
    fabric_mode: str
    reason: str
    rejected: tuple[tuple[str, str], ...]
    manifest: dict
    permissions: tuple[str, ...]
    tests: tuple[str, ...]
    fallback: str
    exposure: str

    def to_dict(self) -> dict:
        return {
            "repo_class": self.repo_class,
            "path": self.path,
            "fabric_mode": self.fabric_mode,
            "reason": self.reason,
            "rejected": [{"path": p, "reason": r} for p, r in self.rejected],
            "manifest": dict(self.manifest),
            "permissions": list(self.permissions),
            "tests": list(self.tests),
            "fallback": self.fallback,
            "exposure": self.exposure,
        }


def _native_call(d: rc.Dossier, c: rc.Classification) -> str | None:
    if c.repo_class != rc.LIBRARY:
        return f"classified {c.repo_class}, not an importable library"
    if may_link_in_process(d.license):
        return None
    if "gpl" in d.license.casefold():
        return (f"{d.license} is copyleft: the fabric forbids linking it into "
                "FRIDAY's process")
    return (f"licence {d.license!r} is not known to permit linking it into "
            "FRIDAY's process")


def _cli_wrapper(d: rc.Dossier, c: rc.Classification) -> str | None:
    if c.repo_class != rc.CLI:
        return f"classified {c.repo_class}, so its supported interface is not a command"
    return None


def _mcp_server(d: rc.Dossier, c: rc.Classification) -> str | None:
    if c.repo_class != rc.MCP:
        return f"classified {c.repo_class}, not an MCP server"
    return None


def _service_adapter(d: rc.Dossier, c: rc.Classification) -> str | None:
    if c.repo_class != rc.SERVICE:
        return f"classified {c.repo_class}, not a service that listens for requests"
    return None


def _web_only(d: rc.Dossier) -> bool:
    kinds = {signal.split(":", 1)[0] for signal in d.app_signals}
    return "frontend" in kinds and not kinds & {"gui", "mobile"}


def _browser_path(d: rc.Dossier, c: rc.Classification) -> str | None:
    if c.repo_class != rc.APPLICATION:
        return f"classified {c.repo_class}, not an application with a user interface"
    if not _web_only(d):
        return "its interface is a desktop or mobile window, not a page a browser can reach"
    return None


#: Why nothing runs, when REFERENCE_ONLY is reached. Keyed by class.
_REFERENCE_REASONS = {
    rc.SKILL: "a skill is a prompt, not a program; it enters through the skill lifecycle, not as running code",
    rc.CAPABILITY_PACK: "a pack bundles members that are classified and planned on their own",
    rc.MODEL: "model weights are data; running them is a model-gateway decision, not an integration",
    rc.RESEARCH_CORPUS: "a text corpus is never executable",
    rc.REFERENCE_ONLY: "ambiguous or unlicensed: it may be read for patterns but not used",
    rc.APPLICATION: "a desktop or mobile application has no path among the fixed recipes",
    rc.LIBRARY: "its licence does not permit in-process linking (or is unknown) and it exposes no isolated interface",
}

_CONDITIONS = (
    (NATIVE_CALL, _native_call),
    (CLI_WRAPPER, _cli_wrapper),
    (MCP_SERVER, _mcp_server),
    (SERVICE_ADAPTER, _service_adapter),
    (BROWSER_PATH, _browser_path),
)

_CHOSEN_REASONS = {
    NATIVE_CALL: "an importable library with a licence that may be linked",
    CLI_WRAPPER: "a command-line tool: its command is the supported interface",
    MCP_SERVER: "an MCP server: it already speaks the protocol the fabric supports",
    SERVICE_ADAPTER: "a service: it is reached over its own listening port",
    BROWSER_PATH: "a web-only application: a browser page is its only surface",
}


def manifest_id(name: str) -> str:
    """A fabric-safe id from an untrusted repository name (`forge.NAME_RE`)."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"repo_{slug}".strip("_")
    return slug[:_ID_LENGTH].rstrip("_").ljust(3, "x")


def plan(dossier: rc.Dossier, classification: rc.Classification) -> IntegrationPlan:
    """
    Pure and deterministic. The first path on the ladder whose condition holds
    is chosen; every earlier path is recorded as rejected with its reason.
    Native call, the only non-isolated path, fails closed: it needs a known
    permissive licence, so copyleft variants and unknown licences end on an
    isolated mode.
    """
    if classification.repo_class not in rc.CLASSES:
        raise ForgeRecipeError(
            f"cannot plan for class {classification.repo_class!r}; "
            f"expected one of {', '.join(rc.CLASSES)}")
    rejected: list[tuple[str, str]] = []
    for path, condition in _CONDITIONS:
        refusal = condition(dossier, classification)
        if refusal is None:
            chosen, reason = path, _CHOSEN_REASONS[path]
            break
        rejected.append((path, refusal))
    else:
        # Cli, MCP and service always satisfy their own rung, so only the
        # classes in _REFERENCE_REASONS can reach the floor.
        chosen, reason = REFERENCE_ONLY, _REFERENCE_REASONS[classification.repo_class]
    recipe = RECIPES[chosen]
    permissions = (tuple(sorted(set(recipe.base_permissions) | set(dossier.permissions)))
                   if chosen != REFERENCE_ONLY else ())
    manifest = {
        "id": manifest_id(dossier.name),
        "name": dossier.name,
        "origin_class": classification.repo_class,
        "integration_path": chosen,
        "fabric_mode": recipe.fabric_mode,
        "license": dossier.license,
        "entry_points": list(dossier.entry_points[:MAX_ENTRY_POINTS]),
    }
    return IntegrationPlan(
        repo_class=classification.repo_class, path=chosen,
        fabric_mode=recipe.fabric_mode, reason=reason,
        rejected=tuple(rejected), manifest=manifest, permissions=permissions,
        tests=recipe.tests, fallback=recipe.fallback, exposure=recipe.exposure)
