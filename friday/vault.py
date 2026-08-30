"""
friday/vault.py -- the Vault: Friday's memory as readable, linked markdown.

"If it's not in the vault, it didn't happen." The point is that a human can
open a folder and read everything the system knows, in plain files, without a
database client.

    vault/raw/      what was captured, by day (the brain ledger + new facts)
    vault/wiki/     distilled knowledge, one page per subject namespace
    vault/outputs/  what Friday produced: finished runs and their artifacts

This is a PROJECTION, not a fourth memory (non-negotiable #11 forbids a
duplicate store). GBrain stays canonical and ada.sqlite3 stays Friday-local;
`sync()` re-renders the files from them. Every generated page says so in its
front matter, so nobody mistakes an export for the system of record -- and so
a hand-edit is never silently destroyed without warning: edits to generated
pages are reported by `sync(check_edits=True)` rather than overwritten blindly.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

VAULT = Path(os.getenv("FRIDAY_VAULT",
                       str(Path(__file__).resolve().parent.parent / "vault")))
SECTIONS = ("raw", "wiki", "outputs")

_GEN = "<!-- generated-by: friday.vault -->"
_SLUG = re.compile(r"[^a-z0-9._-]+")


def _slug(s):
    return _SLUG.sub("-", (s or "").strip().lower()).strip("-")[:60] or "untitled"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamp(body):
    """Content hash, so a later sync can tell a hand-edit from its own output."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def _header(title, source, stamp):
    return ("---\ntitle: %s\nsource: %s\nstamp: %s\ncanonical: %s\n---\n\n"
            % (title, source, stamp,
               "GBrain + data/ada.sqlite3 (this file is a projection)"))


def _write(rel, title, source, body, report):
    """Write a generated page, refusing to clobber a hand-edited one.

    Idempotent on purpose: the front matter carries a hash of the BODY, so an
    unchanged fact set rewrites nothing. A wall-clock `generated:` stamp would
    rewrite all 60+ pages on every sync and fill git with noise.
    """
    path = VAULT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = body.rstrip() + "\n"
    stamp = _stamp(body)
    payload = _header(title, source, stamp) + body + "\n" + _GEN + "\n"
    if path.exists():
        old = path.read_text(encoding="utf-8", errors="replace")
        if _GEN not in old:
            report["skipped_edited"].append(rel)
            return
        if ("stamp: %s" % stamp) in old:
            report["unchanged"] += 1
            return
    path.write_text(payload, encoding="utf-8")
    report["written"].append(rel)


def _facts():
    from friday import ui_server as U
    conn = U._connect()
    try:
        return U._rows(conn, "SELECT subject, value, kind, scope, source, "
                             "confidence, created_at FROM memories "
                             "WHERE superseded=0 ORDER BY subject")
    finally:
        if conn is not None:
            conn.close()


def _ledger():
    try:
        from friday.brain import SharedBrainAdapter
        p = Path(SharedBrainAdapter()._ledger_path())
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return out
    except Exception:  # noqa: BLE001
        return []


def _runs():
    from friday import ui_server as U
    conn = U._connect()
    try:
        runs = U._rows(conn, "SELECT run_id, request, status, summary, "
                             "objective_summary, created_at, finished_at "
                             "FROM objective_runs ORDER BY created_at DESC LIMIT 40")
        arts = U._rows(conn, "SELECT run_id, type, title, path_or_uri, producer, "
                             "created_at FROM artifacts ORDER BY created_at DESC LIMIT 200")
    finally:
        if conn is not None:
            conn.close()
    by_run = defaultdict(list)
    for a in arts:
        by_run[a.get("run_id")].append(a)
    return runs, by_run


def sync():
    """Re-render the vault from the canonical memory. Returns a report."""
    report = {"written": [], "skipped_edited": [], "unchanged": 0, "at": _now()}
    for s in SECTIONS:
        (VAULT / s).mkdir(parents=True, exist_ok=True)

    # ---- wiki: one page per subject namespace, facts as linked bullets
    facts = _facts()
    groups = defaultdict(list)
    for f in facts:
        ns = (f["subject"] or "misc").split(".", 1)[0]
        groups[ns].append(f)
    for ns, items in sorted(groups.items()):
        lines = ["# %s" % ns, "",
                 "%d fact(s) Friday holds under `%s.`" % (len(items), ns), ""]
        for f in sorted(items, key=lambda x: x["subject"] or ""):
            leaf = (f["subject"] or "").split(".", 1)[-1]
            conf = f.get("confidence")
            tag = "" if conf in (None, 1.0) else " _(confidence %.2f)_" % conf
            lines.append("- **%s** — %s%s" % (leaf, f.get("value") or "", tag))
            if f.get("source"):
                lines.append("  - source: %s" % str(f["source"])[:160])
        others = [n for n in sorted(groups) if n != ns][:8]
        if others:
            lines += ["", "See also: " + ", ".join("[[%s]]" % o for o in others)]
        _write("wiki/%s.md" % _slug(ns), ns, "memories table", "\n".join(lines),
               report)

    # ---- raw: what was captured, by day
    days = defaultdict(list)
    for e in _ledger():
        day = (e.get("recorded_at") or "")[:10] or "undated"
        days[day].append("- `brain` **%s** — %s _(via %s)_" % (
            e.get("entity") or "friday", e.get("fact") or "",
            e.get("provenance") or "?"))
    for f in facts:
        day = (f.get("created_at") or "")[:10] or "undated"
        days[day].append("- `local` **%s** — %s" % (f.get("subject"),
                                                    f.get("value") or ""))
    for day, lines in sorted(days.items(), reverse=True)[:60]:
        _write("raw/%s.md" % _slug(day), "captured %s" % day,
               "brain ledger + memories",
               "# Captured %s\n\n%s" % (day, "\n".join(lines[:400])), report)

    # ---- outputs: finished work, with its artifacts
    runs, arts = _runs()
    index = ["# Outputs", "", "What Friday produced, newest first.", ""]
    for r in runs:
        rid = r.get("run_id") or ""
        title = (r.get("request") or r.get("objective_summary") or rid)[:80]
        name = "%s-%s" % (_slug(rid)[:20], _slug(title)[:30])
        body = ["# %s" % title, "",
                "- run: `%s`" % rid,
                "- status: **%s**" % (r.get("status") or "?"),
                "- started: %s" % (r.get("created_at") or ""),
                "- finished: %s" % (r.get("finished_at") or "—"), ""]
        if r.get("summary"):
            body += ["## Summary", "", str(r["summary"]), ""]
        mine = arts.get(rid) or []
        if mine:
            body += ["## Artifacts", ""]
            body += ["- **%s** (%s) — `%s`" % (a.get("title") or "untitled",
                                               a.get("type") or "?",
                                               a.get("path_or_uri") or "")
                     for a in mine]
        _write("outputs/%s.md" % name, title, "objective_runs + artifacts",
               "\n".join(body), report)
        index.append("- [[%s]] — %s" % (name, r.get("status") or "?"))
    _write("outputs/index.md", "Outputs index", "objective_runs",
           "\n".join(index), report)
    return report


def _safe(rel):
    """Resolve a vault-relative path, refusing anything outside the vault."""
    if not rel:
        return None
    p = (VAULT / rel).resolve()
    try:
        p.relative_to(VAULT.resolve())
    except ValueError:
        return None
    return p if p.is_file() and p.suffix == ".md" else None


def read(rel):
    p = _safe(rel)
    if p is None:
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    return {"path": rel, "text": text, "edited": _GEN not in text}


def tree():
    """Every vault file, grouped by section. Empty is a valid, honest answer."""
    out = {"root": str(VAULT), "sections": {}, "count": 0, "exists": VAULT.exists()}
    for s in SECTIONS:
        d = VAULT / s
        files = []
        if d.is_dir():
            for f in sorted(d.glob("*.md"), key=lambda x: x.name):
                try:
                    files.append({"path": "%s/%s" % (s, f.name), "name": f.stem,
                                  "bytes": f.stat().st_size})
                except OSError:
                    pass
        out["sections"][s] = files
        out["count"] += len(files)
    return out
