"""
A listening port is not a running server.

The difference cost a live gate. A previous run's MCP server had been killed
but its socket lingered in CLOSE_WAIT, so a TCP connect still succeeded; the
gate concluded the server was up, every agent session started with "no MCP
tools found", and the run reported thirteen perfectly reachable capabilities
as unreachable. Nothing looked wrong - it looked like a routing regression.
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from friday import health


@pytest.fixture
def deaf_socket():
    """Accepts connections and then says nothing at all."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    stop = threading.Event()

    def accept_and_ignore():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                listener.accept()          # deliberately never answered
            except OSError:
                continue

    thread = threading.Thread(target=accept_and_ignore, daemon=True)
    thread.start()
    yield listener.getsockname()[1]
    stop.set()
    thread.join(timeout=2)
    listener.close()


@pytest.fixture
def http_server():
    class Quiet(BaseHTTPRequestHandler):
        def do_GET(self):               # noqa: N802
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):   # keep the test output clean
            pass

    server = HTTPServer(("127.0.0.1", 0), Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_a_socket_that_accepts_but_never_answers_is_not_serving(deaf_socket):
    assert not health.serving(f"http://127.0.0.1:{deaf_socket}", timeout=0.5)


def test_a_server_that_answers_at_all_is_serving(http_server):
    """A 404 proves the application is up as well as a 200 would."""
    assert health.serving(f"http://127.0.0.1:{http_server}", timeout=2.0)


def test_nothing_listening_is_not_serving():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    assert not health.serving(f"http://127.0.0.1:{free_port}", timeout=0.5)


def test_waiting_gives_up_rather_than_hanging_forever():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    assert not health.wait_until_serving(
        f"http://127.0.0.1:{free_port}", deadline_seconds=1.0)
