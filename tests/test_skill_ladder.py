"""Skill ladder + recovery packet - anti-pollution and resume-from-files."""
from friday import arena
from friday import skill_ladder as sl


def make(tmp_path):
    return sl.SkillLadder(tmp_path / 'skills.sqlite3')


def measured(tmp_path, name, *, version=1, ok=True):
    """An arena report in which skill `name` at `version` was replayed."""
    store = arena.ArenaStore(tmp_path / 'arena.json')
    score = arena.Score(gates={'success_rate': ok, 'minimum_cases': True}, cases=12, score=1.0 if ok else 0.4)
    report = arena.build_report(arena.ArenaKind.SKILL, {sl.arena_id(name, version): score}, provenance={'commit': 'abc1234', 'date': '2026-10-05'})
    return store, store.record(report).report_id


def test_capture_without_criteria_is_refused(tmp_path):
    out = make(tmp_path).capture('everything-i-did-today', 'steps...', criteria=['it happened'], evidence='x')
    assert out['status'] == 'refused'


def test_capture_without_evidence_is_refused(tmp_path):
    out = make(tmp_path).capture('resolve-launch', 'steps...', criteria=['expensive_rediscovery'], evidence='   ')
    assert out['status'] == 'refused'


def test_candidate_to_validated_skill(tmp_path):
    ladder = make(tmp_path)
    out = ladder.capture('resolve-launch-and-bridge', '1. probe socket 2. launch Resolve 3. attach bridge', criteria=['expensive_rediscovery', 'repeated_procedure'], evidence='rediscovered 3x across sessions, ~20 min each time')
    assert out['status'] == 'captured' and out['version'] == 1
    store, report_id = measured(tmp_path, 'resolve-launch-and-bridge')
    out = ladder.validate('resolve-launch-and-bridge', passed=True, validation='replayed against live Resolve: attach ok', arena_report=report_id, arena_store=store)
    assert out == {'status': 'succeeded', 'state': 'VALIDATED'}
    current = ladder.current('resolve-launch-and-bridge')
    assert current['state'] == 'VALIDATED'
    assert report_id in current['validation']


def test_validate_without_an_arena_report_does_not_reach_validated(tmp_path):
    ladder = make(tmp_path)
    ladder.capture('proc', 'steps', criteria=['repeated_procedure'], evidence='e')
    out = ladder.validate('proc', passed=True, validation='I ran it and it looked fine')
    assert out['status'] == 'refused' and out['state'] == 'CANDIDATE'
    assert ladder.current('proc')['state'] == 'CANDIDATE'


def test_a_free_text_report_id_does_not_reach_validated(tmp_path):
    ladder = make(tmp_path)
    ladder.capture('proc', 'steps', criteria=['repeated_procedure'], evidence='e')
    store, _ = measured(tmp_path, 'proc')
    out = ladder.validate('proc', passed=True, validation='ok', arena_report='validated by hand', arena_store=store)
    assert out['status'] == 'refused'
    assert ladder.current('proc')['state'] == 'CANDIDATE'


def test_a_claim_of_success_does_not_override_a_failing_report(tmp_path):
    ladder = make(tmp_path)
    ladder.capture('proc', 'steps', criteria=['repeated_procedure'], evidence='e')
    store, report_id = measured(tmp_path, 'proc', ok=False)
    out = ladder.validate('proc', passed=True, validation='ok', arena_report=report_id, arena_store=store)
    assert out['status'] == 'refused' and 'success_rate' in out['note']
    assert ladder.current('proc')['state'] == 'CANDIDATE'


def test_a_report_for_an_older_version_does_not_validate_the_new_one(tmp_path):
    ladder = make(tmp_path)
    ladder.capture('proc', 'v1', criteria=['repeated_procedure'], evidence='e')
    store, report_id = measured(tmp_path, 'proc', version=1)
    ladder.capture('proc', 'v2', criteria=['repeated_procedure'], evidence='e2')
    out = ladder.validate('proc', passed=True, validation='ok', arena_report=report_id, arena_store=store)
    assert out['status'] == 'refused'
    assert ladder.current('proc')['state'] == 'CANDIDATE'


def test_validating_a_skill_that_was_never_captured_is_absent(tmp_path):
    assert make(tmp_path).validate('ghost', passed=True, validation='ok')['status'] == 'absent'


def test_failed_validation_rejects_not_mutates(tmp_path):
    ladder = make(tmp_path)
    ladder.capture('flaky-proc', 'steps', criteria=['repeated_procedure'], evidence='e')
    ladder.validate('flaky-proc', passed=False, validation='replay broke')
    assert ladder.current('flaky-proc')['state'] == 'REJECTED'


def test_new_version_supersedes(tmp_path):
    ladder = make(tmp_path)
    ladder.capture('proc', 'v1 steps', criteria=['repeated_procedure'], evidence='e')
    out = ladder.capture('proc', 'v2 improved steps', criteria=['repeated_procedure'], evidence='e2')
    assert out['version'] == 2
    assert ladder.current('proc')['procedure'] == 'v2 improved steps'


def test_deprecate(tmp_path):
    ladder = make(tmp_path)
    ladder.capture('old', 'steps', criteria=['repeated_procedure'], evidence='e')
    ladder.deprecate('old', 'superseded by newer procedure')
    assert ladder.current('old') is None


def test_recovery_packet_round_trip(tmp_path):
    path = tmp_path / 'packet.json'
    out = sl.write_recovery_packet(path, objective='build vnext', state='phase 7', last_verified='orgplane tests 6/6', blocker='', next_action='first-run wizard', decisions=['paperclip degraded-honest', 'no graph memory yet'], pointers=['docs/architecture/VNEXT_RUN_STATE.json'])
    assert out['chars'] <= sl.PACKET_LIMIT
    packet = sl.read_recovery_packet(path)
    assert packet['objective'] == 'build vnext'
    assert packet['next_action'] == 'first-run wizard'


def test_recovery_packet_caps_hard(tmp_path):
    path = tmp_path / 'packet.json'
    out = sl.write_recovery_packet(path, objective='x' * 5000, state='y' * 5000, last_verified='z' * 5000, decisions=['dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd'] * 20, pointers=['pppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp'] * 30)
    assert out['chars'] <= sl.PACKET_LIMIT