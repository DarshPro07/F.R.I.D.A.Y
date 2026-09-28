"""
A simple deterministic task uses no model at all (PRD v7 COG-001:
Eliminate -> Automate -> Delegate; acceptance "simple deterministic tasks use
zero frontier calls").

`tests/test_execution_economics.py` proves the routing functions contain no
model-call primitive. What it cannot prove is the pipeline: a request going
through admission, planning, compilation, the executor and the capability
runtime, any of which could reach for a model on the way. So this drives the
real path - `objective_start` -> the hybrid planner -> compile -> the
continuous executor -> `capability_runtime.build_dispatch()` - for several
read-only requests, and counts every boundary a model call has to cross:

  network      any DNS lookup or non-loopback connection (blocked)
  planner      `planner_model.interpret` / `_model` (the DEEP planning route)
  gateway      `ModelGateway.infer` and the gateway worker's RPC
  delegation   `HermesSupervisor.delegate`
  text brain   `voice_brain._model`
  Gemini       any `google.genai.Client` constructed

A zero only means something if every counter can move, so each one has its
own negative control: the door is called the way in-process code resolves it
(module attribute, or class attribute through an instance) with the real
function stubbed out, and its counter must move. The planner control goes
further and forces the model-assist branch through the whole pipeline. A
static check pins that no product module binds a counted function by name,
which would let a call slip past the module-attribute counters.

Scope, stated rather than implied: this is the task pipeline. On the
conversational surface the interface model that hears the request is the
NORMAL-tier reply model (`voice_brain._ROLE_MODEL`), not a frontier route.
A model reached from another process is outside what an in-process counter
can see; the gateway worker's RPC is counted at the boundary for that reason.
"""
from __future__ import annotations

import ast
import asyncio
import ipaddress
import socket
from pathlib import Path

import pytest

from friday import contracts as c
from friday import objectives as O
from friday.store import Store

ROOT = Path(__file__).resolve().parent.parent

#: Read-only requests the deterministic planner answers, and the capability
#: each must run. Host-dependent ones (battery, desktop windows, a real
#: Downloads folder) are left out so the test means the same on any machine.
REQUESTS = [
    ("check my system", "system_get_info"),
    ("how much disk space do I have", "system_disks"),
    ("what time is it", "get_current_time"),
    ("list running processes", "system_list_processes"),
    ("show my network status", "system_network"),
]


class _Counter:
    def __init__(self) -> None:
        self.calls: dict[str, list] = {}
        #: The function behind each counted door. A control swaps in a stub
        #: so it can prove the counter moves without running a real model.
        self.real: dict[str, object] = {}

    def hit(self, boundary: str, detail=None) -> None:
        self.calls.setdefault(boundary, []).append(detail)

    def total(self) -> int:
        return sum(len(v) for v in self.calls.values())


def _loopback(host) -> bool:
    try:
        return ipaddress.ip_address(str(host).strip("[]")).is_loopback
    except ValueError:
        return str(host).lower() in ("localhost",)


@pytest.fixture
def counter(monkeypatch):
    """Every model boundary counted; the network is closed. Loopback stays
    open because asyncio's own self-pipe on Windows is a loopback socket
    pair - local model routes are counted at FRIDAY's boundaries instead."""
    from friday import hermes_bridge, model_gateway, planner_model, voice_brain

    seen = _Counter()
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if not _loopback(host):
            seen.hit("network", address)
            raise OSError("test: no network during a deterministic task")
        return real_connect(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not _loopback(host):
            seen.hit("network", ("dns", host))
            raise OSError("test: no DNS during a deterministic task")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    def counted(boundary, door, real):
        seen.real[door] = real

        def wrapper(*args, **kwargs):
            seen.hit(boundary)
            return seen.real[door](*args, **kwargs)
        return wrapper

    for owner, name, boundary, door in DOORS:
        target = {"planner_model": planner_model, "voice_brain": voice_brain,
                  "ModelGateway": model_gateway.ModelGateway,
                  "ModelGatewayWorker": model_gateway.ModelGatewayWorker,
                  "HermesSupervisor": hermes_bridge.HermesSupervisor}[owner]
        monkeypatch.setattr(target, name,
                            counted(boundary, door, getattr(target, name)))
    try:
        from google import genai
    except ImportError:                                     # pragma: no cover
        genai = None
    if genai is not None:
        monkeypatch.setattr(genai.Client, "__init__",
                            counted("gemini", "gemini", genai.Client.__init__))
    planner_model._RECENT.clear()
    yield seen
    planner_model._RECENT.clear()


#: (owner, attribute, counter, door): every model door the fixture counts.
DOORS = [
    ("planner_model", "interpret", "planner", "planner.interpret"),
    ("planner_model", "_model", "planner", "planner._model"),
    ("ModelGateway", "infer", "gateway", "gateway.infer"),
    ("ModelGatewayWorker", "call", "gateway", "gateway.worker_call"),
    ("HermesSupervisor", "delegate", "delegation", "delegation"),
    ("voice_brain", "_model", "text brain", "text brain"),
]


@pytest.fixture
def store(tmp_path):
    from friday.toolsets import objectives as OT

    db = Store(tmp_path / "cog001.sqlite3")
    OT.reset_store(db)
    yield db
    OT.reset_store(None)
    db.close()


def _start_and_finish(store, text: str) -> tuple[dict, dict, list[dict]]:
    """The production entry for a task, then the executor over the real
    dispatch until the run is terminal."""
    from friday import capability_runtime
    from friday.continuous import ContinuousTaskExecutor
    from friday.toolsets import objectives as OT

    started = OT.objective_start(c.Run.create(text, capability="objectives"), text)
    assert started.status == c.SUCCEEDED, started.error
    run_id = started.output["run_id"]

    async def drive():
        executor = ContinuousTaskExecutor(
            store, call_capability=capability_runtime.build_dispatch())
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 20.0
            while loop.time() < deadline:
                if store.objective_run(run_id)["status"] in O.RUN_TERMINAL:
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(drive())
    return started.output, store.objective_run(run_id), store.objective_tasks(run_id)


@pytest.mark.parametrize("request_text,capability", REQUESTS)
def test_a_deterministic_task_runs_end_to_end_without_any_model(
        store, counter, request_text, capability):
    created, final, tasks = _start_and_finish(store, request_text)

    # Not vacuous: the task really ran, through the real capability.
    assert created["tasks"] == [{"capability": capability, "dependencies": []}]
    assert final["status"] == O.RUN_COMPLETED, (final["status"], tasks)
    assert [t["status"] for t in tasks] == [O.TASK_SUCCEEDED]

    assert counter.total() == 0, f"model boundaries crossed: {counter.calls}"


def test_the_counters_see_a_model_call_when_one_is_made(store, counter, monkeypatch):
    """Negative control: bypass the deterministic classifier so the planner
    asks its model, through the same entry point. The model itself is a fake
    (no key, no network) - what is measured is that the boundary counted it."""
    from friday import planner_model as SPM

    class _FakeModels:
        def generate_content(self, **kwargs):
            counter.hit("fake model answered")
            return type("A", (), {"text": '{"goals": []}'})()

    monkeypatch.setattr(SPM, "needs_model", lambda text, plan: True)
    # `interpret` stays the counted wrapper from the fixture; only the client
    # behind it is fake. (Calling the real `_model` would load .env into
    # os.environ for the rest of the session.)
    monkeypatch.setattr(SPM, "_model",
                        lambda: (type("C", (), {"models": _FakeModels()})(), "fake-deep"))

    _start_and_finish(store, REQUESTS[0][0])
    assert counter.calls.get("planner"), (
        "the planner's model branch ran and the counter did not move: "
        "a zero from the test above would prove nothing")


def _call_door(owner: str, name: str) -> None:
    """Call one door the way in-process code resolves it: a module function
    through its module, a method through an instance of its class."""
    from friday import hermes_bridge, model_gateway, planner_model, voice_brain

    modules = {"planner_model": planner_model, "voice_brain": voice_brain}
    classes = {"ModelGateway": model_gateway.ModelGateway,
               "ModelGatewayWorker": model_gateway.ModelGatewayWorker,
               "HermesSupervisor": hermes_bridge.HermesSupervisor}
    if owner in modules:
        getattr(modules[owner], name)()
    else:
        getattr(object.__new__(classes[owner]), name)()


@pytest.mark.parametrize("owner,name,boundary,door", DOORS,
                         ids=[door for *_, door in DOORS])
def test_every_counted_door_moves_its_counter(counter, owner, name, boundary, door):
    """Negative control per door: with the real function stubbed out, a call
    through the attribute in-process code resolves must be counted."""
    counter.real[door] = lambda *args, **kwargs: None
    _call_door(owner, name)
    assert counter.calls.get(boundary), f"{door} was called and {boundary!r} did not move"


def test_constructing_a_gemini_client_is_counted(counter):
    genai = pytest.importorskip("google.genai")
    counter.real["gemini"] = lambda self, *args, **kwargs: None
    genai.Client()
    assert counter.calls.get("gemini")


def test_a_blocked_connection_is_counted(counter):
    """The network backstop can move too, and it refuses rather than connects."""
    sock = socket.socket()
    try:
        with pytest.raises(OSError, match="no network"):
            sock.connect(("203.0.113.9", 443))           # TEST-NET-3, never routed
    finally:
        sock.close()
    assert counter.calls.get("network")


#: Functions counted through their module attribute. A product module that
#: bound one by name at import would call the original past the counter.
COUNTED_BY_MODULE = {"friday.planner_model": {"interpret", "_model"},
                     "friday.voice_brain": {"_model"}}


def test_no_product_module_binds_a_counted_door_by_name():
    offenders = []
    for path in sorted((ROOT / "friday").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in COUNTED_BY_MODULE:
                bound = {a.name for a in node.names} & COUNTED_BY_MODULE[node.module]
                if bound:
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:"
                                     f"{node.lineno} from {node.module} import "
                                     f"{', '.join(sorted(bound))}")
    assert offenders == [], (
        "these calls would bypass the module-attribute counters; count them "
        "too:\n" + "\n".join(offenders))
