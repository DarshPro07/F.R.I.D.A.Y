"""
The Friday↔Hermes bridge, proven against a scripted gateway.

Every test here runs the REAL supervisor - real subprocess, real stdio
JSON-RPC, real reader thread - against `fake_hermes_gateway.py`, which
speaks the same wire protocol as Hermes's `tui_gateway.entry`. What is
faked is the model, not the transport; the protocol handling being tested
is exactly what production uses.

The four journeys the bridge must survive:

    delegate  →  bounded bundle in, structured completion out
    steer     →  mid-run correction accepted without killing the turn
    question  →  clarify.request answered from evidence, or parked
    crash     →  gateway death detected, restart works, no invented state
"""
from __future__ import annotations
import sys
import time
from pathlib import Path
import pytest
from friday import hermes_bridge as hb
FAKE = str(Path(__file__).parent / 'fake_hermes_gateway.py')


def make(tmp_path, monkeypatch, *, flags: dict | None = None, answer=None) -> hb.HermesSupervisor:
    for key in ('FAKE_HERMES_CLARIFY', 'FAKE_HERMES_HANG', 'FAKE_HERMES_DIE'):
        monkeypatch.delenv(key, raising=False)
    for key, value in (flags or {}).items():
        monkeypatch.setenv(key, value)
    log = hb.WorkRunLog(tmp_path / 'bridge.sqlite3')
    supervisor = hb.HermesSupervisor(log=log, answer_question=answer, command=[sys.executable, FAKE], profile='')
    supervisor.READY_TIMEOUT = 20
    return supervisor


def test_bundle_renders_only_what_it_has():
    bundle = hb.TaskBundle(goal='fix the bug', acceptance=('it works',), skill_hints=('systematic-debugging',))
    text = bundle.render()
    assert 'GOAL\nfix the bug' in text
    assert '- it works' in text
    assert 'systematic-debugging' in text
    assert 'KNOWN FACTS' not in text
    assert 'DISALLOWED' not in text


def test_bundle_measure_flags_oversize():
    small = hb.TaskBundle(goal='tiny')
    assert small.measure()['oversized'] is False
    huge = hb.TaskBundle(goal='x' * 7000)
    assert huge.measure()['oversized'] is True


def test_work_run_log_round_trip(tmp_path):
    log = hb.WorkRunLog(tmp_path / 'runs.sqlite3')
    run_id = log.create(task='inspect', bundle_chars=100)
    log.update(run_id, status=hb.WORKING, hermes_session_id='abc')
    record = log.get(run_id)
    assert record['status'] == hb.WORKING
    assert record['hermes_session_id'] == 'abc'
    assert log.active()[0]['work_run_id'] == run_id
    log.update(run_id, status=hb.COMPLETE)
    assert log.active() == []


def test_work_run_log_refuses_unknown_fields(tmp_path):
    log = hb.WorkRunLog(tmp_path / 'runs.sqlite3')
    run_id = log.create(task='x')
    with pytest.raises(ValueError):
        log.update(run_id, hermes_prose_status='done')


def test_delegate_completes_with_structured_result(tmp_path, monkeypatch):
    supervisor = make(tmp_path, monkeypatch)
    try:
        out = supervisor.delegate(hb.TaskBundle(goal='inspect the project, do not modify'), wait=True, turn_timeout=30)
        record = out['result']
        assert record['status'] == hb.COMPLETE
        assert record['result'].startswith('DONE:')
        assert record['hermes_session_id']
        assert record['hermes_stored_session_id'].startswith('stored-')
        assert record['model'] == 'fake-model'
        assert 'input_tokens' in record['usage_json']
        assert out['bundle']['oversized'] is False
    finally:
        supervisor.stop()


def test_events_are_mapped_not_parsed(tmp_path, monkeypatch):
    seen = []
    supervisor = make(tmp_path, monkeypatch)
    supervisor.on_event = lambda kind, sid, payload: seen.append(kind)
    try:
        supervisor.delegate(hb.TaskBundle(goal='x'), wait=True, turn_timeout=30)
        assert 'HERMES_TOOL_START' in seen
        assert 'HERMES_TOOL_COMPLETE' in seen
        assert 'HERMES_RESULT' in seen
        assert 'HERMES_USAGE' in seen
    finally:
        supervisor.stop()


def test_steer_reaches_the_running_session(tmp_path, monkeypatch):
    supervisor = make(tmp_path, monkeypatch, flags={'FAKE_HERMES_HANG': '1'})
    try:
        out = supervisor.delegate(hb.TaskBundle(goal='long job'))
        result = supervisor.steer(out['work_run_id'], 'use the existing RunManager')
        assert result['status'] == 'queued'
        assert supervisor.log.get(out['work_run_id'])['status'] == hb.STEERED
    finally:
        supervisor.stop()


def test_interrupt_marks_partial(tmp_path, monkeypatch):
    supervisor = make(tmp_path, monkeypatch, flags={'FAKE_HERMES_HANG': '1'})
    try:
        out = supervisor.delegate(hb.TaskBundle(goal='long job'))
        supervisor.interrupt(out['work_run_id'])
        assert supervisor.log.get(out['work_run_id'])['status'] == hb.PARTIAL
    finally:
        supervisor.stop()


def test_grounded_question_is_answered_automatically(tmp_path, monkeypatch):
    def broker(question, options):
        assert 'storage engine' in question
        assert options == ['sqlite', 'postgres']
        return 'sqlite'
    supervisor = make(tmp_path, monkeypatch, flags={'FAKE_HERMES_CLARIFY': '1'}, answer=broker)
    try:
        out = supervisor.delegate(hb.TaskBundle(goal='build storage'), wait=True, turn_timeout=30)
        record = out['result']
        assert record['status'] == hb.COMPLETE
        assert record['result'] == 'ANSWERED-WITH: sqlite'
        assert record['pending_question'] == ''
    finally:
        supervisor.stop()


def test_unknown_question_is_parked_not_guessed(tmp_path, monkeypatch):
    supervisor = make(tmp_path, monkeypatch, flags={'FAKE_HERMES_CLARIFY': '1'}, answer=lambda q, o: None)
    try:
        out = supervisor.delegate(hb.TaskBundle(goal='build storage'))
        deadline = time.time() + 15
        while time.time() < deadline:
            record = supervisor.log.get(out['work_run_id'])
            if record['status'] == hb.WAIT_USER:
                break
            time.sleep(0.1)
        assert record['status'] == hb.WAIT_USER
        assert 'storage engine' in record['pending_question']
    finally:
        supervisor.stop()


def test_crash_is_detected_and_restart_recovers(tmp_path, monkeypatch):
    supervisor = make(tmp_path, monkeypatch, flags={'FAKE_HERMES_DIE': '1'})
    try:
        out = supervisor.delegate(hb.TaskBundle(goal='doomed'))
        deadline = time.time() + 10
        while time.time() < deadline and supervisor.alive():
            time.sleep(0.1)
        assert not supervisor.alive()
        monkeypatch.delenv('FAKE_HERMES_DIE')
        supervisor.restart()
        assert supervisor.alive()
        record = supervisor.log.get(out['work_run_id'])
        assert record['status'] == hb.STARTING
        again = supervisor.delegate(hb.TaskBundle(goal='works now'), wait=True, turn_timeout=30)
        assert again['result']['status'] == hb.COMPLETE
    finally:
        supervisor.stop()


def test_health_answers_with_a_real_rpc(tmp_path, monkeypatch):
    supervisor = make(tmp_path, monkeypatch)
    try:
        supervisor.start()
        report = supervisor.health()
        assert report['alive'] is True
        assert report['commands'] >= 1
    finally:
        supervisor.stop()
    assert supervisor.health()['alive'] is False


def test_request_without_process_refuses_loudly(tmp_path, monkeypatch):
    supervisor = make(tmp_path, monkeypatch)
    with pytest.raises(hb.HermesUnavailable):
        supervisor.request('session.status', {})


def test_missing_profile_refuses_at_start(tmp_path, monkeypatch):
    """A profile that does not exist is a loud refusal with the fix named,
    never a silent fall-through onto the shared default HERMES_HOME."""
    monkeypatch.delenv(hb.ENV_PROFILE_HOME, raising=False)
    supervisor = hb.HermesSupervisor(log=hb.WorkRunLog(tmp_path / 'p.sqlite3'), command=[sys.executable, FAKE], profile='no-such-profile-xyz')
    with pytest.raises(hb.HermesUnavailable) as err:
        supervisor.start()
    assert 'profile create' in str(err.value)


def test_profile_home_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(hb.ENV_PROFILE_HOME, str(tmp_path))
    assert hb.profile_home('anything') == str(tmp_path)
    monkeypatch.setenv(hb.ENV_PROFILE_HOME, str(tmp_path / 'absent'))
    assert hb.profile_home('anything') == ''


def test_gateway_env_carries_the_profile_home(tmp_path, monkeypatch):
    """The child gateway gets HERMES_HOME = the profile dir - the exact
    variable `hermes -p <name>` sets, which is the isolation boundary."""
    profile_dir = tmp_path / 'friday-profile'
    profile_dir.mkdir()
    monkeypatch.setenv(hb.ENV_PROFILE_HOME, str(profile_dir))
    captured = {}
    import subprocess as sp
    real_popen = sp.Popen

    def spy(cmd, **kwargs):
        captured['env'] = kwargs.get('env', {})
        return real_popen(cmd, **kwargs)
    monkeypatch.setattr(hb.subprocess, 'Popen', spy)
    supervisor = hb.HermesSupervisor(log=hb.WorkRunLog(tmp_path / 'p.sqlite3'), command=[sys.executable, FAKE], profile='friday')
    supervisor.READY_TIMEOUT = 20
    try:
        supervisor.start()
        assert captured['env']['HERMES_HOME'] == str(profile_dir)
        assert supervisor.profile_dir == str(profile_dir)
        assert 'HERMES_SESSION_ID' not in captured['env']
    finally:
        supervisor.stop()


def test_tool_with_progress_is_running_not_stalled(tmp_path, monkeypatch):
    supervisor = make(tmp_path, monkeypatch)
    run_id = supervisor.log.create(task='x')
    supervisor.log.update(run_id, hermes_session_id='s1', status=hb.WORKING)
    supervisor._proc = type('P', (), {'poll': lambda self: None})()
    now = time.time()
    supervisor._activity['s1'] = {'current_tool': 'terminal', 'last_event_at': now, 'last_tool_start_at': now - 900, 'last_tool_progress_at': now - 5}
    verdict = supervisor.stall_state(run_id)
    assert verdict['state'] == supervisor.TOOL_RUNNING
    supervisor._activity['s1'] = {'current_tool': 'read_file', 'last_event_at': now - 45, 'last_tool_start_at': now - 45}
    verdict = supervisor.stall_state(run_id)
    assert verdict['state'] == supervisor.TOOL_STALLED
    assert verdict['current_tool'] == 'read_file'
    assert verdict['stall_ceiling_s'] == 30.0
    assert verdict['tool_silent_s'] > 30.0
    supervisor._activity['s1'] = {'current_tool': 'terminal', 'last_event_at': now - 45, 'last_tool_start_at': now - 45}
    assert supervisor.stall_state(run_id)['state'] == supervisor.TOOL_RUNNING


def test_tool_class_ceilings_resolve_prefixes(tmp_path, monkeypatch):
    supervisor = make(tmp_path, monkeypatch)
    assert supervisor.tool_stall_ceiling('read_file') == 30.0
    assert supervisor.tool_stall_ceiling('mcp__read_file') == 30.0
    assert supervisor.tool_stall_ceiling('terminal') == 600.0
    assert supervisor.tool_stall_ceiling('never_heard_of_it') == supervisor.DEFAULT_TOOL_STALL_S


def test_dead_gateway_is_named_not_inferred(tmp_path, monkeypatch):
    supervisor = make(tmp_path, monkeypatch)
    run_id = supervisor.log.create(task='x')
    supervisor.log.update(run_id, hermes_session_id='s1', status=hb.WORKING)
    assert supervisor.stall_state(run_id)['state'] == supervisor.GATEWAY_DEAD


def test_terminal_run_is_idle(tmp_path, monkeypatch):
    supervisor = make(tmp_path, monkeypatch)
    run_id = supervisor.log.create(task='x')
    supervisor.log.update(run_id, status=hb.COMPLETE)
    assert supervisor.stall_state(run_id)['state'] == supervisor.IDLE


def test_recover_interrupts_a_stalled_tool(tmp_path, monkeypatch):
    """End-to-end against the fake: a hanging tool crosses the stall
    ceiling and recover_stalled() interrupts it - PARTIAL, not eternal
    WORKING."""
    supervisor = make(tmp_path, monkeypatch, flags={'FAKE_HERMES_HANG': '1'})
    supervisor.TOOL_STALL_CLASSES = dict(supervisor.TOOL_STALL_CLASSES)
    supervisor.TOOL_STALL_CLASSES['read_file'] = 1.0
    supervisor.DEFAULT_TOOL_STALL_S = 1.0
    supervisor.TURN_STALL_S = 2.0
    try:
        out = supervisor.delegate(hb.TaskBundle(goal='doomed to hang'))
        sid = supervisor.log.get(out['work_run_id'])['hermes_session_id']
        deadline = time.time() + 10
        while time.time() < deadline:
            state = supervisor.stall_state(out['work_run_id'])['state']
            if state in (supervisor.TOOL_STALLED, supervisor.TURN_STALLED):
                break
            time.sleep(0.3)
        assert state in (supervisor.TOOL_STALLED, supervisor.TURN_STALLED)
        outcome = supervisor.recover_stalled(out['work_run_id'])
        assert outcome['action'] in ('interrupted', 'restarted_gateway')
        assert supervisor.log.get(out['work_run_id'])['status'] in (hb.PARTIAL, hb.STARTING, hb.FAILED)
    finally:
        supervisor.stop()