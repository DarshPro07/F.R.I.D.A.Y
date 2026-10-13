"""
SKILL-006: an activated external skill records source, commit, hash, signature,
SBOM, scan and evaluation.

The import supplies source, commit, signature and SBOM at register(); activation
fills the rest from the scan kept beside the version (CAP-MKT-002) and the
evaluation, and writes one provenance row per activated version. An agent or
community skill with a missing field is refused with the reason recorded. FRIDAY
has no signature verifier, so a supplied signature is stored as declared and
unverified, and a missing one is stored as unsigned: nothing is invented.
"""
from __future__ import annotations

import json

import pytest

from friday import forge
from friday.skills import contracts as K
from test_skills_golden import World, use_stand_in_scanner

COMMIT = "0123456789abcdef0123456789abcdef01234567"
URL = "https://example.invalid/skills/pack.git"
SBOM = {"components": [{"name": "helper", "version": "1.2.3"}]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    use_stand_in_scanner(tmp_path / "scanner", monkeypatch)
    # The activation gate under test is provenance; keep the evaluator's own external scan away from
    # whatever SkillSpector is installed at the repository root.
    monkeypatch.setenv("FRIDAY_SKILLSPECTOR_VENV", str(tmp_path / "no-such-venv"))
    return World(tmp_path)


def staged(world: World, name: str, trust: str, **imported):
    skill = world.write_skill(name)
    world.lifecycle.register(skill, tier=K.PROJECT_TIER, trust=trust, **imported)
    world.lifecycle.stage(name)
    return skill


def full_import(**over) -> dict:
    data = {"source": "marketplace:example", "source_url": URL, "source_commit": COMMIT,
            "signature": "sig:abc123", "sbom": SBOM}
    data.update(over)
    return data


def test_an_activated_community_skill_records_every_provenance_field(world):
    skill = staged(world, "ext-full", K.TRUST_COMMUNITY, **full_import())
    report, record = world.lifecycle.evaluate_and_activate(skill.name)
    assert record.state == K.ACTIVE
    row = world.registry.provenance(skill.name)
    kept = json.loads((world.root / "versions" / skill.name / "1.0.0.skillspector.json").read_text(encoding="utf-8"))
    assert row["version"] == "1.0.0"
    assert row["source"] == "marketplace:example" and row["source_url"] == URL
    assert row["source_commit"] == COMMIT
    assert row["content_hash"] == skill.content_hash() == record.content_hash
    assert row["signature"] == "sig:abc123" and row["signature_status"] == "declared-unverified"
    assert row["sbom_status"] == "declared" and json.loads(row["sbom"]) == SBOM
    assert row["scan"]["status"] == forge.SCAN_CLEAN
    assert row["scan"]["report_sha256"] == kept["report_sha256"]
    assert row["scan"]["target_sha256"] == kept["target_sha256"]
    assert row["scan"]["scanner_version"] == kept["scanner_version"]
    assert row["evaluation"]["nonce"] == report.nonce and row["evaluation"]["passed"] is True
    assert row["evaluation"]["content_hash"] == report.content_hash


def test_the_provenance_scan_says_a_clean_scan_is_not_runtime_safe(world):
    skill = staged(world, "ext-static", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.evaluate_and_activate(skill.name)
    scan = world.registry.provenance(skill.name)["scan"]
    assert scan["status"] == forge.SCAN_CLEAN             # the scanner's verdict is clean ...
    assert scan["runtime_safe"] is False                  # ... and the row still does not say it is safe to run


def test_a_skill_with_no_signature_records_that_explicitly(world):
    skill = staged(world, "ext-unsigned", K.TRUST_COMMUNITY, **full_import(signature="", sbom=None))
    world.lifecycle.evaluate_and_activate(skill.name)
    row = world.registry.provenance(skill.name)
    assert row["signature"] == "" and row["signature_status"] == "unsigned"
    assert row["sbom"] == "" and row["sbom_status"] == "none"


def test_a_community_skill_without_a_source_commit_cannot_activate(world):
    skill = staged(world, "ext-nocommit", K.TRUST_COMMUNITY, **full_import(source_commit=""))
    with pytest.raises(K.TransitionRefused, match="provenance.*source_commit"):
        world.lifecycle.evaluate_and_activate(skill.name)
    assert world.registry.get(skill.name).state != K.ACTIVE
    assert world.registry.provenance(skill.name) is None
    assert "provenance" in world.registry.history(skill.name)[-1]["reason"]
    assert "source_commit" in world.registry.history(skill.name)[-1]["reason"]


def test_a_community_skill_with_no_import_at_all_names_every_missing_field(world):
    skill = staged(world, "ext-bare", K.TRUST_COMMUNITY)
    with pytest.raises(K.TransitionRefused) as raised:
        world.lifecycle.evaluate_and_activate(skill.name)
    for field in ("source", "source_url", "source_commit"):
        assert field in str(raised.value)
    assert world.registry.get(skill.name).state != K.ACTIVE


def test_an_agent_skill_without_a_source_cannot_activate_but_with_one_it_does(world):
    bare = staged(world, "agent-bare", K.TRUST_AGENT)
    with pytest.raises(K.TransitionRefused, match="provenance.*source"):
        world.lifecycle.evaluate_and_activate(bare.name)
    assert world.registry.get(bare.name).state != K.ACTIVE
    sourced = staged(world, "agent-sourced", K.TRUST_AGENT, source="objective-42")
    _, record = world.lifecycle.evaluate_and_activate(sourced.name)
    assert record.state == K.ACTIVE
    row = world.registry.provenance(sourced.name)
    assert row["source"] == "objective-42" and row["signature_status"] == "unsigned"
    assert row["source_commit"] == record.codebase_commit


@pytest.mark.parametrize("trust", [K.TRUST_BUILT_IN, K.TRUST_OWNER])
def test_built_in_and_owner_skills_need_no_import_record(world, trust):
    skill = staged(world, f"own-{trust}".replace("_", "-"), trust)
    _, record = world.lifecycle.evaluate_and_activate(skill.name)
    assert record.state == K.ACTIVE
    assert world.registry.provenance(skill.name) is None


def test_re_registering_without_an_import_keeps_the_recorded_import(world):
    skill = staged(world, "ext-keep", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.register(skill, tier=K.PROJECT_TIER, trust=K.TRUST_COMMUNITY)
    assert world.registry.import_record(skill.name)["source_commit"] == COMMIT
    world.lifecycle.evaluate_and_activate(skill.name)
    assert world.registry.provenance(skill.name)["source_commit"] == COMMIT


def _lose_provenance(world, name):
    """An external skill as it stood before SKILL-006: ACTIVE, with no import and no provenance row."""
    with world.registry._connect() as db:
        db.execute("DELETE FROM skill_provenance WHERE name = ?", (name,))
        db.execute("DELETE FROM skill_imports WHERE name = ?", (name,))
    assert world.registry.provenance(name) is None


def test_revalidation_does_not_return_an_external_skill_to_active_without_provenance(world):
    skill = staged(world, "ext-legacy", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.evaluate_and_activate(skill.name)
    _lose_provenance(world, skill.name)
    world.registry.transition(skill.name, K.NEEDS_REVALIDATION, actor="test", reason="a dependency changed")
    assert world.graph.check(skill.name)["fresh"]  # the dependency fingerprints alone would say FRESH
    with pytest.raises(K.TransitionRefused, match="provenance.*source_commit"):
        world.lifecycle.revalidate(skill.name, run_tests=False)
    assert world.registry.get(skill.name).state == K.NEEDS_REVALIDATION
    assert world.registry.provenance(skill.name) is None
    assert "provenance incomplete" in world.registry.history(skill.name)[-1]["reason"]


def test_revalidation_re_gates_an_external_skill_that_has_its_import_but_no_provenance_row(world):
    skill = staged(world, "ext-backfill", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.evaluate_and_activate(skill.name)
    with world.registry._connect() as db:
        db.execute("DELETE FROM skill_provenance WHERE name = ?", (skill.name,))
    world.registry.transition(skill.name, K.NEEDS_REVALIDATION, actor="test", reason="a dependency changed")
    outcome, _ = world.lifecycle.revalidate(skill.name, run_tests=False)
    assert outcome == "FRESH"
    assert world.registry.get(skill.name).state == K.ACTIVE
    assert world.registry.provenance(skill.name)["source_commit"] == COMMIT


def test_backfill_records_provenance_for_an_already_active_external_skill(world):
    skill = staged(world, "ext-active-backfill", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.evaluate_and_activate(skill.name)
    with world.registry._connect() as db:
        db.execute("DELETE FROM skill_provenance WHERE name = ?", (skill.name,))
    assert world.registry.get(skill.name).state == K.ACTIVE
    assert world.registry.provenance(skill.name) is None

    result = world.lifecycle.backfill_active_provenance(skill.name, actor="test")

    assert result == [{"skill": skill.name, "version": "1.0.0", "status": "backfilled"}]
    row = world.registry.provenance(skill.name)
    assert row["source"] == "marketplace:example"
    assert row["source_url"] == URL
    assert row["source_commit"] == COMMIT
    assert row["content_hash"] == skill.content_hash()
    assert row["scan"]["status"] == forge.SCAN_CLEAN
    assert row["evaluation"]["passed"] is True
    assert world.registry.get(skill.name).state == K.ACTIVE


def test_backfill_uses_the_active_verified_version_after_rollback(world):
    skill = staged(world, "ext-rollback-backfill", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.evaluate_and_activate(skill.name)
    frontmatter, body = skill.read()
    skill.write(frontmatter, body + "\n## Pitfalls\n\n- newer guidance\n")
    assert world.lifecycle.update(skill.name, bump="major")["version"] == "2.0.0"
    restored = world.lifecycle.rollback(skill.name, actor="test", reason="prefer v1")
    assert restored.version == "1.0.0"
    assert "newer guidance" not in skill.text()
    with world.registry._connect() as db:
        db.execute("DELETE FROM skill_provenance WHERE name = ? AND version = ?", (skill.name, "1.0.0"))
    assert world.registry.provenance(skill.name) is None
    assert world.registry.provenance(skill.name, "2.0.0") is not None

    result = world.lifecycle.backfill_active_provenance(skill.name, actor="test")

    assert result == [{"skill": skill.name, "version": "1.0.0", "status": "backfilled"}]
    row = world.registry.provenance(skill.name)
    assert row["version"] == "1.0.0"
    assert row["content_hash"] == skill.content_hash() == restored.content_hash
    assert row["evaluation"]["version"] == "1.0.0"


def test_backfill_refuses_an_active_community_skill_with_no_import_record(world):
    skill = staged(world, "ext-originless-backfill", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.evaluate_and_activate(skill.name)
    _lose_provenance(world, skill.name)
    with world.registry._connect() as db:
        db.execute("UPDATE skills SET source = '', source_url = '' WHERE name = ?", (skill.name,))
    assert world.registry.get(skill.name).state == K.ACTIVE

    with pytest.raises(K.TransitionRefused, match="provenance.*source_url.*source_commit"):
        world.lifecycle.backfill_active_provenance(skill.name, actor="test")

    assert world.registry.provenance(skill.name) is None
    assert world.registry.get(skill.name).state == K.ACTIVE
    assert "provenance incomplete" in world.registry.history(skill.name)[-1]["reason"]


def test_a_kept_scan_from_before_the_flag_is_never_read_as_runtime_safe(world):
    skill = staged(world, "ext-old-scan", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.evaluate_and_activate(skill.name)
    kept_path = world.root / "versions" / skill.name / "1.0.0.skillspector.json"
    kept = json.loads(kept_path.read_text(encoding="utf-8"))
    del kept["runtime_safe"]                               # a report written before the flag existed
    kept_path.write_text(json.dumps(kept, indent=2, sort_keys=True), encoding="utf-8")
    with world.registry._connect() as db:
        db.execute("DELETE FROM skill_provenance WHERE name = ?", (skill.name,))
    world.registry.transition(skill.name, K.NEEDS_REVALIDATION, actor="test", reason="a dependency changed")
    outcome, _ = world.lifecycle.revalidate(skill.name, run_tests=False)
    assert outcome == "FRESH"
    scan = world.registry.provenance(skill.name)["scan"]
    assert scan["status"] == forge.SCAN_CLEAN and scan["runtime_safe"] is False


def test_revalidation_keeps_the_fresh_shortcut_when_provenance_is_on_record(world):
    skill = staged(world, "ext-shortcut", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.evaluate_and_activate(skill.name)
    before = world.registry.provenance(skill.name)
    world.registry.transition(skill.name, K.NEEDS_REVALIDATION, actor="test", reason="a dependency changed")
    outcome, report = world.lifecycle.revalidate(skill.name, run_tests=False)
    assert (outcome, report) == ("FRESH", None)
    assert world.registry.get(skill.name).state == K.ACTIVE
    assert world.registry.provenance(skill.name)["activated_at"] == before["activated_at"]


def test_the_row_says_where_its_commit_came_from(world, monkeypatch):
    upstream = staged(world, "ext-kind-up", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.evaluate_and_activate(upstream.name)
    assert world.registry.provenance(upstream.name)["source_commit_kind"] == "upstream"

    monkeypatch.setattr(world.graph, "identity", lambda: ("repo", "feedface" * 5))
    agent = staged(world, "agent-kind-code", K.TRUST_AGENT, source="objective-1")
    world.lifecycle.evaluate_and_activate(agent.name)
    row = world.registry.provenance(agent.name)
    assert row["source_commit"] == "feedface" * 5 and row["source_commit_kind"] == "codebase"

    monkeypatch.setattr(world.graph, "identity", lambda: ("", ""))
    nowhere = staged(world, "agent-kind-none", K.TRUST_AGENT, source="objective-2")
    world.lifecycle.evaluate_and_activate(nowhere.name)
    row = world.registry.provenance(nowhere.name)
    assert row["source_commit"] == "" and row["source_commit_kind"] == "none"


def test_provenance_defaults_to_the_current_version_after_a_rollback(world):
    skill = staged(world, "ext-roll", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.evaluate_and_activate(skill.name)
    with world.registry._connect() as db:  # a newer activated version on record
        db.execute("INSERT INTO skill_provenance (name, version, content_hash, signature_status, sbom_status, activated_at)"
                   " VALUES (?,?,?,?,?,?)", (skill.name, "2.0.0", "h2", "unsigned", "none", K.now_iso()))
    assert world.registry.get(skill.name).version == "1.0.0"
    assert world.registry.provenance(skill.name)["version"] == "1.0.0"
    assert world.registry.provenance(skill.name, "2.0.0")["content_hash"] == "h2"


def test_a_failed_provenance_write_leaves_the_skill_inactive(world, monkeypatch):
    skill = staged(world, "ext-write-fails", K.TRUST_COMMUNITY, **full_import())

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(world.registry, "record_provenance", boom)
    with pytest.raises(RuntimeError):
        world.lifecycle.evaluate_and_activate(skill.name)
    assert world.registry.get(skill.name).state != K.ACTIVE


class _Marks:
    """The graph seam cmd_revalidate reads, pinned so no git history is consulted."""

    def __init__(self, names):
        self._names = list(names)

    def changed_paths_since(self, since):
        return ["friday/launcher.py"]

    def revalidate_after_change(self, changed, actor):
        return [{"skill": name, "changed": list(changed)} for name in self._names]


def _services(world, names):
    from types import SimpleNamespace
    return SimpleNamespace(lifecycle=world.lifecycle, graph=_Marks(names))


def _args(**over):
    from types import SimpleNamespace
    data = {"name": None, "since": "HEAD~1", "no_run": True}
    data.update(over)
    return SimpleNamespace(**data)


def test_the_revalidate_command_reports_a_provenance_refusal_instead_of_raising(world, capsys):
    from friday.skills import cli
    skill = staged(world, "ext-cli-one", K.TRUST_COMMUNITY, **full_import())
    world.lifecycle.evaluate_and_activate(skill.name)
    _lose_provenance(world, skill.name)
    world.registry.transition(skill.name, K.NEEDS_REVALIDATION, actor="test", reason="a dependency changed")
    assert cli.cmd_revalidate(_services(world, []), _args(name=skill.name)) == 2
    assert "refused" in capsys.readouterr().err
    assert world.registry.get(skill.name).state == K.NEEDS_REVALIDATION


def test_one_provenance_refusal_does_not_stop_the_batch_revalidation(world, capsys):
    from friday.skills import cli
    legacy = staged(world, "ext-cli-a-legacy", K.TRUST_COMMUNITY, **full_import())
    healthy = staged(world, "ext-cli-b-healthy", K.TRUST_COMMUNITY, **full_import())
    for skill in (legacy, healthy):
        world.lifecycle.evaluate_and_activate(skill.name)
        world.registry.transition(skill.name, K.NEEDS_REVALIDATION, actor="test", reason="a dependency changed")
    _lose_provenance(world, legacy.name)
    code = cli.cmd_revalidate(_services(world, [legacy.name, healthy.name]), _args())
    captured = capsys.readouterr()
    assert code == 1
    assert f"{legacy.name}: refused" in captured.out
    assert f"{healthy.name}: FRESH" in captured.out
    assert world.registry.get(legacy.name).state == K.NEEDS_REVALIDATION
    assert world.registry.get(healthy.name).state == K.ACTIVE
