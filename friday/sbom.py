"""
The Agent Capability SBOM (PRD v7 SUPPLY-001).

One list of everything outside code we wrote that an agent can run on, each
component with where it came from, which version, and what has been done to it.
It joins what was already recorded in separate places:

- upstream repositories and model weights: third_party/UPSTREAM_LOCK.json
- installed Python packages: importlib.metadata
- MCP servers: the firewall's pins (friday.mcp_firewall) plus the servers
  Hermes is configured with
- CLIs: the capability fabric's providers in CLI mode

`scan_state` says only what is true. AUDITED: pinned to an immutable commit with
a licence decision. SCANNED: an MCP server whose tool descriptions the firewall
has captured and scanned. FLAGGED: suspended by the firewall or carrying a
licence warning. UNSCANNED: nothing has looked at it - every Python package is
this until a vulnerability scanner is wired in, and the SBOM does not pretend
otherwise. Credentials, headers, environment and command arguments are never
read into it.

    python -m friday.sbom [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import pathlib
from importlib import metadata

AUDITED = "AUDITED"
SCANNED = "SCANNED"
FLAGGED = "FLAGGED"
UNSCANNED = "UNSCANNED"
SCAN_STATES = (AUDITED, SCANNED, FLAGGED, UNSCANNED)

#: Firewall states, by what they say about whether the server has been looked at.
_MCP_SCANNED = {"ACTIVE", "EXTERNAL_UNVERIFIED", "VERIFIED_PENDING_APPROVAL", "REVALIDATING"}
_MCP_FLAGGED = {"SUSPENDED"}

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "third_party" / "UPSTREAM_LOCK.json"
UNKNOWN = "unknown"


def _component(kind: str, name: str, source: str, version: str, scan_state: str,
               **extra) -> dict:
    return {"kind": kind, "name": name, "source": source or UNKNOWN,
            "version": version or UNKNOWN, "scan_state": scan_state, **extra}


def _from_lock(lock: dict) -> list[dict]:
    out = []
    for name, row in lock.items():
        model = row.get("status") == "MODEL_PINNED"
        if row.get("license_warning"):
            state = FLAGGED
        elif row.get("commit") and row.get("license_verified"):
            state = AUDITED
        else:
            state = UNSCANNED
        out.append(_component(
            "model" if model else "upstream", name, row.get("url", ""),
            row.get("version") or str(row.get("commit", ""))[:12], state,
            commit=row.get("commit", ""), license=row.get("license_verified", ""),
            active=bool(row.get("provider_id"))))
    return out


def _mcp_source(entry: dict) -> str:
    """Where a server points - an endpoint or the executable's name. Never its
    arguments, which can carry a token."""
    for part in ("binding", "declared"):
        binding = entry.get(part) or {}
        if binding.get("endpoint"):
            return binding["endpoint"]
        if binding.get("command"):
            return str(binding["command"][0])
    return entry.get("source", "")


def _hermes_source(spec: dict) -> str:
    from friday.mcp_firewall import endpoint_of

    spec = spec if isinstance(spec, dict) else {}
    if isinstance(spec.get("url"), str):
        return endpoint_of(spec["url"])
    return str(spec["command"]) if isinstance(spec.get("command"), str) else ""


def _from_mcp(pins: dict, hermes: dict) -> list[dict]:
    out = []
    for key, entry in sorted(pins.items()):
        state = entry.get("state")
        scan = (SCANNED if state in _MCP_SCANNED
                else FLAGGED if state in _MCP_FLAGGED else UNSCANNED)
        out.append(_component(
            "mcp_server", key, _mcp_source(entry),
            (entry.get("binding") or {}).get("package_version", ""), scan,
            firewall_state=state or UNKNOWN))
    for name, spec in sorted(hermes.items(), key=lambda item: str(item[0])):
        key = f"hermes:{name}"
        if key not in pins:
            out.append(_component("mcp_server", key, _hermes_source(spec), "", UNSCANNED,
                                  firewall_state="NOT_DISCOVERED"))
    return out


def _from_providers(providers) -> list[dict]:
    return [_component("cli", p.id, p.upstream, p.version or str(p.commit)[:12],
                       AUDITED if p.commit else UNSCANNED, commit=p.commit)
            for p in providers if p.integration_mode == "CLI"]


def build(*, lock: dict, packages, pins: dict, hermes: dict, providers) -> dict:
    """The SBOM for the given inputs (see `collect` for the real ones)."""
    components = _from_lock(lock)
    components += [_component("package", name, "pypi", version, UNSCANNED)
                   for name, version in packages]
    components += _from_mcp(pins, hermes)
    components += _from_providers(providers)
    by_kind: dict[str, int] = {}
    by_state: dict[str, int] = {}
    for component in components:
        by_kind[component["kind"]] = by_kind.get(component["kind"], 0) + 1
        by_state[component["scan_state"]] = by_state.get(component["scan_state"], 0) + 1
    return {"components": components,
            "summary": {"total": len(components), "by_kind": by_kind,
                        "by_scan_state": by_state}}


def installed_packages() -> list[tuple[str, str]]:
    found = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            found[name.lower().replace("_", "-")] = dist.version
    return sorted(found.items())


def _hermes_servers() -> dict:
    import yaml

    from friday.mcp_firewall import hermes_config_path

    try:
        data = yaml.safe_load(hermes_config_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, yaml.YAMLError):
        return {}
    table = data.get("mcp_servers") if isinstance(data, dict) else None
    return table if isinstance(table, dict) else {}


def collect() -> dict:
    """The SBOM of this machine, read from the lock, the venv, the firewall's
    pins, Hermes' config and the fabric. Nothing is started or called."""
    from friday import fabric, mcp_firewall

    try:
        pins = json.loads(mcp_firewall.default().path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pins = {}
    return build(lock=json.loads(LOCK_PATH.read_text(encoding="utf-8")),
                 packages=installed_packages(), pins=pins, hermes=_hermes_servers(),
                 providers=fabric.registry().values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", help="write the SBOM here instead of stdout")
    arguments = parser.parse_args(argv)
    rendered = json.dumps(collect(), indent=2, sort_keys=True) + "\n"
    if arguments.out:
        pathlib.Path(arguments.out).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
