"""
UX-001 (PRD 69): "User may show/hide workers." The `workers` flag on
/api/observability is a view over an already-redacted trace; it grants nothing.
Kept apart from tests/test_observability.py, which another build owns.
"""
from __future__ import annotations

import contextlib
import json
import re
import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from friday import observability as OB
from friday.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "obs_workers.sqlite3"))


def _ui_client(store, monkeypatch):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from friday import access
    from friday import ui_server as u

    monkeypatch.setattr(access, "GATE_ENABLED", False)
    monkeypatch.setenv("ADA_DB", str(store.path))
    # GateMiddleware 403s any non-local Host (TestClient's default is "testserver").
    return TestClient(u.create_app(), base_url="http://127.0.0.1:8770")


def test_user_may_show_or_hide_workers_in_either_mode(store, monkeypatch):
    # PRD 69: "User may show/hide workers." Normal hides them by default and
    # Developer shows them; the `workers` flag flips either, and is only a view.
    started = {"at": "2026-10-10T00:00:01", "kind": "worker", "event": "worker.started",
               "executor": "hermes", "worker_run_id": "W1", "pid": 4242}
    step = {"at": "2026-10-10T00:00:00", "kind": "objective", "event": "task.started"}
    full = {"run_id": "RUN-w", "found": True, "status": "RUNNING", "tasks": [],
            "timeline": [step, started], "errors": []}
    monkeypatch.setattr(OB, "trace", lambda run_id: dict(full))

    def get(client, **params):
        r = client.get("/api/observability", params={"run_id": "RUN-w", **params})
        assert r.status_code == 200, r.text
        return r.json()

    with _ui_client(store, monkeypatch) as client:
        normal, normal_shown = get(client), get(client, workers="show")
        dev, dev_hidden = get(client, mode="developer"), get(client, mode="developer", workers="hide")
        odd = get(client, workers="all")
    assert "workers" not in normal and "workers" not in odd
    assert normal_shown["workers"] == [{"at": started["at"], "event": "worker.started",
                                        "executor": "hermes", "worker_run_id": "W1"}]
    assert "timeline" not in normal_shown  # showing workers does not open the developer view
    assert dev["workers"] == normal_shown["workers"] and started in dev["timeline"]
    assert "workers" not in dev_hidden and dev_hidden["timeline"] == [step]


def test_control_room_exposes_the_mode_and_workers_toggle():
    # The API flags had no control in the UI. The page must offer exactly the
    # values the server understands, send them to /api/observability, and route
    # the click through the whitelisted delegated-action path (no inline JS).
    html = (Path(__file__).resolve().parents[1] / "ui" / "index.html").read_text(encoding="utf-8")
    choices = json.loads(re.search(r"const OBS_CHOICES=(\{.*?\});", html).group(1)
                         .replace("mode:", '"mode":').replace("workers:", '"workers":'))
    assert choices["mode"] == [OB.NORMAL, OB.DEVELOPER]
    assert {"show", "hide"} <= set(choices["workers"])
    assert all(OB.parse_mode(m) == m for m in choices["mode"])
    assert '"obsView"' in re.search(r"const DELEGATED=new Set\(\[(.*?)\]\)", html).group(1)
    assert 'data-act="obsView"' in html and 'jget("/api/observability?"' in html
    assert 'q.set("workers"' in html and 'q.set("run_id"' in html


@contextlib.contextmanager
def _control_room(store, monkeypatch, *, init_script=None):
    """A real browser on the real UI server; every /api call except
    /api/observability is a stub, and each /api/observability query is recorded."""
    sync_api = pytest.importorskip("playwright.sync_api")
    import uvicorn
    from friday import access
    from friday import ui_server as u

    monkeypatch.setattr(access, "GATE_ENABLED", False)
    monkeypatch.setenv("ADA_DB", str(store.path))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(u.create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the UI server did not start"

    queries: list[dict] = []

    def observability(route):
        q = parse_qs(urlsplit(route.request.url).query)
        queries.append({k: v[0] for k, v in q.items()})
        route.fulfill(json={"mode": q.get("mode", ["normal"])[0], "scope": "diagnostics"})

    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                if init_script:
                    page.add_init_script(init_script)
                page.route("**/api/**", lambda r: r.fulfill(json={}))   # catch-all first, specifics win
                page.route("**/api/auth/status", lambda r: r.fulfill(
                    json={"locked": False, "gate": False, "enrolled": True, "auth_mode": "face"}))
                page.route("**/api/state", lambda r: r.fulfill(json={"system": {"ok": True}}))
                page.route("**/api/doctor", lambda r: r.fulfill(json={"checks": [], "healthy": 0, "total": 0}))
                page.route("**/api/observability**", observability)
                page.goto(f"http://127.0.0.1:{port}/")
                yield page, queries
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _open_control_room(page):
    page.locator('.tabs button[data-v="room"]').click()
    page.wait_for_selector('#cr [data-act="obsView"]')


def _picked(page, choice):
    return page.locator(f'[data-act="obsView"][data-arg="{choice}"]:not(.quiet)')


def test_detail_level_choice_survives_a_page_reload(store, monkeypatch):
    # UX-001: the Normal/Developer and workers choice was in-memory only, so
    # every reload put the user back in Normal. It is a view preference like
    # fridayView / fridaySTT, so it persists in localStorage and is re-sent.
    with _control_room(store, monkeypatch) as (page, queries):
        _open_control_room(page)
        assert _picked(page, "mode:normal").count() == 1 and _picked(page, "workers:default").count() == 1
        page.locator('[data-act="obsView"][data-arg="mode:developer"]').click()
        _picked(page, "mode:developer").wait_for()
        page.locator('[data-act="obsView"][data-arg="workers:hide"]').click()
        _picked(page, "workers:hide").wait_for()

        queries.clear()
        page.reload()
        _open_control_room(page)
        _picked(page, "mode:developer").wait_for()
        _picked(page, "workers:hide").wait_for()
        assert queries and queries[0].get("mode") == "developer" and queries[0].get("workers") == "hide"


def test_a_stored_detail_level_the_server_does_not_know_is_ignored(store, monkeypatch):
    # localStorage is user-editable: only the values the server understands may
    # become the view, and anything else is the Normal/default view.
    poison = ('localStorage.setItem("fridayObsMode","admin");'
              'localStorage.setItem("fridayObsWorkers","<img src=x>");')
    with _control_room(store, monkeypatch, init_script=poison) as (page, queries):
        _open_control_room(page)
        assert _picked(page, "mode:normal").count() == 1 and _picked(page, "workers:default").count() == 1
        assert queries and queries[0].get("mode") == "normal" and "workers" not in queries[0]
