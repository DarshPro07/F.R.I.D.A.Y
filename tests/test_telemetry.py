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
import time

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


# -- the model gateway emits a chat span under the caller's span ------------


def _fake_gateway(tmp_path):
    import sys
    from pathlib import Path
    from friday import model_gateway as mg
    fake = str(Path(__file__).parent / "fake_model_gateway_worker.py")
    worker = mg.ModelGatewayWorker(command=[sys.executable, fake], profile="")
    return mg, mg.ModelGateway(
        worker=worker, telemetry=mg.GatewayTelemetry(tmp_path / "gateway.sqlite3"),
        tier_table={t: ("anthropic", "fake-model") for t in mg.TIERS})


def test_the_gateway_nests_a_chat_span_under_the_executor_span(tmp_path, monkeypatch):
    from friday import provider_cooldowns as PC
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", tmp_path / "cooldowns.json")
    rec = telemetry.configure(path=tmp_path / "gw.jsonl")
    mg, gw = _fake_gateway(tmp_path)
    try:
        req = mg.ModelGatewayRequest(
            objective_id="obj-otel", task_class=mg.SIMPLE,
            context_package=mg.compile_context(
                system="You are Friday.", user=f"say hi {DUMMY_SECRET}"))
        with telemetry.span("invoke_agent claude_code",
                            component="executor.claude_code") as root:
            result = gw.infer(req)
        assert result.status == "ok"

        spans = rec.trace(root.trace_id)
        chat = [s for s in spans if s.component == "model_gateway"]
        assert len(chat) == 1 and chat[0].parent_id == root.span_id
        attrs = chat[0].attributes
        assert attrs["gen_ai.operation.name"] == "chat"
        assert attrs["gen_ai.request.model"] == result.model != ""
        assert attrs["gen_ai.usage.input_tokens"] == result.input_tokens
        assert attrs["gen_ai.usage.output_tokens"] == result.output_tokens
        assert chat[0].name == f"chat {result.model}"

        times = rec.component_time_ms(root.trace_id)
        assert set(times) == {"executor.claude_code", "model_gateway"}
        assert times["model_gateway"] > 0
        assert sum(times.values()) == pytest.approx(rec.duration_ms(root.trace_id))

        # Content stays off by default and the planted secret is never exported.
        dumped = (tmp_path / "gw.jsonl").read_text(encoding="utf-8")
        assert "say hi" not in dumped and DUMMY_SECRET not in dumped
    finally:
        gw.close()
        telemetry.reset()


def test_a_refused_gateway_call_is_an_error_span(tmp_path):
    rec = telemetry.configure()
    mg, gw = _fake_gateway(tmp_path)
    try:
        req = mg.ModelGatewayRequest(
            objective_id="obj-big", task_class=mg.SIMPLE, max_input_tokens=1,
            context_package=mg.compile_context(system="s", user="x " * 200))
        with pytest.raises(mg.BudgetExceeded):
            gw.infer(req)
        (chat,) = [s for s in rec.spans() if s.component == "model_gateway"]
        assert chat.status == telemetry.ERROR
        assert chat.attributes["error.type"] == "BudgetExceeded"
    finally:
        gw.close()
        telemetry.reset()


# -- the Hermes bridge and the claude_code executor nest their parts ---------


def test_a_hermes_run_is_a_span_tree_with_time_per_component(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from friday import hermes_bridge as hb
    for key in ("FAKE_HERMES_CLARIFY", "FAKE_HERMES_HANG", "FAKE_HERMES_DIE"):
        monkeypatch.delenv(key, raising=False)
    out = tmp_path / "hermes.jsonl"
    rec = telemetry.configure(path=out)
    fake = str(Path(__file__).parent / "fake_hermes_gateway.py")
    supervisor = hb.HermesSupervisor(log=hb.WorkRunLog(tmp_path / "bridge.sqlite3"),
                                     command=[sys.executable, fake], profile="")
    supervisor.READY_TIMEOUT = 20
    try:
        with telemetry.span("invoke_agent friday", component="runtime") as root:
            result = supervisor.delegate(
                hb.TaskBundle(goal=f"{PROMPT_TEXT} {DUMMY_SECRET}"),
                wait=True, turn_timeout=30)
        assert result["result"]["status"] == hb.COMPLETE
    finally:
        supervisor.stop()
        telemetry.reset()

    spans = rec.trace(root.trace_id)
    by_name = {s.name: s for s in spans}
    run = by_name["invoke_agent hermes"]
    assert run.parent_id == root.span_id and run.component == "hermes_bridge"
    assert run.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert run.attributes["gen_ai.agent.name"] == "hermes"
    assert by_name["governor.admit"].parent_id == run.span_id
    assert by_name["hermes.turn"].parent_id == run.span_id

    times = rec.component_time_ms(root.trace_id)
    assert {"runtime", "hermes_bridge", "governor", "hermes"} <= set(times)
    assert times["hermes"] > 0
    assert sum(times.values()) == pytest.approx(rec.duration_ms(root.trace_id))
    # Content is off by default and the planted secret never reaches the export.
    dumped = out.read_text(encoding="utf-8")
    assert PROMPT_TEXT not in dumped and DUMMY_SECRET not in dumped


def test_the_executor_span_has_governor_and_tool_children(tmp_path, monkeypatch):
    from friday import governor as G
    out = tmp_path / "exec.jsonl"
    rec = telemetry.configure(path=out)

    # What each tool really took, on the spans' own clock: asyncio.sleep may wake up to one
    # clock tick (15.6 ms on Windows) early, so the request is no floor for the duration.
    took_ms: list[float] = []

    async def tool(seconds):
        start = time.perf_counter_ns()
        await asyncio.sleep(seconds)
        took_ms.append((time.perf_counter_ns() - start) / 1e6)

    class FakeRun:
        process = None

        def __init__(self, *a, **k):
            pass

        async def events(self, timeout=None):
            yield cli.Event("init", {"session_id": "s1"})
            yield cli.Event("tool", {}, name="Edit")
            await tool(0.03)                     # the tool, or a permission wait
            yield cli.Event("tool_result", {})
            yield cli.Event("tool", {}, name="Bash")
            await tool(0.02)
            yield cli.Event("tool_result", {})
            yield cli.Event("result", {"result": "done"})

    monkeypatch.setattr(cli, "available", lambda: True)
    monkeypatch.setattr(cli, "Run", FakeRun)
    monkeypatch.setattr(ClaudeCodeExecutor, "finish",
                        lambda self, b, started, final: started.finish(status=c.PARTIAL))
    store = Store(tmp_path / "exec.sqlite3")
    try:
        bundle = TaskBundle(goal=f"{PROMPT_TEXT} {DUMMY_SECRET}",
                            workspace=str(tmp_path), project="halo",
                            acceptance=("tests pass",))
        result = asyncio.run(ClaudeCodeExecutor(store).execute(bundle))
    finally:
        store.close()
        telemetry.reset()
    assert result.status == c.PARTIAL, result.error

    spans = rec.spans()
    (root,) = [s for s in spans if s.name == "invoke_agent claude_code"]
    kids = [s for s in spans if s.parent_id == root.span_id]
    assert [s.name for s in kids if s.component == "governor"] == ["governor.admit"]
    tools = [s for s in kids if s.component == "claude_code.tool"]
    assert [s.name for s in tools] == ["execute_tool", "execute_tool"]
    assert [s.attributes["gen_ai.tool.name"] for s in tools] == ["Edit", "Bash"]
    assert all(s.attributes["gen_ai.operation.name"] == "execute_tool" for s in tools)
    # Each span opens before its tool starts and closes after it ends, so it covers the whole tool.
    assert len(took_ms) == 2 and all(ms > 0 for ms in took_ms)
    assert tools[0].duration_ms >= took_ms[0] and tools[1].duration_ms >= took_ms[1]

    times = rec.component_time_ms(root.trace_id)
    assert {"executor.claude_code", "governor", "claude_code.tool"} <= set(times)
    assert sum(times.values()) == pytest.approx(rec.duration_ms(root.trace_id))
    dumped = out.read_text(encoding="utf-8")
    assert PROMPT_TEXT not in dumped and DUMMY_SECRET not in dumped


def test_a_tool_left_open_when_the_run_dies_is_closed_as_an_error(tmp_path, monkeypatch):
    rec = telemetry.configure()

    class FakeRun:
        process = None

        def __init__(self, *a, **k):
            pass

        async def events(self, timeout=None):
            yield cli.Event("tool", {}, name="Bash")
            raise TimeoutError("claude exceeded 1s")

    monkeypatch.setattr(cli, "available", lambda: True)
    monkeypatch.setattr(cli, "Run", FakeRun)
    store = Store(tmp_path / "exec.sqlite3")
    try:
        bundle = TaskBundle(goal="long job", workspace=str(tmp_path),
                            project="halo", acceptance=("tests pass",))
        result = asyncio.run(ClaudeCodeExecutor(store).execute(bundle))
    finally:
        store.close()
        telemetry.reset()
    assert result.status == c.FAILED
    (tool,) = [s for s in rec.spans() if s.component == "claude_code.tool"]
    assert tool.status == telemetry.ERROR and tool.end_ns is not None


def test_overlapping_tool_calls_do_not_double_count_time():
    """Two tools open together (0-50 and 0-60 ms) inside a 100 ms run: every
    instant is counted once, so the parts still add up to the run."""
    now = [0]
    rec = telemetry.Recorder(clock=lambda: now[0])
    ms = 1_000_000
    with rec.span("invoke_agent x", component="executor.x") as root:
        a = rec.start("execute_tool", component="tool")
        b = rec.start("execute_tool", component="tool")
        now[0] = 50 * ms
        a.end()
        now[0] = 60 * ms
        b.end()
        now[0] = 100 * ms
    times = rec.component_time_ms(root.trace_id)
    assert sum(times.values()) == pytest.approx(rec.duration_ms(root.trace_id))
    assert times["tool"] == pytest.approx(60.0)
    assert times["executor.x"] == pytest.approx(40.0)


def test_parallel_tool_calls_in_a_real_executor_run_sum_to_the_run(tmp_path, monkeypatch):
    rec = telemetry.configure()

    class FakeRun:
        process = None

        def __init__(self, *a, **k):
            pass

        async def events(self, timeout=None):
            yield cli.Event("tool", {}, name="Read")
            yield cli.Event("tool", {}, name="Grep")      # both open at once
            await asyncio.sleep(0.03)
            yield cli.Event("tool_result", {})
            await asyncio.sleep(0.02)
            yield cli.Event("tool_result", {})
            yield cli.Event("result", {"result": "done"})

    monkeypatch.setattr(cli, "available", lambda: True)
    monkeypatch.setattr(cli, "Run", FakeRun)
    monkeypatch.setattr(ClaudeCodeExecutor, "finish",
                        lambda self, b, started, final: started.finish(status=c.PARTIAL))
    store = Store(tmp_path / "exec.sqlite3")
    try:
        bundle = TaskBundle(goal="read things", workspace=str(tmp_path),
                            project="halo", acceptance=("tests pass",))
        asyncio.run(ClaudeCodeExecutor(store).execute(bundle))
    finally:
        store.close()
        telemetry.reset()
    (root,) = [s for s in rec.spans() if s.name == "invoke_agent claude_code"]
    tools = [s for s in rec.spans() if s.component == "claude_code.tool"]
    assert len(tools) == 2
    assert sum(t.duration_ms for t in tools) > tools[0].duration_ms   # they overlap
    times = rec.component_time_ms(root.trace_id)
    assert sum(times.values()) == pytest.approx(rec.duration_ms(root.trace_id))


# -- a background Hermes run keeps a span until its terminal event -----------


def _bridge_supervisor(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from friday import hermes_bridge as hb
    for key in ("FAKE_HERMES_CLARIFY", "FAKE_HERMES_HANG", "FAKE_HERMES_DIE"):
        monkeypatch.delenv(key, raising=False)
    fake = str(Path(__file__).parent / "fake_hermes_gateway.py")
    supervisor = hb.HermesSupervisor(log=hb.WorkRunLog(tmp_path / "bridge.sqlite3"),
                                     command=[sys.executable, fake], profile="")
    supervisor.READY_TIMEOUT = 20
    return hb, supervisor


def test_a_background_hermes_run_has_a_span_that_closes_at_its_terminal_event(
        tmp_path, monkeypatch):
    hb, supervisor = _bridge_supervisor(tmp_path, monkeypatch)
    out = tmp_path / "bg.jsonl"
    rec = telemetry.configure(path=out)
    try:
        with telemetry.span("invoke_agent friday", component="runtime") as root:
            started = supervisor.delegate(
                hb.TaskBundle(goal=f"{PROMPT_TEXT} {DUMMY_SECRET}"), wait=False)
        work_run_id = started["work_run_id"]
        # The delegating call has returned; the run is still being traced.
        supervisor.wait_for(work_run_id, timeout=30)
        assert (supervisor.log.get(work_run_id) or {})["status"] == hb.COMPLETE
    finally:
        supervisor.stop()
        telemetry.reset()

    by_name = {s.name: s for s in rec.trace(root.trace_id)}
    run = by_name["invoke_agent hermes"]
    bg = by_name["hermes.background_run"]
    assert bg.parent_id == run.span_id and bg.component == "hermes"
    assert bg.attributes["friday.work_run_id"] == work_run_id
    assert bg.attributes["friday.work_run.status"] == hb.COMPLETE
    assert bg.status == telemetry.OK and bg.end_ns is not None
    assert bg.end_ns > run.end_ns            # it outlives the delegating call
    assert rec.component_time_ms(root.trace_id)["hermes"] > 0
    dumped = out.read_text(encoding="utf-8")
    assert PROMPT_TEXT not in dumped and DUMMY_SECRET not in dumped


def test_a_background_run_attributes_the_wall_time_between_delegation_and_its_end(
        tmp_path, monkeypatch):
    hb, supervisor = _bridge_supervisor(tmp_path, monkeypatch)
    clock = FakeClock()
    rec = telemetry.Recorder(clock=clock)
    monkeypatch.setattr(telemetry, "_recorder", rec)
    run_id = supervisor.log.create(task="t", friday_run_id="", bundle_chars=1,
                                   token_budget=0)
    with rec.span("invoke_agent friday", component="runtime") as root:
        clock.tick(5)
        with rec.span("invoke_agent hermes", component="hermes_bridge"):
            clock.tick(10)
            supervisor._trace_background_run(run_id, model="m")
    clock.tick(300)                          # the run works on its own
    open_before = [s for s in rec.trace(root.trace_id) if s.name == "hermes.background_run"]
    assert open_before == []                 # still open, so not yet exported
    supervisor.log.update(run_id, status=hb.FAILED)

    (bg,) = [s for s in rec.trace(root.trace_id) if s.name == "hermes.background_run"]
    assert bg.status == telemetry.ERROR
    assert bg.attributes["friday.work_run.status"] == hb.FAILED
    assert bg.duration_ms == 300.0
    times = rec.component_time_ms(root.trace_id)
    assert times == {"runtime": 5.0, "hermes_bridge": 10.0, "hermes": 300.0}
    # Ending twice (a late lease release) changes nothing.
    supervisor._end_background_run(run_id)
    assert len([s for s in rec.spans() if s.name == "hermes.background_run"]) == 1


def test_a_background_run_whose_gateway_stops_is_closed_as_an_error(tmp_path, monkeypatch):
    hb, supervisor = _bridge_supervisor(tmp_path, monkeypatch)
    rec = telemetry.Recorder(clock=FakeClock())
    monkeypatch.setattr(telemetry, "_recorder", rec)
    run_id = supervisor.log.create(task="t", friday_run_id="", bundle_chars=1,
                                   token_budget=0)
    supervisor.log.update(run_id, status=hb.WORKING)
    supervisor._trace_background_run(run_id, model="")
    supervisor._leases[run_id] = ""          # the stop path walks the held runs
    supervisor.stop()
    (bg,) = [s for s in rec.spans() if s.name == "hermes.background_run"]
    assert bg.status == telemetry.ERROR and bg.end_ns is not None


def test_a_run_already_terminal_when_traced_closes_at_once(tmp_path, monkeypatch):
    hb, supervisor = _bridge_supervisor(tmp_path, monkeypatch)
    rec = telemetry.Recorder(clock=FakeClock())
    monkeypatch.setattr(telemetry, "_recorder", rec)
    run_id = supervisor.log.create(task="t", friday_run_id="", bundle_chars=1,
                                   token_budget=0)
    supervisor.log.update(run_id, status=hb.COMPLETE)
    supervisor._trace_background_run(run_id, model="")
    (bg,) = [s for s in rec.spans() if s.name == "hermes.background_run"]
    assert bg.status == telemetry.OK
    assert not supervisor._background


def test_a_background_run_on_the_scoped_gateway_is_closed_when_the_owner_stops(
        tmp_path, monkeypatch):
    # The owner's delegate registers the span on the owner, but the run is held
    # on the scoped gateway; owner.stop() reaches only the scoped gateway's own
    # lease release, which must still end the owner's span.
    hb, owner = _bridge_supervisor(tmp_path, monkeypatch)
    rec = telemetry.Recorder(clock=FakeClock())
    monkeypatch.setattr(telemetry, "_recorder", rec)
    scoped = hb.HermesSupervisor(log=owner.log, command=owner._command, profile="",
                                 owner=owner)
    run_id = owner.log.create(task="t", friday_run_id="", bundle_chars=1,
                              token_budget=0)
    owner.log.update(run_id, status=hb.WORKING)
    owner._trace_background_run(run_id, model="")
    scoped._leases[run_id] = ""
    owner._scoped_runs[run_id] = scoped
    owner._scoped_gateway = scoped
    owner.stop()
    (bg,) = [s for s in rec.spans() if s.name == "hermes.background_run"]
    assert bg.status == telemetry.ERROR and bg.end_ns is not None
    assert bg.attributes["error.type"] == "RunStopped"
    assert not owner._background


def test_a_waited_delegation_that_times_out_keeps_a_background_span(
        tmp_path, monkeypatch):
    hb, supervisor = _bridge_supervisor(tmp_path, monkeypatch)
    rec = telemetry.Recorder(clock=FakeClock())
    monkeypatch.setattr(telemetry, "_recorder", rec)
    run_id = supervisor.log.create(task="t", friday_run_id="", bundle_chars=1,
                                   token_budget=0)
    supervisor.log.update(run_id, status=hb.WORKING)
    monkeypatch.setattr(supervisor, "_delegate", lambda bundle, **kw: {
        "work_run_id": run_id, "result": {"status": hb.WORKING}})
    supervisor.delegate(hb.TaskBundle(goal="g"), wait=True)
    assert run_id in supervisor._background        # still running: still traced
    supervisor.log.update(run_id, status=hb.COMPLETE)
    assert not supervisor._background
    (bg,) = [s for s in rec.spans() if s.name == "hermes.background_run"]
    assert bg.status == telemetry.OK


def test_a_waited_delegation_that_finished_gets_no_background_span(
        tmp_path, monkeypatch):
    hb, supervisor = _bridge_supervisor(tmp_path, monkeypatch)
    rec = telemetry.Recorder(clock=FakeClock())
    monkeypatch.setattr(telemetry, "_recorder", rec)
    run_id = supervisor.log.create(task="t", friday_run_id="", bundle_chars=1,
                                   token_budget=0)
    supervisor.log.update(run_id, status=hb.COMPLETE)
    monkeypatch.setattr(supervisor, "_delegate", lambda bundle, **kw: {
        "work_run_id": run_id, "result": {"status": hb.COMPLETE}})
    supervisor.delegate(hb.TaskBundle(goal="g"), wait=True)
    assert not [s for s in rec.spans() if s.name == "hermes.background_run"]


def test_the_run_wall_time_equals_the_parts_when_a_background_span_outlives_the_root():
    clock = FakeClock()
    rec = telemetry.Recorder(clock=clock)
    with rec.span("invoke_agent friday", component="runtime") as root:
        clock.tick(5)
        bg = rec.start("hermes.background_run", component="hermes")
    clock.tick(300)
    bg.end()
    times = rec.component_time_ms(root.trace_id)
    assert rec.duration_ms(root.trace_id) == 305.0
    assert sum(times.values()) == rec.duration_ms(root.trace_id)
