#!/usr/bin/env python3
"""
Create a fresh Friday database (audit A-001: the live `data/ada.sqlite3` is
runtime state and is no longer tracked; the schema lives in code).

    python scripts/seed_demo_db.py                  # data/ada.sqlite3 if absent
    python scripts/seed_demo_db.py --demo           # + a few demo rows
    python scripts/seed_demo_db.py --path x.sqlite3 # elsewhere
    python scripts/seed_demo_db.py --force          # replace an existing file

The schema is `friday.store.SCHEMA` plus the additive migrations in
`friday.store._ADDED_COLUMNS`; opening a `Store` applies both, so this
script never carries its own copy of the DDL (one source of truth).
Demo rows are obviously synthetic (project "demo", preference "dark mode")
and contain no personal data.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=None, help="database file (default: data/ada.sqlite3)")
    ap.add_argument("--demo", action="store_true", help="add a handful of synthetic rows")
    ap.add_argument("--force", action="store_true", help="replace an existing database")
    args = ap.parse_args(argv)

    from friday import store as S

    target = Path(args.path) if args.path else S.DEFAULT_DB
    if target.exists():
        if not args.force:
            print(f"exists: {target} (use --force to replace)")
            return 1
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = Path(str(target) + suffix)
            if p.exists():
                p.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)

    db = S.Store(target)                       # creates schema + applies migrations
    tables = [r[0] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    if args.demo:
        db.ensure_project("demo", "a synthetic project used by the seed script")
        db.remember(subject="ui theme", value="dark mode", kind="PREFERENCE",
                    source="seed_demo_db", scope="user")
        db.remember(subject="project demo", value="uses sqlite for durable state",
                    kind="FACT", source="seed_demo_db", scope="user", project_scope="demo")
    db.close()
    print(f"created {target} with {len(tables)} tables" + (" + demo rows" if args.demo else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
