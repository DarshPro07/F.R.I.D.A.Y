"""GB-13: a skill requests scope; it never grants it.

The defect this guards is the upstream one (Gawkbot #1068): a free-text
skill ran with the whole tool surface of whoever invoked it. Every test
here is a way a manifest could become a grant, checked at the seam where
it would matter:

    parse()          a manifest cannot say '*', cannot carry a grant word,
                     cannot ask above its risk class, cannot name a machine path
    Authority        intersection only narrows - id sets meet, deny sets join,
                     tiers take the minimum
    CapabilityRuntime a capability outside the authority is refused BEFORE
                     resolution, and an ALLOWED capability still meets policy
    narrow_profile   the Claude CLI allowlist loses what the manifest withheld
    TaskBundle       Hermes gets the skills' prohibitions in its contract

Real SkillLadder in a real temp SQLite file; real CapabilityRuntime; real
registry. The only fake is the resolved function under a capability, so the
test can prove it was never reached.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from friday import capability_runtime as R
from friday import contracts as c
from friday import policy as P
from friday import skill_permissions as SP
from friday import trust as T
from friday.skill_ladder import SkillLadder


@pytest.fixture()
def ladder(tmp_path):
    return SkillLadder(tmp_path / "skills.sqlite3")


@pytest.fixture()
def perms(ladder):
    return SP.SkillPermissions(ladder)


def validated(ladder, name):
    ladder.capture(name, "procedure", criteria=["expensive_rediscovery"], evidence="e")
    ladder.validate(name, passed=True, validation="ok")


LOW_READER = """
risk: LOW
permissions:
  capabilities:
    - files_read
    - files_roots
    - web_search
  prohibited:
    - files_delete
    - memory_forget
  filesystem:
    read: [friday]
  network:
    domains: [github.com]
"""


# --------------------------------------------------------------------------
# parse / lint
# --------------------------------------------------------------------------

class TestAManifestCannotBeAGrant:
    def test_a_well_formed_low_manifest_passes(self):
        m, lint = SP.parse(LOW_READER)
        assert lint.status == SP.PASS, lint.findings
        assert m.risk == SP.LOW and m.ceiling == T.R1
        assert "files_read" in m.capabilities and "files_delete" in m.prohibited
        assert m.network and m.network_domains == ("github.com",)

    def test_a_bare_star_is_refused(self):
        m, lint = SP.parse("risk: LOW\npermissions:\n  capabilities: ['*']\n")
        assert m is None and lint.status == SP.FAIL
        assert any("'*'" in f for f in lint.findings)

    @pytest.mark.parametrize("key", ["grant", "allow_all", "override", "bypass"])
    def test_a_grant_shaped_key_is_refused_wherever_it_hides(self, key):
        m, lint = SP.parse({"risk": "LOW", "permissions": {key: ["files_delete"]}})
        assert m is None and lint.status == SP.FAIL
        assert any(key in f for f in lint.findings)

    def test_a_grant_shaped_value_is_refused_too(self):
        """The key allow-lists cannot see values. A capability entry called
        `allow_all` or a write path called `bypass/` is not a scope request."""
        m, lint = SP.parse({"risk": "LOW", "permissions": {"capabilities": ["allow_all"]}})
        assert m is None and any("allow_all" in f and "grant" in f for f in lint.findings)
        m, lint = SP.parse({"risk": "LOW", "permissions": {"filesystem": {"write": ["bypass/out"]}}})
        assert m is None and any("bypass" in f for f in lint.findings)

    def test_a_skill_cannot_ask_above_its_risk_class(self):
        # files_delete is R3 (DELETE); a LOW skill tops out at R1.
        m, lint = SP.parse({"risk": "LOW", "permissions": {"capabilities": ["files_delete"]}})
        assert m is None
        assert any("files_delete" in f and "R3" in f for f in lint.findings)

    def test_a_high_skill_may_ask_for_r3_but_never_r4(self):
        m, _ = SP.parse({"risk": "HIGH", "permissions": {"capabilities": ["files_delete"]}})
        assert m is not None and m.ceiling == T.R3
        # No risk class lifts R4: asking for a SECRET_READ tool fails the lint.
        m4, lint = SP.parse({"risk": "HIGH", "permissions": {"capabilities": ["secrets.read"]}})
        assert m4 is None and any("R4" in f for f in lint.findings)

    def test_no_manifest_means_read_only(self, perms, ladder):
        validated(ladder, "plain")
        a = perms.authority_for("plain")
        assert a.max_tier == T.R0 and a.write_paths == () and a.commands is False
        assert a.permits("files_write") is not None
        assert a.permits("files_read") is None

    def test_a_machine_path_is_refused(self):
        m, lint = SP.parse({"risk": "LOW", "permissions": {
            "filesystem": {"write": ["E:/friday-tony-stark-demo-main/friday"]}}})
        assert m is None and any("absolute path" in f for f in lint.findings)

    def test_an_escaping_path_is_refused(self):
        m, lint = SP.parse({"risk": "LOW", "permissions": {"filesystem": {"write": ["friday/../.."]}}})
        assert m is None and any("escapes" in f for f in lint.findings)

    def test_unknown_keys_are_refused_not_ignored(self):
        m, lint = SP.parse({"risk": "LOW", "permissions": {"capabilities": []}, "tools": ["Bash"]})
        assert m is None and any("unknown top-level" in f for f in lint.findings)

    def test_an_unregistered_capability_is_a_warning_not_a_grant(self):
        m, lint = SP.parse({"risk": "LOW", "permissions": {"capabilities": ["hermes_only_tool"]}})
        assert m is not None and lint.status == SP.WARN

    def test_a_failed_manifest_is_not_recorded(self, perms, ladder):
        validated(ladder, "s")
        out = perms.record("s", "risk: LOW\npermissions:\n  capabilities: ['*']\n")
        assert out["status"] == "refused" and out["lint"] == SP.FAIL
        assert perms.manifest_for("s") is None
        assert perms.authority_for("s").max_tier == T.R0

    def test_frontmatter_in_a_skill_md_is_accepted(self):
        text = "---\nrisk: LOW\npermissions:\n  capabilities: [files_read]\n---\n# Skill\nbody\n"
        m, lint = SP.parse(text)
        assert m is not None and m.capabilities == ("files_read",)


# --------------------------------------------------------------------------
# the algebra
# --------------------------------------------------------------------------

class TestIntersectionOnlyNarrows:
    def test_id_sets_meet(self):
        a = SP.Authority("worker", capabilities=frozenset({"files_read", "files_write", "web_search"}))
        b = SP.Authority("skill", capabilities=frozenset({"files_read", "files_delete"}))
        m = a.intersect(b)
        assert m.capabilities == frozenset({"files_read"})

    def test_deny_sets_join(self):
        a = SP.Authority("worker", prohibited=frozenset({"files_delete"}))
        b = SP.Authority("skill", prohibited=frozenset({"memory_forget"}))
        assert a.intersect(b).prohibited == frozenset({"files_delete", "memory_forget"})

    def test_tier_takes_the_minimum(self):
        a = SP.Authority("worker", max_tier=T.R3)
        b = SP.Authority("skill", max_tier=T.R1)
        assert a.intersect(b).max_tier == T.R1
        assert b.intersect(a).max_tier == T.R1

    def test_a_layer_with_no_opinion_does_not_widen(self):
        narrow = SP.Authority("skill", capabilities=frozenset({"files_read"}), max_tier=T.R0)
        wide = SP.Authority.unrestricted("objective")
        m = narrow.intersect(wide)
        assert m.capabilities == frozenset({"files_read"}) and m.max_tier == T.R0

    def test_a_glob_admits_only_what_the_other_side_names(self):
        a = SP.Authority("worker", capabilities=frozenset({"files_*"}))
        b = SP.Authority("skill", capabilities=frozenset({"files_read", "web_search"}))
        assert a.intersect(b).capabilities == frozenset({"files_read"})

    def test_two_different_globs_have_no_meet(self):
        a = SP.Authority("worker", capabilities=frozenset({"files_*"}))
        b = SP.Authority("skill", capabilities=frozenset({"*_read"}))
        assert a.intersect(b).capabilities == frozenset()

    def test_write_paths_meet_under_common_roots(self):
        a = SP.Authority("worker", write_paths=("friday",))
        b = SP.Authority("skill", write_paths=("friday/generated", "docs"))
        assert a.intersect(b).write_paths == ("friday/generated",)

    def test_prohibited_beats_requested(self):
        a = SP.Authority("x", capabilities=frozenset({"files_read"}), prohibited=frozenset({"files_read"}))
        assert "prohibited" in a.permits("files_read")

    def test_r4_is_never_permitted(self):
        a = SP.Authority.unrestricted("anyone")
        r4_tools = [t for t, cat in P.TOOL_CATEGORIES.items()
                    if T.tier_of_category(cat) == T.R4]
        assert r4_tools, "no R4 tool registered - the invariant has nothing to bite"
        for tool in r4_tools:
            assert a.permits(tool) is not None, tool
            # The `family_op` spelling falls through to the dot form UNLESS
            # it is registered in its own right (secrets_list lists aliases
            # only - values never enter model space - and is R0 on purpose).
            id_form = tool.replace(".", "_", 1)
            if id_form not in P.TOOL_CATEGORIES:
                assert a.permits(id_form) is not None, id_form

    def test_network_scope_refuses_a_network_capability(self):
        a = SP.Authority("skill", network=False)
        refusal = a.permits("web_search")
        assert refusal and "network" in refusal


# --------------------------------------------------------------------------
# the runtime seam
# --------------------------------------------------------------------------

class Substitute:
    """Stands in for a resolved capability and records whether it ran."""
    calls = 0

    def load(self):
        def fn(run, **kw):
            Substitute.calls += 1
            return c.succeeded(c.started(run.run_id, "files_roots"),
                               verification=c.Verification(method="test", evidence="ran"),
                               output={"ok": True})
        return fn


class TestTheRuntimeRefusesBeforeResolving:
    def test_out_of_authority_is_refused_before_the_function_loads(self, monkeypatch):
        Substitute.calls = 0
        monkeypatch.setitem(R.resolutions(), "files_roots", Substitute())
        authority = SP.Authority("skill", capabilities=frozenset({"web_search"}))
        rt = R.CapabilityRuntime(authority=authority)
        result = rt.execute("files_roots", {})
        assert result.status == c.NOT_PERMITTED
        assert "OUT_OF_AUTHORITY" in (result.error or "")
        assert Substitute.calls == 0, "the capability ran despite being out of authority"

    def test_inside_authority_the_call_proceeds(self, monkeypatch):
        Substitute.calls = 0
        monkeypatch.setitem(R.resolutions(), "files_roots", Substitute())
        authority = SP.Authority("skill", capabilities=frozenset({"files_roots"}), max_tier=T.R0)
        rt = R.CapabilityRuntime(authority=authority)
        result = rt.execute("files_roots", {})
        assert result.status == c.SUCCEEDED, result.error
        assert Substitute.calls == 1

    def test_no_authority_layer_is_the_old_behaviour(self, monkeypatch):
        Substitute.calls = 0
        monkeypatch.setitem(R.resolutions(), "files_roots", Substitute())
        assert R.CapabilityRuntime().execute("files_roots", {}).status == c.SUCCEEDED

    def test_authority_cannot_answer_policy(self, monkeypatch):
        """The layers above policy narrow; they never approve. A capability
        the manifest ALLOWS still meets the PolicyEngine, and a DENY there
        is a refusal however wide the manifest is."""
        engine = P.PolicyEngine(overrides={P.READ_LOCAL_SAFE: P.DENY})
        authority = SP.Authority.unrestricted("everything-goes")
        rt = R.CapabilityRuntime(engine=engine, authority=authority)
        result = rt.execute("files_roots", {})
        assert result.status == c.CANCELLED
        assert "APPROVAL_REQUIRED" in (result.error or "") or "denied" in (result.error or "").lower()

    def test_the_default_manifest_stops_a_write(self, perms, ladder, monkeypatch):
        """End to end: an unmanifested skill on the ladder -> read-only
        authority -> a write capability is refused before it resolves."""
        validated(ladder, "unmanifested")
        Substitute.calls = 0
        monkeypatch.setitem(R.resolutions(), "files_write", Substitute())
        rt = R.CapabilityRuntime(authority=perms.authority_for("unmanifested"))
        result = rt.execute("files_write", {"path": "x", "content": "y"})
        assert result.status == c.NOT_PERMITTED and Substitute.calls == 0


# --------------------------------------------------------------------------
# the Claude CLI boundary
# --------------------------------------------------------------------------

class TestTheClaudeAllowlistLosesWhatTheManifestWithheld:
    def test_a_read_only_skill_strips_write_tools_from_build(self, perms, ladder):
        from friday.executors import brokers as B
        validated(ladder, "reader")
        perms.record("reader", LOW_READER)         # no write scope requested
        narrowed = SP.narrow_profile(B.BUILD, perms.authority_for("reader"))
        for tool in B.WRITE_TOOLS:
            assert tool not in narrowed.allowed
        assert not any(t.startswith(("Bash(", "PowerShell(")) for t in narrowed.allowed)
        assert "Read" in narrowed.allowed
        assert narrowed.disallowed == B.BUILD.disallowed

    def test_a_medium_skill_with_write_scope_keeps_them(self, perms, ladder):
        from friday.executors import brokers as B
        validated(ladder, "builder")
        out = perms.record("builder", {"risk": "MEDIUM", "permissions": {
            "capabilities": ["files_read", "files_write"],
            "filesystem": {"write": ["friday"]}, "commands": True}})
        assert out["status"] == "recorded", out
        narrowed = SP.narrow_profile(B.BUILD, perms.authority_for("builder"))
        assert set(B.WRITE_TOOLS) <= set(narrowed.allowed)
        assert any(t.startswith("Bash(") for t in narrowed.allowed)

    def test_the_executor_narrows_its_launch(self, perms, ladder, monkeypatch, tmp_path):
        """launch_for() goes through the manifests: naming a read-only skill
        on a BUILD bundle produces a launch whose --allowedTools has no
        Write/Edit - what the CLI enforces outside the model."""
        from friday.executors import brokers as B, claude_code as CC
        from friday.skill_ladder import SkillLadder as SL
        validated(ladder, "reader")
        perms.record("reader", LOW_READER)
        db = ladder._path
        monkeypatch.setattr(SL, "__init__", lambda self, db_path=None: SL.__init__.__wrapped__(self, db)
                            if hasattr(SL.__init__, "__wrapped__") else _init_with(self, db))

        class Store:
            def remember(self, **kw): pass
            def recall(self, *a, **kw): return []

        ex = CC.ClaudeCodeExecutor(Store())
        bundle = CC.TaskBundle(goal="implement the fix", workspace=str(tmp_path), skills=("reader",))
        launch = ex.launch_for(bundle, profile=B.BUILD)
        assert "Write" not in launch.allowed_tools and "Edit" not in launch.allowed_tools
        assert "Read" in launch.allowed_tools


def _init_with(self, db):
    self._path = str(db)
    with self._connect() as conn:
        from friday import skill_ladder as sl
        conn.execute(sl._TABLE)


# --------------------------------------------------------------------------
# the Hermes contract
# --------------------------------------------------------------------------

class TestHermesGetsTheProhibitions:
    def test_prohibitions_land_in_the_bundle_contract(self, perms, ladder, monkeypatch):
        from friday import hermes_bridge as hb
        from friday.skill_ladder import SkillLadder as SL
        validated(ladder, "reader")
        perms.record("reader", LOW_READER)
        db = ladder._path
        monkeypatch.setattr(SL, "__init__", lambda self, db_path=None: _init_with(self, db))
        bundle = hb.TaskBundle(goal="look into the retrieval bug", skill_hints=("reader",))
        out = bundle.with_skill_prohibitions()
        assert any("files_delete" in d for d in out.disallowed)
        assert any("memory_forget" in d for d in out.disallowed)
        assert "PROHIBITED ACTIONS" in out.render()
        # and it composes: applying it twice adds nothing
        assert out.with_skill_prohibitions().disallowed == out.disallowed

    def test_no_hints_means_no_change(self):
        from friday import hermes_bridge as hb
        bundle = hb.TaskBundle(goal="g")
        assert bundle.with_skill_prohibitions() is bundle
