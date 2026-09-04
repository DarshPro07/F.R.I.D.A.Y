"""
open_notebook_research, maxun_scraping and openmontage_media: request shape,
auth header, honest UNAVAILABLE, and that a write refuses without its
required fields.

Each test spins up one real (fake) HTTP server in a thread so the request
that leaves the adapter is proven, not assumed.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from friday import contracts as c
from friday import fabric
from friday.fabric_adapters import maxun_scraping as mx
from friday.fabric_adapters import open_notebook_research as on
from friday.fabric_adapters import openmontage_media as om

NEW = ("open_notebook_research", "maxun_scraping", "openmontage_media")


@pytest.fixture(autouse=True)
def clean():
    fabric.reload()
    yield
    fabric.reload()


class _Recorder(BaseHTTPRequestHandler):
    """Records every request it saw (in order) and replies with a fixed body."""

    seen: list | None = None
    reply: dict = {"id": "sess-1"}

    def _handle(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        type(self).seen.append({
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(body) if body else None,
        })
        payload = json.dumps(type(self).reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def log_message(self, *a):  # silence
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _Recorder.seen = []
    try:
        yield httpd
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


@pytest.mark.parametrize("provider_id", NEW)
def test_every_new_provider_is_pinned_and_registered(provider_id):
    p = fabric.get(provider_id)
    assert len(p.commit) == 40
    assert p.family in fabric.FAMILIES


def test_maxun_is_copyleft_and_isolated():
    p = mx.DESCRIPTOR
    assert p.license_mode == fabric.COPYLEFT
    assert p.integration_mode in fabric.ISOLATED_MODES
    assert p.imported is False


def test_openmontage_is_copyleft_and_isolated():
    p = om.DESCRIPTOR
    assert p.license_mode == fabric.COPYLEFT
    assert p.integration_mode in fabric.ISOLATED_MODES


def test_open_notebook_writes_are_gated_and_reads_are_open():
    p = on.DESCRIPTOR
    assert "research.write" in p.permissions
    assert set(p.operations) - set(p.open_operations) == {"add_source"}


def test_maxun_writes_are_gated_and_reads_are_open():
    p = mx.DESCRIPTOR
    assert "scraping.run" in p.permissions
    assert set(p.operations) - set(p.open_operations) == {"run_robot"}


def test_openmontage_has_no_permissions_or_secrets():
    p = om.DESCRIPTOR
    assert p.permissions == ()
    assert p.secrets == ()


# --- honest unreachable -----------------------------------------------------


def test_open_notebook_absent_store_is_unavailable_with_the_reason(monkeypatch):
    monkeypatch.setenv(on.ENV_URL, "http://127.0.0.1:1")
    probe = on.health()
    assert probe["state"] == fabric.UNAVAILABLE
    assert on.ENV_URL in probe["detail"]


def test_maxun_absent_store_is_unavailable_with_the_reason(monkeypatch):
    monkeypatch.setenv(mx.ENV_URL, "http://127.0.0.1:1")
    probe = mx.health()
    assert probe["state"] == fabric.UNAVAILABLE
    assert mx.ENV_URL in probe["detail"]


def test_openmontage_absent_store_is_unavailable_with_the_reason(monkeypatch):
    monkeypatch.setenv(om.ENV_URL, "http://127.0.0.1:1")
    probe = om.health()
    assert probe["state"] == fabric.UNAVAILABLE
    assert om.ENV_URL in probe["detail"]


# --- request shape and auth --------------------------------------------------


def test_open_notebook_notebooks_sends_bearer_password(server, monkeypatch):
    monkeypatch.setenv(on.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = on.call("notebooks", secrets={"open_notebook_password": "pw"})
    assert result.status == c.SUCCEEDED
    assert _Recorder.seen[0]["method"] == "GET"
    assert _Recorder.seen[0]["path"] == "/api/notebooks"
    assert _Recorder.seen[0]["headers"]["Authorization"] == "Bearer pw"


def test_open_notebook_notebook_needs_id(server, monkeypatch):
    monkeypatch.setenv(on.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = on.call("notebook")
    assert result.status == c.FAILED and "id" in result.error
    assert _Recorder.seen == []


def test_open_notebook_ask_creates_session_then_executes(server, monkeypatch):
    monkeypatch.setenv(on.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = on.call("ask", notebook="nb-1", question="what happened?")
    assert result.status == c.SUCCEEDED
    assert len(_Recorder.seen) == 2
    first, second = _Recorder.seen
    assert first["path"] == "/api/chat/sessions"
    assert first["body"]["notebook_id"] == "nb-1"
    assert second["path"] == "/api/chat/execute"
    assert second["body"]["session_id"] == "sess-1"
    assert second["body"]["message"] == "what happened?"


def test_open_notebook_ask_refuses_without_notebook_or_question(server, monkeypatch):
    monkeypatch.setenv(on.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    r1 = on.call("ask", question="hi")
    assert r1.status == c.FAILED and "notebook" in r1.error
    r2 = on.call("ask", notebook="nb-1")
    assert r2.status == c.FAILED and "question" in r2.error
    assert _Recorder.seen == []


def test_open_notebook_add_source_is_a_write_and_posts_url(server, monkeypatch):
    monkeypatch.setenv(on.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = on.call("add_source", notebook="nb-1", url="https://example.com")
    assert result.status == c.SUCCEEDED
    assert _Recorder.seen[0]["method"] == "POST"
    assert _Recorder.seen[0]["path"] == "/api/sources"
    assert _Recorder.seen[0]["body"]["notebooks"] == ["nb-1"]
    assert _Recorder.seen[0]["body"]["url"] == "https://example.com"


def test_maxun_robots_sends_raw_api_key_header(server, monkeypatch):
    monkeypatch.setenv(mx.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = mx.call("robots", secrets={"maxun_api_key": "mx_secret"})
    assert result.status == c.SUCCEEDED
    assert _Recorder.seen[0]["method"] == "GET"
    assert _Recorder.seen[0]["path"] == "/api/robots"
    assert _Recorder.seen[0]["headers"]["x-api-key"] == "mx_secret"


def test_maxun_requires_the_key_before_any_http(server, monkeypatch):
    monkeypatch.setenv(mx.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = mx.call("robots")
    assert result.status == c.FAILED and "maxun_api_key" in result.error
    assert _Recorder.seen == []


def test_maxun_runs_needs_a_bare_robot_id(server, monkeypatch):
    monkeypatch.setenv(mx.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = mx.call("runs", secrets={"maxun_api_key": "k"})
    assert result.status == c.FAILED and "robot" in result.error
    assert _Recorder.seen == []


def test_maxun_results_hits_robot_and_run_path(server, monkeypatch):
    monkeypatch.setenv(mx.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = mx.call("results", secrets={"maxun_api_key": "k"},
                      robot="rb-1", run="run-9")
    assert result.status == c.SUCCEEDED
    assert _Recorder.seen[0]["path"] == "/api/robots/rb-1/runs/run-9"


def test_maxun_run_robot_is_a_write_and_posts(server, monkeypatch):
    monkeypatch.setenv(mx.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = mx.call("run_robot", secrets={"maxun_api_key": "k"}, robot="rb-1")
    assert result.status == c.SUCCEEDED
    assert _Recorder.seen[0]["method"] == "POST"
    assert _Recorder.seen[0]["path"] == "/api/robots/rb-1/runs"


def test_openmontage_projects_needs_no_secret(server, monkeypatch):
    monkeypatch.setenv(om.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = om.call("projects")
    assert result.status == c.SUCCEEDED
    assert _Recorder.seen[0]["method"] == "GET"
    assert _Recorder.seen[0]["path"] == "/api/projects"


def test_openmontage_project_needs_a_bare_id(server, monkeypatch):
    monkeypatch.setenv(om.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = om.call("project")
    assert result.status == c.FAILED and "id" in result.error
    assert _Recorder.seen == []
