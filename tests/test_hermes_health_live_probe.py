"""The live Hermes probe must not mistake an HTTP error for a dead server.

Friday's MCP server is FastMCP over SSE and has no route at "/", so a perfectly
healthy one answers `GET /` with 404. The probe used to catch that as "the MCP
server is gone", which failed `mcp_server_alive`, which failed
`hermes_bridge_ready`, which stopped Friday delegating to a Hermes that was
running the whole time.

An HTTP status is proof of life. Only a refused connection or a timeout is not.
"""
from __future__ import annotations

import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from friday import hermes_health as hh

LAYERS_AFTER_MCP = ("mcp_server_alive", "mcp_sse_connected")


def _serve(status: int):
    """A one-request server that answers with `status`, like FastMCP does."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):                       # noqa: N802
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b"")

        def log_message(self, *a):              # keep pytest output clean
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_port


@pytest.mark.parametrize("status", [200, 404, 405, 501])
def test_any_http_answer_means_the_mcp_server_is_alive(status):
    """404 is what a healthy FastMCP SSE server returns at '/'."""
    srv, url = _serve(status)
    try:
        report = hh.live_probe_factory(url, timeout=3.0)()
    finally:
        srv.shutdown()
    for layer in LAYERS_AFTER_MCP:
        assert report.signals[layer] is True, (
            "%s should be alive: the server answered with HTTP %d" % (layer, status))


def test_nothing_listening_still_reads_as_dead():
    """The probe must keep working for the case it was written for."""
    srv, url = _serve(200)
    srv.shutdown()                              # free the port, then probe it
    srv.server_close()
    report = hh.live_probe_factory(url, timeout=1.0)()
    assert report.signals["mcp_server_alive"] is False
    assert report.signals["mcp_sse_connected"] is False
    assert report.signals["hermes_bridge_ready"] is False
    assert report.recovery is hh.Recovery.RESTART_MCP


def test_a_reachable_server_still_defers_the_bridge_to_a_real_round_trip():
    """Reaching MCP is not proof the bridge is up, and the probe must not claim it is."""
    srv, url = _serve(404)
    try:
        report = hh.live_probe_factory(url, timeout=3.0)()
    finally:
        srv.shutdown()
    assert report.signals["hermes_bridge_ready"] is False
    assert report.recovery is hh.Recovery.RECONNECT_BRIDGE


def test_http_error_is_not_swallowed_by_the_generic_handler():
    """Guards the exact regression: HTTPError must be caught before Exception."""
    srv, url = _serve(404)
    try:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(url, timeout=3)      # the exact call the probe makes
        report = hh.live_probe_factory(url, timeout=3.0)()
    finally:
        srv.shutdown()
    assert report.signals["mcp_server_alive"] is True
