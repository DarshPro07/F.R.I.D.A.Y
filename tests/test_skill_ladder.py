"""Skill ladder + recovery packet - anti-pollution and resume-from-files."""
from friday import skill_ladder as sl


def make(tmp_path):
    return sl.SkillLadder(tmp_path / 'skills.sqlite3')


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
    ladder.validate('resolve-launch-and-bridge', passed=True, validation='replayed against live Resolve: attach ok')
    current = ladder.current('resolve-launch-and-bridge')
    assert current['state'] == 'VALIDATED'


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