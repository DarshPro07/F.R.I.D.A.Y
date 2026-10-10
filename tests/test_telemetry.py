"""
OTEL-001: a local span recorder in the OpenTelemetry GenAI conventions.

  * a run's span tree attributes its wall time per component,
  * a default-configuration trace holds no prompt or completion text,
  * a planted dummy secret never appears, content capture on or off,
  * the coding-agent executor emits a live span.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from friday import contracts as c
from friday import telemetry
from friday.executors import cli
from friday.executors.claude_code import ClaudeCodeExecutor, TaskBundle
from friday.store import Store

# Token-shaped so it is caught by shape, assembled so no scanner flags the file.
DUMMY_SECRET = "sk-ant-" + "DUMMYSECRET0123456789abcdef"
PROMPT_TEXT = "summarise the quarterly board minutes for the owner"
COMPLETION_TEXT = "the board approved the budget unanimously"


class FakeClock:
    """Time moves only when a test says so, in milliseconds."""

    def __init__(self) -> None:
        self.ns = 0

    def __call__(self) -> int:
        return self.ns

    def tick(self, ms: int) -> None:
        self.ns += ms * 1_000_000


def _run_tree(rec, clock):
    with rec.span("invoke_agent friday", component="runtime",
                  attributes={"gen_ai.operation.name": "invoke_agent"}) as root:
        clock.tick(10)
        with rec.span("chat gemini", component="model_gateway",
                      attributes={"gen_ai.operation.name": "chat",
                                  "gen_ai.request.model": "gemini-x",
                                  "gen_ai.usage.input_tokens": 120,
                                  "gen_ai.usage.output_tokens": 30}) as model:
            clock.tick(70)
            model.set("gen_ai.prompt", PROMPT_TEXT)
            model.set("gen_ai.completion", COMPLETION_TEXT)
        with rec.span("execute_tool files_read", component="tools",
                      attributes={"gen_ai.operation.name": "execute_tool"}):
            clock.tick(15)
        clock.tick(5)
    return root


def test_the_span_tree_attributes_wall_time_per_component():
    clock = FakeClock()
    rec = telemetry.Recorder(clock=clock)
    root = _run_tree(rec, clock)

    spans = rec.trace(root.trace_id)
    assert len(spans) == 3
    by_name = {s.name: s for s in spans}
    assert by_name["chat gemini"].parent_id == root.span_id
    assert by_name["execute_tool files_read"].parent_id == root.span_id
    assert root.parent_id is None

    times = rec.component_time_ms(root.trace_id)
    # The parent keeps only its own time (10 + 5), children keep theirs.
    assert times == {"runtime": 15.0, "model_gateway": 70.0, "tools": 15.0}
    assert sum(times.values()) == rec.duration_ms(root.trace_id) == 100.0


def test_a_default_trace_holds_no_prompt_text(tmp_path):
    clock = FakeClock()
    out = tmp_path / "spans.jsonl"
    rec = telemetry.Recorder(path=out, clock=clock)
    assert rec.capture_content is False
    _run_tree(rec, clock)

    text = out.read_text(encoding="utf-8")
    assert PROMPT_TEXT not in text and COMPLETION_TEXT not in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert len(rows) == 3
    chat = next(r for r in rows if r["name"] == "chat gemini")
    # Metadata stays, in the GenAI convention names.
    assert chat["attributes"]["gen_ai.request.model"] == "gemini-x"
    assert chat["attributes"]["gen_ai.usage.input_tokens"] == 120
    assert "gen_ai.prompt" not in chat["attributes"]


def test_content_appears_only_when_explicitly_enabled():
    clock = FakeClock()
    rec = telemetry.Recorder(capture_content=True, clock=clock)
    root = _run_tree(rec, clock)
    chat = next(s for s in rec.trace(root.trace_id) if s.name == "chat gemini")
    assert chat.attributes["gen_ai.prompt"] == PROMPT_TEXT


@pytest.mark.parametrize("capture", [False, True])
def test_a_planted_dummy_secret_never_appears(tmp_path, capture):
    clock = FakeClock()
    out = tmp_path / "spans.jsonl"
    rec = telemetry.Recorder(path=out, capture_content=capture, clock=clock)
    with pytest.raises(RuntimeError):
        with rec.span("invoke_agent friday", component="runtime",
                      attributes={"note": f"key is {DUMMY_SECRET}",
                                  "api_key": DUMMY_SECRET}) as sp:
            sp.set("gen_ai.prompt", f"use {DUMMY_SECRET} to log in")
            sp.set("authorization", DUMMY_SECRET)
            sp.set("headers", {"Authorization": DUMMY_SECRET, "n": [DUMMY_SECRET]})
            raise RuntimeError(f"upstream rejected {DUMMY_SECRET}")

    in_memory = json.dumps([s.to_record() for s in rec.spans()])
    on_disk = out.read_text(encoding="utf-8")
    assert DUMMY_SECRET not in in_memory
    assert DUMMY_SECRET not in on_disk
    row = json.loads(on_disk.splitlines()[0])
    assert row["status"] == "ERROR"
    assert row["attributes"]["error.type"] == "RuntimeError"


def test_a_failed_export_never_breaks_the_run(tmp_path):
    clock = FakeClock()
    # A directory cannot be opened for append, so the export fails.
    rec = telemetry.Recorder(path=tmp_path, clock=clock)
    with rec.span("x", component="runtime"):
        clock.tick(1)
    assert len(rec.spans()) == 1


def test_the_default_recorder_is_local_and_content_free(monkeypatch):
    monkeypatch.delenv("FRIDAY_TELEMETRY_FILE", raising=False)
    monkeypatch.delenv("FRIDAY_TELEMETRY_CONTENT", raising=False)
    telemetry.reset()
    rec = telemetry.recorder()
    assert rec.path is None
    assert rec.capture_content is False
    telemetry.reset()


def test_the_claude_code_executor_emits_a_live_span(tmp_path, monkeypatch):
    out = tmp_path / "live.jsonl"
    telemetry.configure(path=out)
    try:
        monkeypatch.setattr(cli, "available", lambda: False)
        store = Store(tmp_path / "exec.sqlite3")
        try:
            bundle = TaskBundle(goal=f"fix it with {DUMMY_SECRET}",
                                workspace=str(tmp_path), project="halo",
                                acceptance=("tests pass",))
            result = asyncio.run(ClaudeCodeExecutor(
                store, model="claude-sonnet-x").execute(bundle))
        finally:
            store.close()
        assert result.status == c.FAILED
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1
        attrs = rows[0]["attributes"]
        assert rows[0]["name"] == "invoke_agent claude_code"
        assert attrs["gen_ai.operation.name"] == "invoke_agent"
        assert attrs["gen_ai.agent.name"] == "claude_code"
        assert attrs["gen_ai.request.model"] == "claude-sonnet-x"
        assert attrs["friday.component"] == "executor.claude_code"
        assert attrs["friday.result.status"] == c.FAILED
        # The goal is prompt-like content: off by default.
        assert "fix it" not in out.read_text(encoding="utf-8")
    finally:
        telemetry.reset()
