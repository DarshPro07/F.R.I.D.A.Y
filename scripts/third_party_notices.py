"""
Generate THIRD_PARTY_NOTICES.md from the upstream lock.

MIT asks that its copyright notice travel with the work. Apache-2.0 asks that
NOTICE be retained. AGPL-3.0 asks considerably more: anyone the software is
offered to over a network is owed its source. None of that is satisfied by a
lock file buried in third_party/, so this writes the notice a reader can find.

Generated, never typed: every fact here is read from third_party/UPSTREAM_LOCK.json,
which is itself derived from the clones. Re-run it whenever the lock changes.

    .venv\\Scripts\\python.exe scripts\\third_party_notices.py
    .venv\\Scripts\\python.exe scripts\\third_party_notices.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "third_party" / "UPSTREAM_LOCK.json"
OUT = ROOT / "THIRD_PARTY_NOTICES.md"

#: Licences that oblige us to say more than "thank you".
STRONG_COPYLEFT = ("AGPL-3.0", "GPL-3.0")
WEAK_COPYLEFT = ("LGPL", "MPL-2.0")

HEADER = """# Third-party notices

Friday is built on the projects below. They are pinned as git clones under
`third_party/upstream/`, each at an exact commit, and this file is generated
from `third_party/UPSTREAM_LOCK.json` rather than maintained by hand.

Some are used as running code. Most are pinned as reference: read during design,
never linked. The `use` column says which, because the distinction is the whole
point of the licence review.

Regenerate with `python scripts/third_party_notices.py`.
"""

COPYLEFT_NOTE = """
## Copyleft obligations

These upstreams are strong copyleft. Friday does not import them: they are
either isolated in a separate process (SIDECAR) or read as reference and never
linked, which is what keeps their terms off the rest of this codebase. If any of
them is ever run as part of a networked service, AGPL-3.0 section 13 obliges
that service to offer its users the corresponding source.
"""


def rows():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    for name, e in sorted(lock.items()):
        yield {
            "name": name,
            "url": e.get("url") or "",
            "commit": (e.get("commit") or "")[:12],
            "license": e.get("license_verified") or e.get("license") or "UNIDENTIFIED",
            "mode": e.get("integration_mode") or e.get("status") or "",
            "role": (e.get("role") or "").strip(),
        }


def render() -> str:
    data = list(rows())
    out = [HEADER, "", "## Pinned upstreams", "",
           "| project | licence | pinned commit | use |",
           "| --- | --- | --- | --- |"]
    for r in data:
        link = "[%s](%s)" % (r["name"], r["url"]) if r["url"] else r["name"]
        out.append("| %s | %s | `%s` | %s |" % (link, r["license"], r["commit"], r["mode"]))

    strong = [r for r in data if any(c in r["license"] for c in STRONG_COPYLEFT)]
    if strong:
        out += ["", COPYLEFT_NOTE.strip(), ""]
        for r in strong:
            out.append("- **%s** — %s, used as %s" % (r["name"], r["license"], r["mode"] or "reference"))

    weak = [r for r in data if any(c in r["license"] for c in WEAK_COPYLEFT)]
    if weak:
        out += ["", "## Weak copyleft", ""]
        for r in weak:
            out.append("- **%s** — %s, used as %s" % (r["name"], r["license"], r["mode"] or "reference"))

    # The audit reports a missing licence as UNIDENTIFIED or NONE depending on
    # whether it found a file it could not classify or no file at all. Both mean
    # the same thing here -- nobody has granted us terms -- and both must show up
    # in this section, which is the whole reason it exists.
    unknown = [r for r in data
               if r["license"].upper() in ("UNIDENTIFIED", "NONE", "")]
    if unknown:
        out += ["", "## Licence not identified", "",
                "These need a human decision before the code they contain is used:", ""]
        for r in unknown:
            out.append("- **%s** — no recognised licence text found in the clone" % r["name"])

    out += ["", "## Full licence texts", "",
            "Each clone keeps its own licence file at",
            "`third_party/upstream/<project>/LICENSE`. Those files are the",
            "authoritative terms; this page only summarises them.", ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="fail if the notices have drifted from the lock")
    args = ap.parse_args()

    if not LOCK.exists():
        print("missing %s -- run scripts/upstream_lock.py first" % LOCK)
        return 1
    text = render()

    if args.check:
        if not OUT.exists():
            print("missing %s" % OUT)
            return 1
        if OUT.read_text(encoding="utf-8") != text:
            print("%s has drifted from the lock; re-run without --check" % OUT)
            return 1
        print("%s matches the lock" % OUT.name)
        return 0

    OUT.write_text(text, encoding="utf-8")
    n = len(list(rows()))
    print("wrote %s (%d upstreams)" % (OUT, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
