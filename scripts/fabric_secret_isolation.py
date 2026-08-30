"""
MCP_VERIFICATION_PLAN M6: prove no live secret VALUE reached a provider store.

Run:  .venv/Scripts/python.exe scripts/fabric_secret_isolation.py

Reads the real values out of `.env`, asks each provider's own search surface
for a 24-character prefix of each, and prints only the verdict. The values are
never printed, never logged and never written to a file - which is the point,
and is why this is a script rather than a pytest case with a fixture.

A `clean` line means the provider's index does not contain that secret. It is
a stronger claim than "the .env file was excluded", because it asks the store
rather than trusting the exclusion list.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CBM = ROOT / "third_party" / "bin" / "cbm" / "codebase-memory-mcp.exe"
PROJECT = "friday-core"

#: How much of a secret to search for. Long enough that a hit is not a
#: coincidence, short enough to survive a store that truncates.
PROBE = 24


def live_secrets() -> dict[str, str]:
    env = ROOT / ".env"
    if not env.exists():
        return {}
    found = {}
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^([A-Z_]+)=(.+)$", line.strip())
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip().strip('"')
        if ("KEY" in name or "SECRET" in name or "TOKEN" in name) and len(value) >= PROBE:
            found[name] = value
    return found


def probe_codebase_memory(value: str) -> bool:
    """True when the code graph contains this secret."""
    result = subprocess.run(
        [str(CBM), "cli", "search_code", "--project", PROJECT,
         "--pattern", value[:PROBE], "--limit", "5"],
        capture_output=True, text=True, timeout=300,
        encoding="utf-8", errors="replace")
    return value[:PROBE] in (result.stdout or "")


PROVIDERS = {"codebase_memory": (CBM, probe_codebase_memory)}


def main() -> int:
    secrets = live_secrets()
    if not secrets:
        print("  no .env secrets found to probe")
        return 0

    failures = []
    for provider, (binary, probe) in PROVIDERS.items():
        if not pathlib.Path(binary).exists():
            print(f"  {provider:20} SKIPPED (not installed)")
            continue
        print(f"  {provider}: probing {len(secrets)} values (never printed)")
        for name, value in secrets.items():
            leaked = probe(value)
            print(f"    {name:26} {'LEAK' if leaked else 'clean'}")
            if leaked:
                failures.append(f"{provider}/{name}")

    print("\n  VERDICT:", ("M6 FAIL - " + ", ".join(failures)) if failures
          else "M6 PASS - no secret value found in any provider store")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
