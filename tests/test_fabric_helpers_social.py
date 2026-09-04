"""
postiz_social and anythingllm_research: request shape, auth header, honest
UNAVAILABLE, and that a write refuses without its required fields.

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
from friday.fabric_adapters import anythingllm_research as al
from friday.fabric_adapters import postiz_social as pz

NEW = ("postiz_social", "anythingllm_research")


@pytest.fixture(autouse=True)
def clean():
    fabric.reload()
    yield
    fabric.reload()


class _Recorder(BaseHTTPRequestHandler):
    """Records the last request it saw and replies with a fixed JSON body."""

    seen: dict | None = None
    reply: dict = {"ok": True}

    def _handle(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        type(self).seen = {
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(body) if body else None,
        }
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
    _Recorder.seen = None
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


def test_postiz_is_copyleft_and_isolated():
    p = pz.DESCRIPTOR
    assert p.license_mode == fabric.COPYLEFT
    assert p.integration_mode in fabric.ISOLATED_MODES
    assert p.imported is False


def test_anythingllm_open_reads_and_no_permission_needed():
    p = al.DESCRIPTOR
    assert set(p.open_operations) == set(p.operations)
    assert p.permissions == ()


def test_postiz_writes_are_gated_and_reads_are_open():
    p = pz.DESCRIPTOR
    assert "social.publish" in p.permissions
    assert set(p.operations) - set(p.open_operations) == {"schedule"}
    assert "integrations" in p.open_operations and "queue" in p.open_operations


# --- honest unreachable -----------------------------------------------------


def test_postiz_absent_store_is_unavailable_with_the_reason(monkeypatch):
    monkeypatch.setenv(pz.ENV_URL, "http://127.0.0.1:1")
    probe = pz.health()
    assert probe["state"] == fabric.UNAVAILABLE
    assert pz.ENV_URL in probe["detail"]


def test_anythingllm_absent_store_is_unavailable_with_the_reason(monkeypatch):
    monkeypatch.setenv(al.ENV_URL, "http://127.0.0.1:1")
    probe = al.health()
    assert probe["state"] == fabric.UNAVAILABLE
    assert al.ENV_URL in probe["detail"]


# --- request shape and auth --------------------------------------------------


def test_postiz_integrations_sends_raw_key_no_bearer(server, monkeypatch):
    monkeypatch.setenv(pz.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = pz.call("integrations", secrets={"postiz_api_key": "pz_secret"})
    assert result.status == c.SUCCEEDED
    assert _Recorder.seen["method"] == "GET"
    assert _Recorder.seen["path"] == "/public/v1/integrations"
    assert _Recorder.seen["headers"]["Authorization"] == "pz_secret"


def test_postiz_schedule_posts_text_when_and_integrations(server, monkeypatch):
    monkeypatch.setenv(pz.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = pz.call("schedule", secrets={"postiz_api_key": "pz_secret"},
                      text="hello world", when="2026-09-10T12:00:00Z",
                      integrations=["abc123"])
    assert result.status == c.SUCCEEDED
    assert _Recorder.seen["method"] == "POST"
    assert _Recorder.seen["path"] == "/public/v1/posts"
    body = _Recorder.seen["body"]
    assert body["date"] == "2026-09-10T12:00:00Z"
    assert body["posts"][0]["integration"]["id"] == "abc123"
    assert body["posts"][0]["value"][0]["content"] == "hello world"


def test_postiz_schedule_refuses_without_text_or_when(server, monkeypatch):
    monkeypatch.setenv(pz.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    r1 = pz.call("schedule", secrets={"postiz_api_key": "pz_secret"},
                 when="2026-09-10T12:00:00Z", integrations=["abc123"])
    assert r1.status == c.FAILED and "text" in r1.error
    r2 = pz.call("schedule", secrets={"postiz_api_key": "pz_secret"},
                 text="hello", integrations=["abc123"])
    assert r2.status == c.FAILED and "when" in r2.error
    assert _Recorder.seen is None  # neither reached the server


def test_anythingllm_workspaces_sends_bearer_key(server, monkeypatch):
    monkeypatch.setenv(al.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = al.call("workspaces", secrets={"anythingllm_api_key": "al_secret"})
    assert result.status == c.SUCCEEDED
    assert _Recorder.seen["method"] == "GET"
    assert _Recorder.seen["path"] == "/api/v1/workspaces"
    assert _Recorder.seen["headers"]["Authorization"] == "Bearer al_secret"


def test_anythingllm_ask_posts_message_to_workspace_chat(server, monkeypatch):
    monkeypatch.setenv(al.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    result = al.call("ask", secrets={"anythingllm_api_key": "al_secret"},
                      workspace="research-desk", question="what is Friday?")
    assert result.status == c.SUCCEEDED
    assert _Recorder.seen["method"] == "POST"
    assert _Recorder.seen["path"] == "/api/v1/workspace/research-desk/chat"
    assert _Recorder.seen["body"]["message"] == "what is Friday?"


def test_anythingllm_ask_refuses_without_workspace_or_question(server, monkeypatch):
    monkeypatch.setenv(al.ENV_URL, f"http://127.0.0.1:{server.server_port}")
    r1 = al.call("ask", secrets={"anythingllm_api_key": "al_secret"},
                 question="hi")
    assert r1.status == c.FAILED and "workspace" in r1.error
    r2 = al.call("ask", secrets={"anythingllm_api_key": "al_secret"},
                 workspace="x")
    assert r2.status == c.FAILED and "question" in r2.error
    assert _Recorder.seen is None
