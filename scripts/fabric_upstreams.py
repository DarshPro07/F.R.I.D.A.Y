"""
Clone, pin and audit the 21 upstream repositories. Reproducible, not manual.

Run:  .venv/Scripts/python.exe scripts/fabric_upstreams.py clone
      .venv/Scripts/python.exe scripts/fabric_upstreams.py audit
      .venv/Scripts/python.exe scripts/fabric_upstreams.py status

## Why a script rather than 21 `git clone` lines

The build pack asks for the same twelve facts about every repository - SHA,
license, install path, tests, Windows compatibility, secrets, network, background
processes, privilege, overlap, integration mode. Twenty-one repositories times
twelve facts done by hand is where the twelfth repository quietly gets four of
them. Derived facts are checked the same way every time or they are folklore.

Clones are `--depth 1`. That keeps the *full source tree* at an exact commit,
which is what NON_NEGOTIABLE 7 is about, and skips history we would never read.
`git fetch --depth 1 origin <sha>` remains the update path, and `--unshallow`
is there if an audit ever needs the history. See DECISION_LEDGER D-004.

Nothing here installs anything or runs upstream code. Auditing an untrusted
repository by executing it is the mistake this stage exists to avoid.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "third_party" / "upstream"
LICENSES = ROOT / "third_party" / "licenses"
LOCK = ROOT / "third_party" / "UPSTREAM_LOCK.json"

#: name -> (url, declared license, integration mode, family, why we want it)
#:
#: The declared license is what the research matrix says; `audit` re-reads the
#: LICENSE file from the clone and records a mismatch rather than trusting this.
REPOS: dict[str, tuple[str, str, str, str, str]] = {
    "scrapling": (
        "https://github.com/D4Vinci/Scrapling", "BSD-3-Clause",
        "ADAPTER", "scraping", "adaptive deterministic extraction"),
    "browser-use": (
        "https://github.com/browser-use/browser-use", "MIT",
        "ADAPTER", "browser", "interactive browser agent"),
    "agent-reach": (
        "https://github.com/Panniantong/agent-reach", "MIT",
        "MCP", "research", "platform-specific source access"),
    "vane": (
        "https://github.com/ItzCrazyKns/Vane", "MIT",
        "SIDECAR", "search", "private metasearch"),
    "maxun": (
        "https://github.com/getmaxun/maxun", "AGPL-3.0",
        "SIDECAR", "scraping", "persistent no-code scraping robots"),
    "graft": (
        "https://github.com/trailhq/Graft", "MIT",
        "ADAPTER", "code_intelligence", "concept/code context"),
    "codebase-memory-mcp": (
        "https://github.com/DeusData/codebase-memory-mcp", "MIT",
        "MCP", "code_intelligence", "exact structural code graph"),
    "gstack": (
        "https://github.com/garrytan/gstack", "MIT",
        "SKILL", "roles", "engineering/QA/CEO skill pack"),
    "openhands": (
        "https://github.com/OpenHands/OpenHands", "MIT core; enterprise/ PolyForm",
        "SIDECAR", "coding", "sandboxed specialist coding agent"),
    "cline": (
        "https://github.com/cline/cline", "Apache-2.0",
        "ADAPTER", "coding", "coding SDK / headless CLI"),
    "open-notebook": (
        "https://github.com/lfnovo/open-notebook", "MIT",
        "MCP", "research", "research notebook workspace"),
    "anythingllm": (
        "https://github.com/Mintplex-Labs/anything-llm", "MIT",
        "SIDECAR", "research", "document/RAG workspace (optional)"),
    "agency-agents": (
        "https://github.com/msitarzewski/agency-agents", "MIT",
        "SKILL", "roles", "lazy role recipes"),
    "no-ai-slop": (
        "https://github.com/petergyang/no-ai-slop", "MIT",
        "SKILL", "writing", "writing finalisation"),
    "i-have-adhd": (
        "https://github.com/ayghri/i-have-adhd", "MIT",
        "SKILL", "presentation", "action-first output mode"),
    "openmontage": (
        "https://github.com/calesthio/OpenMontage", "AGPL-3.0",
        "SIDECAR", "media", "video production pipeline"),
    "pipecat": (
        "https://github.com/pipecat-ai/pipecat", "BSD-2-Clause",
        "ADAPTER", "voice", "realtime voice (experimental; LiveKit is baseline)"),
    "postiz": (
        "https://github.com/gitroomhq/postiz-app", "AGPL-3.0",
        "SIDECAR", "social", "social scheduling"),
    "strix": (
        "https://github.com/usestrix/strix", "Apache-2.0",
        "SIDECAR", "security", "authorized app security testing"),
    "crewai": (
        "https://github.com/crewAIInc/crewAI", "MIT",
        "REFERENCE_ONLY", "orchestration", "crew/flow patterns"),
    "agenticseek": (
        "https://github.com/Fosowl/agenticSeek", "GPL-3.0",
        "REFERENCE_ONLY", "orchestration", "local assistant patterns"),
}

#: Filenames that mean "this is how you install it".
INSTALL_MARKERS = (
    "pyproject.toml", "setup.py", "requirements.txt", "package.json",
    "Cargo.toml", "go.mod", "Makefile", "docker-compose.yml",
    "docker-compose.yaml", "Dockerfile", "uv.lock", "pnpm-lock.yaml",
)

#: Directory names that mean "this is how you test it".
TEST_MARKERS = ("tests", "test", "__tests__", "spec", "e2e")

#: Grep patterns whose presence is a fact worth recording before any install.
#: These are read from the tree, never executed.
RISK_PATTERNS = {
    "network_telemetry": (
        r"posthog|segment\.io|mixpanel|sentry_sdk|analytics\.track|"
        r"telemetry|opentelemetry"),
    "subprocess_shell": r"shell\s*=\s*True|os\.system\(|child_process",
    "privileged": r"sudo |runas|Administrator|CAP_SYS_ADMIN|--privileged",
    "docker_required": r"docker\.from_env|docker compose|docker-compose",
    "browser_download": r"playwright install|puppeteer|chromium",
}


def run(args: list[str], cwd: pathlib.Path | None = None,
        timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")


# --- clone -----------------------------------------------------------------


def clone_one(name: str) -> dict:
    url = REPOS[name][0]
    target = UPSTREAM / name
    if (target / ".git").exists():
        return {"name": name, "cloned": "already", "path": str(target)}
    UPSTREAM.mkdir(parents=True, exist_ok=True)
    result = run(["git", "clone", "--depth", "1", url, str(target)])
    if result.returncode != 0:
        return {"name": name, "cloned": "failed",
                "error": (result.stderr or "").strip()[-400:]}
    return {"name": name, "cloned": "ok", "path": str(target)}


def clone(names: list[str] | None = None) -> int:
    failures = 0
    for name in names or list(REPOS):
        outcome = clone_one(name)
        print(f"  {name:22} {outcome['cloned']}"
              + (f"  {outcome.get('error','')}" if outcome["cloned"] == "failed" else ""))
        failures += outcome["cloned"] == "failed"
    return failures


# --- audit -----------------------------------------------------------------


def _sha(path: pathlib.Path) -> str:
    return run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()


def _describe(path: pathlib.Path) -> str:
    result = run(["git", "describe", "--tags", "--always"], cwd=path)
    return result.stdout.strip() if result.returncode == 0 else ""


def _license(path: pathlib.Path) -> dict:
    """The license as the clone actually states it, not as the matrix claims."""
    for candidate in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING",
                      "LICENCE", "LICENSE-MIT"):
        found = path / candidate
        if found.exists():
            raw = found.read_bytes()
            head = raw[:4000].decode("utf-8", "replace")
            for marker, spdx in (
                ("GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL-3.0"),
                ("GNU GENERAL PUBLIC LICENSE", "GPL-3.0"),
                ("Apache License", "Apache-2.0"),
                ("BSD 3-Clause", "BSD-3-Clause"),
                ("Redistribution and use in source and binary forms", "BSD"),
                ("MIT License", "MIT"),
                ("Permission is hereby granted, free of charge", "MIT"),
            ):
                if marker.lower() in head.lower():
                    return {"file": candidate, "detected": spdx,
                            "sha256": hashlib.sha256(raw).hexdigest()}
            return {"file": candidate, "detected": "UNRECOGNISED",
                    "sha256": hashlib.sha256(raw).hexdigest()}
    return {"file": None, "detected": "ABSENT", "sha256": ""}


def _tree_facts(path: pathlib.Path) -> dict:
    install = sorted(m for m in INSTALL_MARKERS if (path / m).exists())
    tests = sorted(d.name for d in path.iterdir()
                   if d.is_dir() and d.name in TEST_MARKERS)
    top = sorted(p.name for p in path.iterdir() if not p.name.startswith("."))
    files = 0
    py = js = 0
    for child in path.rglob("*"):
        if ".git" in child.parts or "node_modules" in child.parts:
            continue
        if child.is_file():
            files += 1
            if child.suffix == ".py":
                py += 1
            elif child.suffix in (".ts", ".tsx", ".js", ".jsx"):
                js += 1
    return {"install_markers": install, "test_dirs": tests,
            "top_level": top[:40], "files": files,
            "python_files": py, "js_ts_files": js,
            "has_enterprise_dir": (path / "enterprise").exists()}


def _risks(path: pathlib.Path) -> dict:
    """Grep the tree for facts that change how we may run it. Never executes."""
    out = {}
    for label, pattern in RISK_PATTERNS.items():
        result = run(["git", "grep", "-l", "-I", "-E", "-i", pattern, "HEAD"],
                     cwd=path, timeout=180)
        hits = [line.split(":", 1)[-1]
                for line in result.stdout.splitlines() if line.strip()]
        out[label] = {"hit": bool(hits), "count": len(hits),
                      "examples": hits[:5]}
    return out


def _windows(path: pathlib.Path, facts: dict) -> dict:
    """
    Whether this can plausibly run on the box Friday actually runs on.

    Recorded as evidence, not a verdict: a docker-compose file means "needs a
    container runtime here", which is a real constraint on a Windows host with
    no Docker, and it is better named than discovered during a live gate.
    """
    reasons = []
    if "docker-compose.yml" in facts["install_markers"] or \
       "docker-compose.yaml" in facts["install_markers"]:
        reasons.append("ships docker-compose; needs a container runtime")
    if (path / "Makefile").exists():
        reasons.append("Makefile build; needs make/WSL or manual steps")
    for shell_script in path.glob("*.sh"):
        reasons.append(f"top-level shell script {shell_script.name}")
        break
    return {"constraints": reasons,
            "pure_python": facts["python_files"] > 0 and facts["js_ts_files"] == 0}


def audit_one(name: str) -> dict:
    path = UPSTREAM / name
    url, declared, mode, family, why = REPOS[name]
    if not (path / ".git").exists():
        return {"name": name, "state": "NOT_CLONED", "url": url,
                "declared_license": declared, "integration_mode": mode,
                "family": family, "why": why}

    facts = _tree_facts(path)
    license_info = _license(path)
    record = {
        "name": name,
        "url": url,
        "state": "AUDITED",
        "family": family,
        "why": why,
        "integration_mode": mode,
        "declared_license": declared,
        "license": license_info,
        "license_matches_declaration": _license_agrees(declared, license_info),
        "commit": _sha(path),
        "describe": _describe(path),
        "tree": facts,
        "risks": _risks(path),
        "windows": _windows(path, facts),
    }
    # Copy the license text next to the lock so it survives a cleaned clone.
    if license_info["file"]:
        LICENSES.mkdir(parents=True, exist_ok=True)
        target = LICENSES / f"{name}-{license_info['file']}"
        target.write_bytes((path / license_info["file"]).read_bytes())
        record["license"]["archived_at"] = str(target.relative_to(ROOT))
    return record


def _license_agrees(declared: str, found: dict) -> bool:
    detected = found.get("detected", "")
    if detected in ("ABSENT", "UNRECOGNISED"):
        return False
    return detected.split("-")[0].lower() in declared.lower()


def audit(names: list[str] | None = None) -> int:
    existing = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}
    for name in names or list(REPOS):
        record = audit_one(name)
        existing[name] = record
        flag = "" if record.get("license_matches_declaration", True) else "  LICENSE-MISMATCH"
        print(f"  {name:22} {record['state']:10} "
              f"{record.get('commit','')[:10]:12} "
              f"{record.get('license',{}).get('detected','-'):14}{flag}")
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    print(f"\n  lock -> {LOCK.relative_to(ROOT)}")
    return sum(1 for r in existing.values() if r.get("state") == "NOT_CLONED")


def status() -> int:
    if not LOCK.exists():
        print("  no lock file yet; run `clone` then `audit`")
        return 1
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    for name in REPOS:
        record = lock.get(name, {"state": "UNKNOWN"})
        print(f"  {name:22} {record['state']:12} {record.get('commit','')[:10]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["clone", "audit", "status"])
    parser.add_argument("names", nargs="*", help="subset of upstreams")
    args = parser.parse_args()
    names = args.names or None
    if names:
        unknown = [n for n in names if n not in REPOS]
        if unknown:
            print(f"unknown upstream(s): {unknown}")
            return 2
    return {"clone": lambda: clone(names),
            "audit": lambda: audit(names),
            "status": status}[args.command]()


if __name__ == "__main__":
    sys.exit(main())
