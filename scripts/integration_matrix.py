"""
scripts/integration_matrix.py -- the integration status matrix, generated.

`docs/integrations/THIRD_PARTY_INTEGRATION_MATRIX.md` documented 21 upstreams
while 41 were cloned. It drifted because it was typed, and a typed table of 41
rows drifts again. `scripts/upstream_lock.py` already proves the pattern for
pins; this does the same for integration status, joining three sources it can
read rather than trusting a human to keep them aligned:

  clone directories       third_party/upstream/*
  pins and licences       third_party/UPSTREAM_LOCK.json
  mode and operations     fabric.registry()

The important half is `--check`. It fails when a clone exists with neither a
descriptor nor an explicit REFERENCE_ONLY demotion, because the UNCLASSIFIED
state is what let 27 clones accumulate unnoticed. Wire it into the gate and the
gap cannot reopen quietly.

Usage:
    python scripts/integration_matrix.py            # write the matrix
    python scripts/integration_matrix.py --check    # fail on drift
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "third_party" / "upstream"
LOCK = ROOT / "third_party" / "UPSTREAM_LOCK.json"
OUT = ROOT / "docs" / "integrations" / "INTEGRATION_STATUS.md"

#: Clones deliberately kept for their patterns and never executed. An entry
#: here is a decision with a reason attached, which is the whole difference
#: between "demoted" and "forgotten".
REFERENCE_ONLY: dict[str, str] = {
    "munder-difflin": "benchmark fixtures; read for shape, never run",
    "openviking": "reference implementation; no operation Friday needs yet",
    "ultron": "read for its planner structure only",
    "vane": "read for its scheduler patterns only",
    # Decided 2026-09-02, one reason each. A whole application with its own
    # agent loop and UI is not a capability Friday can call; it is a second
    # control layer (NON_NEGOTIABLE 1). Its patterns are read; its code is
    # not imported. Each can be promoted to SIDECAR/SVC when a concrete
    # objective needs it and the service contract (FABRIC-SVC-01) is up.
    "openhands": "control layer with its own agent loop and UI; Friday is the "
                 "single orchestrator. The pinned clone is agent-canvas "
                 "(TypeScript UI), not a callable worker",
    "crewai": "orchestration framework; Friday owns orchestration. Its role "
              "patterns feed the roles family via role_recipes",
    "cline": "OPTIONAL_WORKER in executor_router.KNOWN (its own registry, "
             "its own lifecycle); a fabric descriptor too would be two "
             "registrations of one thing - test_upstream_lock forbids it",
    "firstmate": "an agent distro: shell/skill conventions, no single "
                 "runnable entry point at the pin (bin/ is a set of hooks)",
    "browser-use": "Friday has one browser policy (browser_capability + "
                   "netguard); a second driver duplicates it. Read for its "
                   "DOM-extraction patterns",
    "nodriver": "AGPL browser driver; same one-browser rule as browser-use",
    "agentmemory": "Apache-2.0 memory over MCP; NON_NEGOTIABLE 11 (no "
                   "duplicate memories) - measure vs store.py before adopting",
    "anythingllm": "MIT app with an AGPL subtree; a whole RAG workspace app, "
                   "not a capability; GBrain is the memory",
    "bolt.diy": "standalone app on WebContainer (commercial licence for "
                "production); Friday builds via Hermes instead",
    "onlook": "visual code editor app; read for design-to-code patterns",
    "open-lovable": "app-cloning web app; Friday's ui_browser.study_url "
                    "covers the useful half natively",
    "open-notebook": "notebook web app; no research-notebook objective yet",
    "openwork": "MIT core + EE subtree; its MCP model is what the fabric "
                "already is",
    "pipecat": "voice pipeline framework; LiveKit is Friday's voice path",
    "maxun": "AGPL, five services incl. a browser; needs FABRIC-SVC-01 and "
             "an extraction objective before a SIDECAR is worth owning",
    "openmontage": "AGPL media app; no media pipeline objective yet",
    "postiz": "AGPL social scheduler; no social objective yet",
}


def clones() -> list[str]:
    if not UPSTREAM.is_dir():
        return []
    return sorted(d.name for d in UPSTREAM.iterdir() if d.is_dir())


def lock() -> dict:
    if not LOCK.is_file():
        return {}
    try:
        raw = json.loads(LOCK.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(raw, dict) and "upstreams" in raw:
        raw = raw["upstreams"]
    if isinstance(raw, list):
        return {row.get("name", ""): row for row in raw if isinstance(row, dict)}
    return raw if isinstance(raw, dict) else {}


def providers() -> dict:
    """upstream name -> the providers built on it."""
    from friday import fabric
    out: dict[str, list] = {}
    for provider in fabric.registry().values():
        if provider.upstream:
            out.setdefault(provider.upstream, []).append(provider)
    return out


def survey() -> dict:
    """The join. Every clone lands in exactly one of three buckets."""
    built = providers()
    pins = lock()
    rows, unclassified = [], []
    for name in clones():
        pin = pins.get(name, {})
        mine = built.get(name, [])
        if mine:
            status = "INTEGRATED"
            modes = sorted({p.integration_mode for p in mine})
            detail = ", ".join(f"{p.id} ({p.integration_mode})" for p in mine)
        elif name in REFERENCE_ONLY:
            status = "REFERENCE_ONLY"
            modes = ["REFERENCE_ONLY"]
            detail = REFERENCE_ONLY[name]
        else:
            status = "UNCLASSIFIED"
            modes = []
            detail = "no descriptor and no demotion"
            unclassified.append(name)
        rows.append({
            "upstream": name,
            "license": pin.get("license", pin.get("licence", "?")),
            "commit": (pin.get("commit") or "")[:12] or "unpinned",
            "modes": modes,
            "status": status,
            "detail": detail,
        })
    return {"rows": rows, "unclassified": unclassified,
            "clones": len(rows),
            "integrated": sum(1 for r in rows if r["status"] == "INTEGRATED")}


def render(data: dict) -> str:
    lines = [
        "# Third-party integration status",
        "",
        "**Generated** by `scripts/integration_matrix.py`. Do not hand-edit -",
        "run the script. `--check` fails when a clone has neither a descriptor",
        "nor an explicit REFERENCE_ONLY demotion, because the unclassified",
        "state is what let two thirds of the clones go unnoticed.",
        "",
        f"- clones: **{data['clones']}**",
        f"- integrated: **{data['integrated']}**",
        f"- unclassified: **{len(data['unclassified'])}**",
        "",
        "| Upstream | Licence | Pin | Mode | Status | Detail |",
        "|---|---|---|---|---|---|",
    ]
    for row in data["rows"]:
        lines.append(
            f"| {row['upstream']} | {row['license']} | `{row['commit']}` | "
            f"{', '.join(row['modes']) or '—'} | {row['status']} | {row['detail']} |")
    if data["unclassified"]:
        lines += ["", "## Unclassified", "",
                  "Each needs a descriptor against a mode that can run it, or a",
                  "REFERENCE_ONLY entry in `scripts/integration_matrix.py` with a",
                  "reason:", ""]
        lines += [f"- `{name}`" for name in data["unclassified"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    data = survey()
    if "--check" in argv:
        if data["unclassified"]:
            print("integration matrix: unclassified clones:", file=sys.stderr)
            for name in data["unclassified"]:
                print(f"  - {name}", file=sys.stderr)
            print("give each a descriptor or a REFERENCE_ONLY reason.",
                  file=sys.stderr)
            return 1
        print(f"integration matrix: {data['clones']} clones, all classified.")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(data), encoding="utf-8")
    print(f"wrote {OUT} ({data['clones']} clones, "
          f"{data['integrated']} integrated, "
          f"{len(data['unclassified'])} unclassified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
