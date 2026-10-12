"""UCW-006: the Independent Code Review workflow (friday.code_review v1).

Risk scoring is deterministic code; the reviewers here are scripted fakes that
differ by perspective. A high-risk file buys the panel and the judge, a
docs-only change gets one economy pass, a reviewer that shares the
implementer's model or session is refused before any inference, a finding that
cites a line the diff does not show is dropped and counted, and an
INCONCLUSIVE never reads as a pass."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from friday.ultracode import definition as D
from friday.ultracode import review_workflow as R
from friday.ultracode.templates import Actor, IsolationError

_ROOT = Path(__file__).resolve().parent.parent

POLICY_DIFF = """\
diff --git a/friday/policy.py b/friday/policy.py
index 1111111..2222222 100644
--- a/friday/policy.py
+++ b/friday/policy.py
@@ -10,3 +10,3 @@ def authorize(call):
     if call.destructive:
-        return deny(call)
+        return True
     return allow(call)
"""

DOCS_DIFF = """\
diff --git a/docs/notes.md b/docs/notes.md
index 1111111..2222222 100644
--- a/docs/notes.md
+++ b/docs/notes.md
@@ -1,2 +1,3 @@
 # Notes
+A sentence with eval( and shell=True quoted as an example.
 end
"""

PLAIN_DIFF = """\
diff --git a/friday/render.py b/friday/render.py
index 1111111..2222222 100644
--- a/friday/render.py
+++ b/friday/render.py
@@ -1,3 +1,4 @@
 import os
+WIDTH = 80
 def draw():
     pass
"""

CLEAN = "VERDICT: CONFIRMED\nFINDING: NONE\nCONFIDENCE: 80"


class Fake:
    """A scripted reviewer: counts its calls and answers by perspective."""

    def __init__(self, name, model, session, answer=CLEAN):
        self.calls = 0
        self.workers = []
        self.answer = answer
        self.actor = Actor(name, model, session, self._infer)

    def _infer(self, system, user, *, worker, objective_id):
        self.calls += 1
        self.workers.append(worker)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer(user) if callable(self.answer) else self.answer


IMPLEMENTER = Actor("impl", "model-impl", "session-impl", lambda *a, **k: "")


def crew(**answers):
    """economy, three panel perspectives and a judge, all independent."""
    fakes = {
        "economy": Fake("eco", "model-eco", "s-eco"),
        "correctness": Fake("cor", "model-cor", "s-cor"),
        "security": Fake("sec", "model-sec", "s-sec"),
        "regression": Fake("reg", "model-reg", "s-reg"),
        "judge": Fake("jdg", "model-jdg", "s-jdg"),
    }
    for key, answer in answers.items():
        fakes[key].answer = answer
    return fakes


def run(diff, fakes, implementer=IMPLEMENTER):
    return asyncio.run(R.run_code_review(
        diff, implementer=implementer, economy=fakes["economy"].actor,
        panel={k: fakes[k].actor for k in ("correctness", "security", "regression")},
        judge=fakes["judge"].actor))


def calls(fakes):
    return {k: f.calls for k, f in fakes.items()}


def routes_for(fakes):
    return {
        "economy": [fakes["economy"].actor.model],
        "standard": [fakes[k].actor.model for k in ("correctness", "security", "regression")],
        "deep": [fakes["judge"].actor.model],
    }


def available_for(fakes):
    return frozenset(actor.actor.model for actor in fakes.values())


def security_sees_flaw(user: str) -> str:
    if "return True" in user:
        return ("VERDICT: DISPUTED\n"
                "FINDING: friday/policy.py:12 a destructive call is now always allowed\n"
                "CONFIDENCE: 90")
    return CLEAN


# --- the definition ----------------------------------------------------------------

def test_the_definition_compiles_and_names_no_model():
    compiled = D.compile_definition(D.parse_definition(R.DEFINITION))
    assert compiled.definition.workflow_id == "friday.code_review"
    assert compiled.definition.version == 1
    roles = {s.id: s.role for s in compiled.definition.steps}
    assert roles["score"] == "risk_scorer" and roles["judge"] == "judge"
    kinds = {s.id: s.kind for s in compiled.definition.steps}
    assert kinds["score"] == "deterministic" and kinds["verdict"] == "deterministic"
    tiers = {s.id: s.tier for s in compiled.definition.steps if s.kind == "agent"}
    assert (tiers["economy"], tiers["panel"], tiers["judge"]) == ("economy", "standard", "deep")


def test_the_json_file_is_the_definition():
    path = _ROOT / "docs" / "plans" / "ultracode" / "workflows" / "code_review.json"
    assert json.loads(path.read_text(encoding="utf-8")) == R.DEFINITION


# --- deterministic risk scoring ----------------------------------------------------

def test_policy_file_scores_high_and_routes_to_the_panel():
    scored = R.score_diff(POLICY_DIFF)
    assert scored.route == R.ROUTE_PANEL
    assert scored.files[0].path == "friday/policy.py"
    assert scored.files[0].score >= R.HIGH_RISK


def test_docs_only_scores_zero_even_when_the_text_quotes_dangerous_code():
    scored = R.score_diff(DOCS_DIFF)
    assert scored.route == R.ROUTE_ECONOMY and scored.docs_only
    assert [f.score for f in scored.files] == [0]


def test_plain_code_is_economy_and_a_dangerous_pattern_is_not():
    assert R.score_diff(PLAIN_DIFF).route == R.ROUTE_ECONOMY
    risky = PLAIN_DIFF.replace("+WIDTH = 80", "+subprocess.run(cmd, shell=True)")
    scored = R.score_diff(risky)
    assert scored.route == R.ROUTE_PANEL
    assert any("shell=True" in r for r in scored.files[0].reasons)


def test_a_secret_literal_in_ordinary_code_routes_to_the_panel():
    leaky = PLAIN_DIFF.replace("+WIDTH = 80", '+api_key = "sk-live-0123456789"')
    assert R.score_diff(leaky).route == R.ROUTE_PANEL


# --- the acceptance ----------------------------------------------------------------

def test_a_planted_flaw_in_a_high_risk_file_is_flagged():
    fakes = crew(security=security_sees_flaw)
    result = run(POLICY_DIFF, fakes)
    assert result.route == R.ROUTE_PANEL
    assert result.verdict == "DISPUTED" and not result.passes
    assert [(f.file, f.line) for f in result.findings] == [("friday/policy.py", 12)]
    assert result.findings[0].by == "security"
    assert result.dropped == 0
    assert calls(fakes) == {"economy": 0, "correctness": 1, "security": 1,
                            "regression": 1, "judge": 1}


def test_a_clean_high_risk_change_is_confirmed_by_the_panel_and_judge():
    fakes = crew()
    result = run(POLICY_DIFF, fakes)
    assert result.verdict == "CONFIRMED" and result.passes
    assert calls(fakes)["judge"] == 1


def test_the_judge_sees_the_panel_findings_and_can_add_its_own():
    fakes = crew(security=security_sees_flaw, judge=lambda user: (
        "VERDICT: DISPUTED\nFINDING: friday/policy.py:11 the deny path is gone\n"
        if "always allowed" in user else CLEAN))
    result = run(POLICY_DIFF, fakes)
    assert {(f.line, f.by) for f in result.findings} == {(12, "security"), (11, "judge")}


def test_a_docs_only_diff_gets_one_economy_pass_not_the_panel():
    fakes = crew()
    result = run(DOCS_DIFF, fakes)
    assert result.route == R.ROUTE_ECONOMY
    assert result.verdict == "CONFIRMED"
    assert calls(fakes) == {"economy": 1, "correctness": 0, "security": 0,
                            "regression": 0, "judge": 0}


def test_plain_code_gets_one_economy_pass():
    fakes = crew()
    assert run(PLAIN_DIFF, fakes).route == R.ROUTE_ECONOMY
    assert calls(fakes)["economy"] == 1 and calls(fakes)["judge"] == 0


def test_review_steps_resolve_their_tier_models_and_caps_before_calling_actors():
    fakes = crew()
    result = asyncio.run(R.run_code_review(
        POLICY_DIFF, implementer=IMPLEMENTER, economy=fakes["economy"].actor,
        panel={k: fakes[k].actor for k in ("correctness", "security", "regression")},
        judge=fakes["judge"].actor, routes=routes_for(fakes), available=available_for(fakes)))
    assert result.verdict == "CONFIRMED"
    assert fakes["economy"].workers == []
    assert fakes["security"].workers == [
        "perspective:sec|tier=standard|model=model-sec|cap=24000"]
    assert fakes["judge"].workers == [
        "perspective:jdg|tier=deep|model=model-jdg|cap=12000"]


def test_an_unavailable_review_route_is_inconclusive_and_calls_nobody():
    fakes = crew()
    result = asyncio.run(R.run_code_review(
        DOCS_DIFF, implementer=IMPLEMENTER, economy=fakes["economy"].actor,
        panel={k: fakes[k].actor for k in ("correctness", "security", "regression")},
        judge=fakes["judge"].actor, routes=routes_for(fakes), available=frozenset()))
    assert result.verdict == "INCONCLUSIVE" and not result.passes
    assert "no available economy route" in result.reason
    assert sum(calls(fakes).values()) == 0


def test_a_resolved_model_that_is_not_the_actor_is_refused_before_inference():
    fakes = crew()
    routes = routes_for(fakes)
    routes["economy"] = ["aaa-other", fakes["economy"].actor.model]
    available = available_for(fakes) | {"aaa-other"}
    with pytest.raises(RuntimeError, match="aaa-other"):
        asyncio.run(R.run_code_review(
            DOCS_DIFF, implementer=IMPLEMENTER, economy=fakes["economy"].actor,
            panel={k: fakes[k].actor for k in ("correctness", "security", "regression")},
            judge=fakes["judge"].actor, routes=routes, available=available))
    assert sum(calls(fakes).values()) == 0 and fakes["economy"].workers == []


def test_a_route_the_risk_route_never_uses_does_not_block_the_review():
    fakes = crew()
    routes = routes_for(fakes)
    routes["deep"] = []
    result = asyncio.run(R.run_code_review(
        DOCS_DIFF, implementer=IMPLEMENTER, economy=fakes["economy"].actor,
        panel={k: fakes[k].actor for k in ("correctness", "security", "regression")},
        judge=fakes["judge"].actor, routes=routes, available=available_for(fakes)))
    assert result.route == R.ROUTE_ECONOMY and result.verdict == "CONFIRMED"
    assert calls(fakes)["economy"] == 1 and calls(fakes)["judge"] == 0


def test_an_unused_economy_route_does_not_block_a_panel_review():
    fakes = crew()
    routes = routes_for(fakes)
    routes["economy"] = []
    result = asyncio.run(R.run_code_review(
        POLICY_DIFF, implementer=IMPLEMENTER, economy=fakes["economy"].actor,
        panel={k: fakes[k].actor for k in ("correctness", "security", "regression")},
        judge=fakes["judge"].actor, routes=routes, available=available_for(fakes)))
    assert result.route == R.ROUTE_PANEL and result.verdict == "CONFIRMED"
    assert calls(fakes)["economy"] == 0 and calls(fakes)["judge"] == 1


def test_the_step_cap_is_enforced_before_inference(monkeypatch):
    patched = copy.deepcopy(R.DEFINITION)
    patched["steps"][1]["cap"] = 10
    monkeypatch.setattr(R, "DEFINITION", patched)
    fakes = crew()
    result = asyncio.run(R.run_code_review(
        DOCS_DIFF, implementer=IMPLEMENTER, economy=fakes["economy"].actor,
        panel={k: fakes[k].actor for k in ("correctness", "security", "regression")},
        judge=fakes["judge"].actor, routes=routes_for(fakes), available=available_for(fakes)))
    assert result.verdict == "INCONCLUSIVE" and not result.passes
    assert "cap 10" in result.reason
    assert calls(fakes)["economy"] == 0


@pytest.mark.parametrize("slot,field,value,word", [
    ("economy", "model", "MODEL-IMPL", "model"),
    ("security", "model", "model-impl", "model"),
    ("judge", "session", "session-impl", "session"),
    ("regression", "session", " Session-Impl ", "session"),
])
def test_a_reviewer_on_the_implementers_model_or_session_is_refused(
        slot, field, value, word):
    fakes = crew()
    old = fakes[slot].actor
    fakes[slot].actor = Actor(old.name, value if field == "model" else old.model,
                              value if field == "session" else old.session, old.infer)
    with pytest.raises(IsolationError, match=word):
        run(POLICY_DIFF, fakes)
    assert sum(calls(fakes).values()) == 0


def test_a_reviewer_that_states_no_session_is_refused():
    fakes = crew()
    old = fakes["economy"].actor
    fakes["economy"].actor = Actor(old.name, old.model, "", old.infer)
    with pytest.raises(IsolationError):
        run(DOCS_DIFF, fakes)


def test_a_finding_citing_a_line_not_in_the_diff_is_dropped_and_counted():
    answer = ("VERDICT: DISPUTED\n"
              "FINDING: friday/policy.py:12 real flaw\n"
              "FINDING: friday/policy.py:999 invented line\n"
              "FINDING: friday/other.py:3 a file that is not in the diff\n"
              "FINDING: no citation at all\n")
    fakes = crew(security=answer)
    result = run(POLICY_DIFF, fakes)
    assert [(f.file, f.line) for f in result.findings] == [("friday/policy.py", 12)]
    assert result.dropped == 3
    assert result.verdict == "DISPUTED"


def test_a_dispute_resting_only_on_a_dropped_finding_is_inconclusive_not_a_pass():
    fakes = crew(security="VERDICT: DISPUTED\nFINDING: friday/nowhere.py:1 invented\n")
    result = run(POLICY_DIFF, fakes)
    assert result.findings == () and result.dropped == 1
    assert result.verdict == "INCONCLUSIVE" and not result.passes


def test_a_missing_reviewer_makes_the_verdict_inconclusive_never_a_pass():
    fakes = crew(regression=RuntimeError("route down"))
    result = run(POLICY_DIFF, fakes)
    assert result.verdict == "INCONCLUSIVE" and not result.passes
    assert "regression" in result.reason


def test_an_unavailable_economy_reviewer_is_inconclusive():
    fakes = crew(economy=RuntimeError("route down"))
    result = run(DOCS_DIFF, fakes)
    assert result.verdict == "INCONCLUSIVE" and not result.passes


def test_an_empty_diff_is_inconclusive_and_calls_nobody():
    fakes = crew()
    result = run("", fakes)
    assert result.verdict == "INCONCLUSIVE" and not result.passes
    assert sum(calls(fakes).values()) == 0


def test_the_panel_needs_distinct_perspectives():
    fakes = crew()
    with pytest.raises(ValueError, match="perspective"):
        asyncio.run(R.run_code_review(
            POLICY_DIFF, implementer=IMPLEMENTER, economy=fakes["economy"].actor,
            panel={"security": fakes["security"].actor}, judge=fakes["judge"].actor))


def test_a_result_serialises_for_the_record():
    result = run(POLICY_DIFF, crew(security=security_sees_flaw))
    data = json.loads(json.dumps(result.to_dict()))
    assert data["verdict"] == "DISPUTED" and data["route"] == "panel"
    assert data["findings"][0]["file"] == "friday/policy.py"


# --- what a reviewer cannot read is never a pass -----------------------------------

def lines(*parts):
    return "\n".join(parts) + "\n"


def added(path, *body, new=False):
    """A one-hunk git diff adding `body` after one context line."""
    head = [f"diff --git a/{path} b/{path}"]
    if new:
        head += ["new file mode 100644", "--- /dev/null", f"+++ b/{path}",
                 f"@@ -0,0 +1,{len(body)} @@"]
        return lines(*head, *[f"+{b}" for b in body])
    head += [f"--- a/{path}", f"+++ b/{path}", f"@@ -1,1 +1,{1 + len(body)} @@", " keep"]
    return lines(*head, *[f"+{b}" for b in body])


BINARY_DIFF = lines("diff --git a/friday/tools/run.exe b/friday/tools/run.exe",
                    "new file mode 100755", "index 0000000..1111111",
                    "Binary files /dev/null and b/friday/tools/run.exe differ")


def test_a_binary_file_next_to_a_docs_change_is_not_confirmed_and_is_listed():
    fakes = crew()
    result = run(DOCS_DIFF + BINARY_DIFF, fakes)
    assert [f.path for f in result.files] == ["docs/notes.md", "friday/tools/run.exe"]
    assert result.verdict == "INCONCLUSIVE" and not result.passes
    assert "run.exe" in result.reason and "binary" in result.reason


def test_a_mode_only_change_is_inconclusive_and_named():
    mode = lines("diff --git a/scripts/run.sh b/scripts/run.sh",
                 "old mode 100644", "new mode 100755")
    result = run(PLAIN_DIFF + mode, crew())
    assert result.verdict == "INCONCLUSIVE" and not result.passes
    assert "scripts/run.sh" in result.reason


def test_a_rename_only_change_is_scored_by_both_paths_and_inconclusive():
    rename = lines("diff --git a/friday/policy.py b/friday/rules.py", "similarity index 100%",
                   "rename from friday/policy.py", "rename to friday/rules.py")
    scored = R.score_diff(rename)
    assert [f.path for f in scored.files] == ["friday/rules.py"]
    assert scored.route == R.ROUTE_PANEL            # the old name was a boundary
    result = run(rename, crew())
    assert result.verdict == "INCONCLUSIVE" and not result.passes


def test_an_empty_new_file_is_a_reviewable_change():
    empty = lines("diff --git a/friday/pkg/__init__.py b/friday/pkg/__init__.py",
                  "new file mode 100644", "index 0000000..e69de29")
    assert run(empty, crew()).verdict == "CONFIRMED"


def test_a_hunk_cut_short_is_inconclusive():
    cut = lines("diff --git a/friday/render.py b/friday/render.py",
                "--- a/friday/render.py", "+++ b/friday/render.py",
                "@@ -1,3 +1,4 @@", " import os", "+WIDTH = 80")
    result = run(cut, crew())
    assert result.verdict == "INCONCLUSIVE" and "shorter" in result.reason


def test_content_beyond_the_declared_hunk_is_inconclusive():
    extra = lines("diff --git a/friday/render.py b/friday/render.py",
                  "--- a/friday/render.py", "+++ b/friday/render.py",
                  "@@ -1,1 +1,2 @@", " import os", "+WIDTH = 80",
                  "+import subprocess")
    result = run(extra, crew())
    assert result.verdict == "INCONCLUSIVE" and "beyond" in result.reason


def test_a_hunk_cut_by_the_next_file_does_not_swallow_its_header():
    cut = lines("diff --git a/friday/render.py b/friday/render.py",
                "--- a/friday/render.py", "+++ b/friday/render.py",
                "@@ -1,3 +1,4 @@", " import os", "+WIDTH = 80")
    scored = R.score_diff(cut + POLICY_DIFF)
    assert [f.path for f in scored.files] == ["friday/render.py", "friday/policy.py"]
    assert "shorter" in scored.files[0].unreviewed


def test_a_format_patch_signature_is_not_content_beyond_the_hunk():
    patch = PLAIN_DIFF + "-- \n2.43.0\n"
    assert run(patch, crew()).verdict == "CONFIRMED"


def test_a_removed_line_that_looks_like_a_header_stays_a_removed_line():
    tricky = lines("diff --git a/friday/render.py b/friday/render.py",
                   "--- a/friday/render.py", "+++ b/friday/render.py",
                   "@@ -1,2 +1,1 @@", "--- x", " keep")
    scored = R.score_diff(tricky)
    assert [f.path for f in scored.files] == ["friday/render.py"]
    assert scored.files[0].unreviewed == ""


def test_a_file_longer_than_the_evidence_limit_is_inconclusive():
    big = added("friday/render.py", *[f"X{n} = {n}" for n in range(700)])
    result = run(big, crew())
    assert result.verdict == "INCONCLUSIVE" and "shown to reviewers" in result.reason


def test_a_clean_multi_file_git_diff_lists_every_file():
    scored = R.score_diff(PLAIN_DIFF + DOCS_DIFF + POLICY_DIFF)
    assert [f.path for f in scored.files] == ["friday/render.py", "docs/notes.md",
                                              "friday/policy.py"]
    assert all(f.unreviewed == "" for f in scored.files)


# --- secrets are high risk wherever they land --------------------------------------

@pytest.mark.parametrize("path,line", [
    ("docs/setup.md", 'api_key = "sk-live-0123456789abcdef"'),
    ("docs/setup.md", "export OPENAI_API_KEY=sk-live-0123456789abcdef"),
    (".env", "OPENAI_API_KEY=live0123456789abcdef"),
    ("deploy/app.yaml", "api_key: live0123456789abcdef"),
    ("deploy/app.json", '"api_key": "live0123456789"'),
    ("friday/render.py", 'KEY = "ghp_0123456789abcdefghijABCDEFGHIJ"'),
    ("friday/render.py", "AWS = 'AKIAABCDEFGHIJKLMNOP'"),
    ("notes/keys.txt", "-----BEGIN RSA PRIVATE KEY-----"),
])
def test_a_secret_added_anywhere_routes_to_the_panel(path, line):
    scored = R.score_diff(added(path, line))
    assert scored.route == R.ROUTE_PANEL, scored.files[0].reasons
    assert not scored.docs_only
    assert any("secret" in r for r in scored.files[0].reasons)


def test_a_secret_in_a_docs_file_gets_the_panel_not_one_pass():
    fakes = crew()
    run(added("docs/setup.md", 'password = "hunter2hunter2"'), fakes)
    assert calls(fakes)["economy"] == 0 and calls(fakes)["judge"] == 1


@pytest.mark.parametrize("path", [".env", ".env.production", "certs/server.pem", "id_rsa"])
def test_a_secrets_file_is_a_boundary_by_its_path(path):
    assert R.score_diff(added(path, "x = 1")).route == R.ROUTE_PANEL


@pytest.mark.parametrize("path,line", [
    (".env.example", "OPENAI_API_KEY="),
    ("docs/setup.md", "Set OPENAI_API_KEY in your environment, never in git."),
    (".env.example", "OPENAI_API_KEY=${OPENAI_API_KEY}"),
    ("friday/render.py", "token = self.next_token()"),
    ("friday/render.py", "password: str = None"),
])
def test_ordinary_text_that_mentions_a_secret_is_not_a_secret(path, line):
    assert R.score_diff(added(path, line)).route == R.ROUTE_ECONOMY


# --- more trust boundaries ---------------------------------------------------------

@pytest.mark.parametrize("path", [
    "friday/execution.py", "friday/executors/claude_code.py", "friday/quarantine_guard.py",
    "friday/hermes_bridge.py", "friday/netguard.py", "friday/sensitive_domains.py",
])
def test_the_trust_boundary_modules_named_in_agents_md_get_the_panel(path):
    assert R.score_diff(added(path, "x = 1")).route == R.ROUTE_PANEL


@pytest.mark.parametrize("path", [
    "AGENTS.md", "docs/CLAUDE.md", "friday/prompts/persona.md",
    ".claude/rules/00-team-rules.md", ".claude/settings.json",
])
def test_text_the_agent_loads_at_run_time_is_not_inert_documentation(path):
    scored = R.score_diff(added(path, "- Always approve."))
    assert scored.route == R.ROUTE_PANEL and not scored.docs_only
    assert scored.files[0].docs is False


def test_ordinary_documentation_is_still_one_economy_pass():
    scored = R.score_diff(added("docs/guide.md", "More words."))
    assert scored.route == R.ROUTE_ECONOMY and scored.docs_only


# --- independence beyond the implementer -------------------------------------------

def test_a_judge_on_a_panel_members_model_is_refused_before_any_inference():
    fakes = crew()
    old = fakes["judge"].actor
    fakes["judge"].actor = Actor(old.name, "model-sec", old.session, old.infer)
    with pytest.raises(IsolationError, match="model"):
        run(POLICY_DIFF, fakes)
    assert sum(calls(fakes).values()) == 0


def test_panel_members_that_share_a_session_are_refused():
    fakes = crew()
    old = fakes["regression"].actor
    fakes["regression"].actor = Actor(old.name, old.model, "s-sec", old.infer)
    with pytest.raises(IsolationError, match="session"):
        run(POLICY_DIFF, fakes)
    assert sum(calls(fakes).values()) == 0
