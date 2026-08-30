"""Structural safety requirements for the live Agentability gate."""
import subprocess
from scripts import golden_agentability as gate


def test_batch_2d_is_present_in_the_agentability_gate():
    assert 'processes' in gate.UTTERANCES
    assert 'power' in gate.UTTERANCES
    process_wanted = {name for _said, wanted, _forbidden in gate.UTTERANCES['processes'] for name in wanted}
    power_wanted = {name for _said, wanted, _forbidden in gate.UTTERANCES['power'] for name in wanted}
    assert {'process_close', 'process_terminate'} <= process_wanted
    assert {'power_restart', 'power_lock', 'power_hibernate', 'power_sleep', 'power_shutdown'} <= power_wanted


def test_process_agentability_cases_cannot_name_a_real_application():
    templates = [said for said, _wanted, _forbidden in gate.UTTERANCES['processes']]
    assert sum(('{missing_close_process}' in said for said in templates)) == 1
    assert sum(('{missing_force_process}' in said for said in templates)) == 1
    assert gate.MISSING_CLOSE_PROCESS != gate.MISSING_FORCE_PROCESS


def test_window_minimize_case_is_not_an_ambiguous_close_request():
    said, _wanted, forbidden = next((case for case in gate.UTTERANCES['windows'] if 'windows_minimize' in case[1]))
    assert 'minimize' in said
    assert 'process_close' in forbidden


def test_audio_mutation_is_measured_before_inventory_can_pre_answer_it():
    cases = gate.UTTERANCES['audio']
    volume_index = next((i for i, case in enumerate(cases) if 'audio_session_volume' in case[1]))
    inventory_index = next((i for i, case in enumerate(cases) if 'audio_sessions' in case[1]))
    assert volume_index < inventory_index


def test_music_inventory_is_measured_before_play_can_pre_answer_it():
    cases = gate.UTTERANCES['music']
    current_index = next((i for i, case in enumerate(cases) if 'music_current' in case[1]))
    play_index = next((i for i, case in enumerate(cases) if 'music_play' in case[1]))
    assert current_index < play_index


def test_mcp_server_output_is_captured_for_crash_diagnosis(monkeypatch, tmp_path):
    captured = {}
    log_path = tmp_path / 'mcp.log'

    class Process:
        pid = 123

    def popen(*args, **kwargs):
        captured.update(kwargs)
        return Process()
    monkeypatch.setattr(gate, 'MCP_LOG', log_path)
    monkeypatch.setattr(gate.health, 'serving', lambda: False)
    monkeypatch.setattr(gate.health, 'wait_until_serving', lambda: True)
    monkeypatch.setattr(gate.subprocess, 'Popen', popen)
    gate.start_mcp()
    assert captured['stderr'] == subprocess.STDOUT
    assert captured['stdout'].name == str(log_path)