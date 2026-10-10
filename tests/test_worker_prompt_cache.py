"""ORCH-012: worker prompts keep a stable prefix and a dynamic suffix, so
prompt caching holds, and the cache hit rate is measured per worker."""
import json

from friday import execution_economics as ee
from friday import hermes_bridge as hb


def _bundle(goal, **kw):
    kw.setdefault("role", "Implementer")
    kw.setdefault("tool_scope", ("terminal", "file"))
    kw.setdefault("disallowed", ("do not push to origin",))
    return hb.TaskBundle(goal=goal, **kw)


def test_the_stable_prefix_is_byte_identical_across_different_tasks():
    a = _bundle("fix the login bug", acceptance=("login works",),
                known_facts=("run 7f3a ended at 10:42",), iteration_budget=3,
                memory_context="owner prefers tabs", allowed_paths=("src/",),
                recorded_state=("tried X, failed",), user_outcome="ship it",
                code_refs=("a.py:1",), token_budget="LOW")
    b = _bundle("write the migration docs", acceptance=("docs exist",),
                constraints=("no network",), allowed_paths=("docs/",))
    assert a.stable_prefix() == b.stable_prefix()
    assert a.stable_prefix().encode() == b.stable_prefix().encode()
    assert a.render().startswith(a.stable_prefix())
    assert b.render().startswith(b.stable_prefix())
    assert a.render() == a.stable_prefix() + hb.TaskBundle.PREFIX_SEPARATOR + a.dynamic_suffix()


def test_the_prefix_holds_rules_role_and_tools_in_a_fixed_order_and_nothing_volatile():
    b = _bundle("fix the login bug", acceptance=("login works",),
                known_facts=("run 7f3a",), recorded_state=("tried X",))
    prefix = b.stable_prefix()
    order = ["REPORTING CONTRACT", "EXECUTION POLICY", "ROLE / RESPONSIBILITY",
             "PROHIBITED ACTIONS", "ALLOWED TOOLS/MCP"]
    positions = [prefix.index(t) for t in order]
    assert positions == sorted(positions), prefix
    for volatile in ("fix the login bug", "login works", "7f3a", "tried X",
                     "GOAL", "ACCEPTANCE", "KNOWN FACTS", "RECORDED STATE",
                     "TOKEN BUDGET", "ITERATION BUDGET"):
        assert volatile not in prefix
    suffix = b.dynamic_suffix()
    assert suffix.startswith("GOAL\nfix the login bug")


def test_a_different_role_or_tool_scope_changes_only_the_prefix_that_names_it():
    a = _bundle("g")
    other_role = _bundle("g", role="Reviewer")
    assert a.stable_prefix() != other_role.stable_prefix()
    assert a.dynamic_suffix() == other_role.dynamic_suffix()


def test_cache_read_tokens_come_from_the_usage_shapes_the_runs_report():
    assert ee.cache_read_tokens({"usage": {"prompt": 10, "cache_read": 7}}) == 7
    assert ee.cache_read_tokens({"cache_read_tokens": 5}) == 5
    assert ee.cache_read_tokens({"cache_read_input_tokens": 4}) == 4
    assert ee.cache_read_tokens({"cached_tokens": 3}) == 3
    assert ee.cache_read_tokens({"usage": {"prompt": 10}}) == 0
    assert ee.cache_read_tokens({}) == 0
    assert ee.cache_read_tokens({"cache_read_tokens": "junk"}) == 0


def test_route_outcomes_record_cache_reads_and_report_a_hit_rate_per_worker(tmp_path):
    outcomes = ee.RouteOutcomes(tmp_path / "o.sqlite3")
    outcomes.record("w-warm", task_class="c", route_level="l", tier="t",
                    prompt_tokens=1000, cache_read_tokens=800, status="COMPLETE")
    outcomes.record("w-cold", task_class="c", route_level="l", tier="t",
                    prompt_tokens=1000, status="COMPLETE")
    outcomes.record("w-none", task_class="c", route_level="l", tier="t",
                    status="COMPLETE")
    rows = {r["work_run_id"]: r for r in outcomes.by_class("c")}
    assert ee.RouteOutcomes.cache_hit_rate(rows["w-warm"]) == 0.8
    assert ee.RouteOutcomes.cache_hit_rate(rows["w-cold"]) == 0.0
    assert ee.RouteOutcomes.cache_hit_rate(rows["w-none"]) is None
    assert outcomes.cache_hit_rates("w-")["w-warm"] == 0.8


def test_an_old_route_outcomes_table_gains_the_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.execute(ee._OUTCOMES_TABLE.replace(
        "    cache_read_tokens INTEGER NOT NULL DEFAULT 0,\n", ""))
    db.commit()
    db.close()
    outcomes = ee.RouteOutcomes(path)
    outcomes.record("w1", task_class="c", route_level="l", tier="t",
                    prompt_tokens=10, cache_read_tokens=5)
    assert outcomes.by_class("c")[0]["cache_read_tokens"] == 5


def test_a_terminal_run_records_its_cache_reads_from_the_session_usage(tmp_path):
    log = hb.WorkRunLog(tmp_path / "bridge.sqlite3")
    sup = hb.HermesSupervisor.__new__(hb.HermesSupervisor)
    sup.log = log
    record = {"work_run_id": "hermes-cache1", "task": "fix a bug",
              "route_reason": "", "model": "m", "provider": "p",
              "status": "COMPLETE", "started_at": 1.0, "last_event_at": 2.0,
              "usage_json": json.dumps(
                  {"usage": {"calls": 2, "prompt": 1000, "completion": 50,
                             "cache_read": 900}})}
    sup._record_route_outcome(record)
    rows = ee.RouteOutcomes(log._path).by_run_prefix("hermes-cache1")
    assert ee.RouteOutcomes.cache_hit_rate(rows[0]) == 0.9
