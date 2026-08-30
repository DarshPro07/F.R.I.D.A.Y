from __future__ import annotations
from datetime import timedelta
import pytest
from friday import confirmation
from friday import contracts as c
from friday.fsjail import FileJail
from friday.policy import DELETE, FULL, PolicyEngine, TOOL_CATEGORIES
from friday.toolsets import files as F


@pytest.fixture
def root(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    artifacts = workspace / 'artifacts'
    artifacts.mkdir()
    F.reset_jail(FileJail(roots=(workspace,)))
    monkeypatch.setattr(F, 'ARTIFACTS_DIR', artifacts)
    confirmation.reset()
    yield workspace
    confirmation.reset()
    F.reset_jail(None)


def run(run_id: str | None = None):
    return c.Run(run_id=run_id, request='delete fixture', capability='files') if run_id else c.Run.create('delete fixture', capability='files')


def engine():
    return PolicyEngine(autonomy=FULL)


def test_recycle_requires_source_absence(root, monkeypatch):
    target = root / 'note.txt'
    target.write_text('owned fixture', encoding='utf-8')
    monkeypatch.setattr('send2trash.send2trash', lambda path: target.unlink())
    result = F.files_delete(run(), str(target), engine=engine())
    assert result.status == c.SUCCEEDED
    assert result.output['result'] == F.RECYCLED
    assert result.output['mode'] == 'recycle'
    assert not target.exists()


def test_recycle_os_failure_is_truthful(root, monkeypatch):
    target = root / 'refused.txt'
    target.write_text('owned fixture', encoding='utf-8')

    def refuse(path):
        raise OSError('trash unavailable')
    monkeypatch.setattr('send2trash.send2trash', refuse)
    result = F.files_delete(run(), str(target), engine=engine())
    assert result.status == c.FAILED
    assert result.output['result'] == F.FAILED
    assert target.exists()


def test_missing_file_and_directory_have_distinct_safe_results(root):
    missing = F.files_delete(run(), str(root / 'missing.txt'), engine=engine())
    directory = F.files_delete(run(), str(root), engine=engine())
    assert missing.status == c.OBSERVED
    assert missing.output['result'] == F.NOT_FOUND
    assert directory.status == c.CANCELLED
    assert directory.output['result'] == F.BLOCKED


def test_permanent_user_delete_consumes_exact_original_run_confirmation(root):
    target = root / 'permanent.txt'
    target.write_text('owned fixture', encoding='utf-8')
    original = run()
    asked = F.files_delete(original, str(target), permanent=True, engine=engine())
    nonce = asked.output['confirm']['nonce']
    assert confirmation.book.approve(nonce).ok
    executed = F.files_delete(run(original.run_id), str(target), permanent=True, nonce=nonce, engine=engine())
    reused = F.files_delete(run(original.run_id), str(target), permanent=True, nonce=nonce, engine=engine())
    assert executed.status == c.SUCCEEDED
    assert executed.run_id == original.run_id
    assert executed.output['result'] == F.DELETED
    assert reused.output['result'] == F.BLOCKED


def test_permanent_confirmation_refuses_retarget_expiry_and_reuse(root):
    first = root / 'first.txt'
    second = root / 'second.txt'
    first.write_text('one', encoding='utf-8')
    second.write_text('two', encoding='utf-8')
    original = run()
    asked = F.files_delete(original, str(first), permanent=True, engine=engine())
    nonce = asked.output['confirm']['nonce']
    confirmation.book.approve(nonce)
    retargeted = F.files_delete(run(original.run_id), str(second), permanent=True, nonce=nonce, engine=engine())
    assert retargeted.status == c.CANCELLED
    assert first.exists() and second.exists()
    pending = confirmation.book.pending[nonce]
    pending.expires_at = pending.created_at - timedelta(seconds=1)
    expired = F.files_delete(run(original.run_id), str(first), permanent=True, nonce=nonce, engine=engine())
    assert expired.status == c.CANCELLED
    assert first.exists()


def test_friday_artifact_can_be_cleaned_directly_without_nonce(root):
    target = root / 'artifacts' / 'temporary.txt'
    target.write_text('Friday-owned', encoding='utf-8')
    result = F.files_delete(run(), str(target), permanent=True, engine=engine())
    assert result.status == c.SUCCEEDED
    assert result.output['result'] == F.DELETED
    assert result.output['confirm'] is None


def test_jail_denylist_outside_root_and_reparse_fail_before_confirmation(root, tmp_path, monkeypatch):
    protected = root / '.env'
    protected.write_text('SECRET=fixture', encoding='utf-8')
    outside = tmp_path / 'outside.txt'
    outside.write_text('outside', encoding='utf-8')
    linked = root / 'linked.txt'
    linked.write_text('fixture', encoding='utf-8')
    original_check = F.is_reparse_point
    monkeypatch.setattr(F, 'is_reparse_point', lambda path: path == linked or original_check(path))
    results = [F.files_delete(run(), str(protected), permanent=True, engine=engine()), F.files_delete(run(), str(outside), permanent=True, engine=engine()), F.files_delete(run(), str(linked), permanent=True, engine=engine())]
    assert all((result.status == c.CANCELLED for result in results))
    assert all((result.output['result'] == F.BLOCKED for result in results))
    assert confirmation.book.pending == {}


def test_delete_tools_use_delete_policy_category():
    assert TOOL_CATEGORIES['files.delete'] == DELETE
    assert TOOL_CATEGORIES['files.delete_permanent'] == DELETE