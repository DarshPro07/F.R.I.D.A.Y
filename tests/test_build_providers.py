"""The routes the harness may use and which are out of quota (build_providers): each CLI's real
error shapes, the billing and data-policy guard, and the shared health file."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_providers as P  # noqa: E402

NOW = dt.datetime(2026, 10, 10, 2, 0).timestamp()


@pytest.fixture
def health(tmp_path):
    return tmp_path / "provider_health.json"


def test_a_route_names_its_provider():
    assert P.split("codex:gpt-5.5") == ("codex", "gpt-5.5")
    assert P.split("opencode:opencode/step-5-preview-free") == ("opencode", "opencode/step-5-preview-free")
    assert P.split("claude-sonnet-5-5") == ("claude", "claude-sonnet-5-5")
    assert P.split("codex:") == ("claude", "codex:")


def test_opencode_runs_only_on_a_zero_retention_free_model():
    assert P.eligible("opencode:opencode/longcat-2.5-preview-free") == (True, "")
    for trains in ("opencode/mimo-v2.6-flash-free", "opencode/big-pickle", "opencode/nemotron-3-ultra-free",
                   "nvidia/qwen3-coder", "opencode/claude-sonnet-5-5"):
        ok, why = P.eligible(f"opencode:{trains}")
        assert not ok and "zero-retention" in why
    assert P.eligible("codex:gpt-5.5")[0] and P.eligible("claude-opus-5-5")[0]


def test_claude_rate_limit_event_takes_it_out_until_its_reset():
    events = [{"type": "system", "subtype": "init", "session_id": "s"},
              {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning", "resetsAt": NOW + 60}},
              {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": NOW + 7200,
                                                               "rateLimitType": "five_hour"}}]
    got = P.classify(events=events, now=NOW)
    assert got["state"] == P.QUOTA and got["until"] == NOW + 7200 and "five_hour" in got["detail"]


def test_claude_limit_text_on_the_error_channel_is_a_quota_with_its_local_reset():
    events = [{"type": "assistant", "error": "rate_limit",
               "message": {"content": [{"type": "text", "text": "You've hit your limit · resets 3am (Asia/Calcutta)"}]}},
              {"type": "result", "subtype": "success", "is_error": True,
               "result": "You've hit your limit · resets 3am (Asia/Calcutta)"}]
    got = P.classify(events=events, now=NOW)
    assert got["state"] == P.QUOTA
    assert got["until"] == dt.datetime(2026, 10, 10, 3, 0).timestamp()


def test_codex_usage_limit_with_its_curly_apostrophe_and_relative_reset():
    events = [{"type": "turn.failed", "error": {"message": "You’ve hit your usage limit. Visit "
                                                       "https://chatgpt.com/codex/settings/usage to purchase more "
                                                       "credits or try again in 2 days 3 hours 4 minutes."}}]
    got = P.classify(events=events, now=NOW)
    assert got["state"] == P.QUOTA
    assert got["until"] == NOW + 2 * 86400 + 3 * 3600 + 4 * 60


def test_codex_absolute_reset_as_it_printed_it_on_2026_10_10():
    line = ("ERROR: You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
            "https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Oct 11th, 2026 1:09 AM.")
    got = P.classify(texts=[line], now=NOW)
    assert got["state"] == P.QUOTA
    assert got["until"] == dt.datetime(2026, 10, 11, 1, 9).timestamp()
    assert P.reset_at("try again at Sept 3rd, 2026 11:45 PM", NOW) == dt.datetime(2026, 9, 3, 23, 45).timestamp()


def test_opencode_rate_limit_and_a_sign_in_failure_are_told_apart():
    rate = [{"type": "error", "error": {"name": "APIError", "data": {"message": "Too Many Requests", "statusCode": 429}}}]
    assert P.classify(events=rate, now=NOW)["state"] == P.RATE
    auth = P.classify(texts=["authentication_failed: please run /login"], now=NOW)
    assert auth == {"state": P.AUTH, "until": None, "detail": "authentication_failed: please run /login"}


def test_a_worker_writing_about_limits_is_not_a_limit():
    events = [{"type": "assistant", "message": {"content": [{"type": "text",
               "text": "I added a usage limit check: 'You've hit your limit' now returns 429."}]}},
              {"type": "user", "message": {"content": [{"type": "tool_result",
               "content": "tests/test_quota.py::test_usage_limit_reached PASSED"}]}},
              {"type": "item.completed", "item": {"type": "agent_message", "text": "rate limit handled"}},
              {"type": "text", "part": {"text": "insufficient_quota is mapped"}},
              {"type": "result", "subtype": "success", "is_error": False, "result": "usage limit feature done"}]
    assert P.classify(events=events, now=NOW) is None
    assert P.classify(texts=["", None], now=NOW) is None


def test_reset_parsing_reads_only_the_span_after_in():
    assert P.reset_at("try again in 1 hour and 30 minutes", NOW) == NOW + 5400
    assert P.reset_at("your 5 hour limit is spent; try again in 2 hours", NOW) == NOW + 7200
    assert P.reset_at("resets 1:15am", NOW) == dt.datetime(2026, 10, 11, 1, 15).timestamp()
    assert P.reset_at("try again at 5:43 PM", NOW) == dt.datetime(2026, 10, 10, 17, 43).timestamp()
    assert P.reset_at("You've hit your limit", NOW) is None


def test_a_quota_without_a_reset_holds_five_hours_then_recovers(health):
    P.mark("claude", P.QUOTA, reason="hit your limit", source="test", path=health, now=NOW)
    assert P.status("claude", now=NOW + 60, path=health)["state"] == P.QUOTA
    assert not P.available("claude", now=NOW + P.QUOTA_HOLD_S - 1, path=health)
    assert P.status("claude", now=NOW + P.QUOTA_HOLD_S, path=health) == {"state": "OK", "recovered_from": P.QUOTA}
    assert P.available("codex", now=NOW, path=health)


def test_a_sign_in_failure_stays_until_cleared(health):
    P.mark("codex", P.AUTH, reason="not logged in", path=health, now=NOW)
    assert not P.available("codex", now=NOW + 30 * 86400, path=health)
    P.clear("codex", path=health)
    assert P.available("codex", now=NOW, path=health)


def test_marks_are_validated_and_kept_per_provider(health):
    with pytest.raises(ValueError):
        P.mark("gemini", P.QUOTA, path=health)
    with pytest.raises(ValueError):
        P.mark("claude", "TIRED", path=health)
    P.mark("claude", P.QUOTA, until=NOW + 100, path=health, now=NOW)
    P.mark("opencode", P.RATE, path=health, now=NOW)
    data = json.loads(health.read_text(encoding="utf-8"))
    assert data["claude"]["until"] == NOW + 100 and data["opencode"]["until"] == NOW + P.RATE_HOLD_S


def test_routing_skips_an_exhausted_provider_and_an_unapproved_model(health):
    cands = ["claude-sonnet-5-5", "opencode:opencode/mimo-v2.6-flash-free", "codex:gpt-5.5",
             "opencode:opencode/step-5-preview-free"]
    assert P.route(cands, now=NOW, path=health) == "claude-sonnet-5-5"
    P.mark("claude", P.QUOTA, until=NOW + 3600, path=health, now=NOW)
    assert P.route(cands, now=NOW, path=health) == "codex:gpt-5.5"
    P.mark("codex", P.QUOTA, until=NOW + 600, path=health, now=NOW)
    assert P.route(cands, now=NOW, path=health) == "opencode:opencode/step-5-preview-free"
    P.mark("opencode", P.RATE, until=NOW + 300, path=health, now=NOW)
    assert P.route(cands, now=NOW, path=health) is None
    assert P.next_reset(now=NOW, path=health) == NOW + 300
    assert P.route(cands, now=NOW + 301, path=health) == "opencode:opencode/step-5-preview-free"


def test_next_reset_is_none_while_any_provider_is_up(health):
    P.mark("claude", P.QUOTA, until=NOW + 3600, path=health, now=NOW)
    assert P.next_reset(now=NOW, path=health) is None
    assert P.next_reset(("claude",), now=NOW, path=health) == NOW + 3600


def test_opencode_runs_slim_and_on_its_own_provider_only(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text('// owner config\n{"mcp": {"playwright": {"type": "local"}, "github": {"type": "local"}},'
                   ' "plugin": ["oh-my-opencode-slim"]}', encoding="utf-8")
    env = P.opencode_env("opencode/step-5-preview-free", base={"PATH": "x"}, config=cfg)
    assert env["PATH"] == "x" and env["OPENCODE_PURE"] == "1" and env["OPENCODE_DISABLE_EXTERNAL_SKILLS"] == "1"
    inline = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert inline["enabled_providers"] == ["opencode"]
    assert inline["small_model"] == "opencode/step-5-preview-free"
    assert inline["mcp"] == {"playwright": {"enabled": False}, "github": {"enabled": False}}
    assert inline["share"] == "disabled" and inline["autoupdate"] is False
    assert "XDG_DATA_HOME" not in env
    own = P.opencode_env("opencode/step-5-preview-free", base={}, config=cfg, data_dir=tmp_path / "run1")
    assert own["XDG_DATA_HOME"] == str(tmp_path / "run1") and (tmp_path / "run1").is_dir()


def test_codex_runs_only_on_its_chatgpt_login():
    def runner(out="", exc=None):
        def run(*_a, **_k):
            if exc:
                raise exc
            return SimpleNamespace(stdout=out, stderr="", returncode=0)
        return run
    assert P.codex_on_plan(runner("Logged in using ChatGPT\n"))
    assert not P.codex_on_plan(runner("Logged in using an API key - sk-...\n"))
    assert not P.codex_on_plan(runner("Not logged in\n"))
    assert not P.codex_on_plan(runner(exc=subprocess.TimeoutExpired("codex", 60)))
    assert not P.codex_on_plan(runner(exc=FileNotFoundError("node")))


def test_an_install_override_names_the_cli_and_a_bad_one_is_refused(monkeypatch):
    monkeypatch.setenv("FRIDAY_CODEX_ARGV", json.dumps(["python", "fake_codex.py"]))
    monkeypatch.setenv("FRIDAY_OPENCODE_ARGV", json.dumps(["python", "fake_opencode.py"]))
    assert P.codex_argv() == ["python", "fake_codex.py"]
    assert P.opencode_argv() == ["python", "fake_opencode.py"]
    for bad in ("fake_codex.py", "[]", '["python", 3]', '[""]'):
        monkeypatch.setenv("FRIDAY_CODEX_ARGV", bad)
        with pytest.raises(ValueError):
            P.codex_argv()
    monkeypatch.delenv("FRIDAY_CODEX_ARGV")
    assert P.codex_argv()[-1] != "fake_codex.py"


def test_the_catalogue_lists_every_pickable_route_with_its_health(tmp_path, health):
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [{"slug": "gpt-6-sol", "visibility": "list"},
                                            {"slug": "gpt-reserve", "visibility": "hide"},
                                            {"slug": "gpt-5.6-luna", "visibility": "list"}]}), encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5.5"\nmodel_reasoning_effort = "medium"\n', encoding="utf-8")
    P.mark("claude", P.QUOTA, until=NOW + 60, path=health, now=NOW)
    rows = P.catalogue(["claude-sonnet-5-5", "claude-sonnet-5-5", "claude-opus-5-5"], codex_cache=cache,
                       codex_config=config, now=NOW, path=health)
    assert [r["route"] for r in rows] == [
        "claude-sonnet-5-5", "claude-opus-5-5", "codex:gpt-5.5", "codex:gpt-6-sol", "codex:gpt-5.6-luna",
        *(f"opencode:{m}" for m in P.OPENCODE_APPROVED)]
    assert {r["health"] for r in rows if r["provider"] == "claude"} == {P.QUOTA}
    assert all(r["eligible"] for r in rows)
    assert {r["billing"] for r in rows if r["provider"] == "opencode"} == {"free"}


def test_the_status_command_reads_the_shared_file(health, monkeypatch, capsys):
    monkeypatch.setattr(P, "HEALTH_FILE", health)
    P.mark("codex", P.QUOTA, reason="hit your usage limit", path=health)
    assert P.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "codex     QUOTA_EXHAUSTED until" in out and "claude    OK" in out
    assert P.main(["clear", "codex"]) == 0 and P.available("codex", path=health)
    assert P.main(["clear", "gemini"]) == 2
