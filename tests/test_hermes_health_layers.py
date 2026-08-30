"""RC1.1 — Hermes connectivity must be diagnosed by LAYER, not by one bool.

The live regression: `hermes gateway status` reported a healthy Windows
Scheduled Task with live PIDs, and Friday still told the boss "Hermes gateway
is currently disconnected" and asked whether to continue. One stale layer was
reported as the whole gateway being dead, and a recoverable internal fault
was handed back to the user as a question.

These gates assert the product behavior: name the failed layer, and let the
objective decide recovery from that instead of from a single boolean.
"""
from __future__ import annotations
import pytest
from friday import hermes_health as H


def test_layers_are_ordered_from_process_outwards():
    """Diagnosis reads outwards, so the innermost failure is the cause."""
    assert H.LAYERS == ('gateway_process_alive', 'gateway_http_reachable', 'friday_profile_registered', 'friday_to_gateway_connected', 'mcp_server_alive', 'mcp_sse_connected', 'hermes_bridge_ready', 'active_workrun_reachable')


def test_all_layers_up_is_healthy_and_needs_no_recovery():
    report = H.Report({name: True for name in H.LAYERS})
    assert report.healthy is True
    assert report.failed_layer == ''
    assert report.recovery is H.Recovery.NONE
    assert report.blocks_read_only_telemetry is False


def test_a_stale_bridge_is_not_a_dead_gateway():
    """The exact live shape: process + HTTP fine, bridge stale.

    Friday must not call the whole gateway dead, and the repair is a bridge
    reconnect - never a process restart, which would orphan live workers.
    """
    signals = {name: True for name in H.LAYERS}
    signals['hermes_bridge_ready'] = False
    signals['active_workrun_reachable'] = False
    report = H.Report(signals)
    assert report.healthy is False
    assert report.failed_layer == 'hermes_bridge_ready'
    assert report.recovery is H.Recovery.RECONNECT_BRIDGE
    assert report.gateway_is_dead is False
    assert 'gateway process is up' in report.summary


def test_only_a_dead_process_may_be_restarted():
    signals = {name: False for name in H.LAYERS}
    report = H.Report(signals)
    assert report.failed_layer == 'gateway_process_alive'
    assert report.recovery is H.Recovery.RESTART_GATEWAY
    assert report.gateway_is_dead is True


def test_restart_is_refused_while_work_is_in_flight():
    """Hermes does not resume delegated children across a process restart.

    Restarting under live work turns provable state into `unknown`, so the
    decision must be reconcile-first even when the process looks dead.
    """
    signals = {name: False for name in H.LAYERS}
    report = H.Report(signals, active_workruns=('hermes-abc123',))
    assert report.recovery is H.Recovery.RECONCILE_THEN_RESTART
    assert 'hermes-abc123' in report.summary


def test_no_layer_failure_ever_blocks_read_only_telemetry():
    """The observed wrong question was "should I pull telemetry?".

    Read-only telemetry is safe and already inside the accepted objective, so
    no connectivity fault may turn it into a user decision.
    """
    for failing in H.LAYERS:
        signals = {name: True for name in H.LAYERS}
        signals[failing] = False
        assert H.Report(signals).blocks_read_only_telemetry is False


def test_summary_names_the_layer_and_never_asks_the_user():
    signals = {name: True for name in H.LAYERS}
    signals['mcp_sse_connected'] = False
    summary = H.Report(signals).summary
    assert 'mcp_sse_connected' in summary
    assert '?' not in summary, 'diagnosis states, it does not ask'


@pytest.mark.parametrize('failing,expected', [('gateway_http_reachable', H.Recovery.RESTART_GATEWAY), ('friday_profile_registered', H.Recovery.REREGISTER_PROFILE), ('friday_to_gateway_connected', H.Recovery.RECONNECT_BRIDGE), ('mcp_server_alive', H.Recovery.RESTART_MCP), ('mcp_sse_connected', H.Recovery.RECONNECT_SSE), ('hermes_bridge_ready', H.Recovery.RECONNECT_BRIDGE), ('active_workrun_reachable', H.Recovery.RECONCILE_WORKRUN)])
def test_each_layer_maps_to_its_own_repair(failing, expected):
    signals = {name: True for name in H.LAYERS}
    signals[failing] = False
    assert H.Report(signals).recovery is expected