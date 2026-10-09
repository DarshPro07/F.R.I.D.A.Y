"""
SKILL-007: a garbage-collection pass over skill usage and overlap archives a
stale duplicate and keeps an active one.
"""
from __future__ import annotations

from friday.skills import contracts as K
from friday.skills import gc as G
from friday.skills.cli import main as cli_main
from friday.skills.registry import SkillRegistry

NOW = "2026-10-09T00:00:00Z"
OLD = "2026-01-01T00:00:00Z"        # 281 days before NOW
RECENT = "2026-10-01T00:00:00Z"

DESC = "resolve a davinci project export render queue and verify the output file"


def plant(reg, name, *, state=K.ACTIVE, description=DESC, uses=0, at=RECENT):
    reg.upsert(K.SkillRecord(name=name, description=description, state=state, tags=["video"]))
    with reg._connect() as db:
        db.execute("UPDATE skills SET usage_count = ?, updated_at = ? WHERE name = ?", (uses, at, name))


def test_gc_archives_a_planted_stale_duplicate_and_keeps_the_active_one(tmp_path):
    reg = SkillRegistry(tmp_path / "r.sqlite3")
    plant(reg, "render-export", uses=12)
    plant(reg, "render-export-old", uses=1, at=OLD)

    report = G.collect(reg, now=NOW)

    assert [a["name"] for a in report["archived"]] == ["render-export-old"]
    assert report["archived"][0]["duplicate_of"] == "render-export"
    assert reg.get("render-export-old").state == K.DEPRECATED
    assert reg.get("render-export").state == K.ACTIVE
    assert reg.history("render-export-old")[0]["actor"] == "gc"       # newest history row: who and why is on record


def test_gc_keeps_a_fresh_duplicate_a_distinct_stale_skill_and_an_idle_unique_one(tmp_path):
    reg = SkillRegistry(tmp_path / "r.sqlite3")
    plant(reg, "render-export", uses=12)
    plant(reg, "render-export-copy", uses=1, at=RECENT)                   # duplicate but in use lately
    plant(reg, "backup-vault", description="rotate the encrypted vault snapshots offsite", at=OLD)  # stale, not a duplicate

    report = G.collect(reg, now=NOW)

    assert report["archived"] == []
    assert {r.name: r.state for r in reg.list()} == {
        "render-export": K.ACTIVE, "render-export-copy": K.ACTIVE, "backup-vault": K.ACTIVE}


def test_gc_dry_run_changes_nothing_and_leaves_the_owners_switches_alone(tmp_path):
    reg = SkillRegistry(tmp_path / "r.sqlite3")
    plant(reg, "render-export", uses=12)
    plant(reg, "render-export-old", uses=1, at=OLD)
    plant(reg, "render-export-off", state=K.DISABLED, uses=0, at=OLD)     # the owner switched it off: not GC's to touch

    report = G.collect(reg, now=NOW, apply=False)

    assert [a["name"] for a in report["archived"]] == ["render-export-old"]
    assert reg.get("render-export-old").state == K.ACTIVE
    assert reg.get("render-export-off").state == K.DISABLED
    assert G.collect(reg, now=NOW)["archived"][0]["name"] == "render-export-old"
    assert reg.get("render-export-off").state == K.DISABLED


def test_cli_gc_archives_and_prints_the_result(tmp_path, capsys):
    db = tmp_path / "r.sqlite3"
    reg = SkillRegistry(db)
    plant(reg, "render-export", uses=12)
    plant(reg, "render-export-old", uses=1, at=OLD)

    assert cli_main(["--db", str(db), "gc"]) == 0

    assert "render-export-old" in capsys.readouterr().out
    assert SkillRegistry(db).get("render-export-old").state == K.DEPRECATED
