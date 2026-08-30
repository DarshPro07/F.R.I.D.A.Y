from __future__ import annotations
import inspect
from friday import confirmation
from friday.fsjail import FileJail
from friday.policy import FULL, PolicyEngine
from friday.store import Store
from friday.tools import file_control as FC
from friday.toolsets import files as F


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(function):
            self.tools[function.__name__] = function
            return function
        return register


def test_files_delete_schema_and_original_run_nonce_contract(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    target = workspace / 'permanent.txt'
    target.write_text('fixture', encoding='utf-8')
    F.reset_jail(FileJail(roots=(workspace,)))
    monkeypatch.setattr(F, 'ARTIFACTS_DIR', workspace / 'artifacts')
    store = Store(tmp_path / 'adapter.sqlite3')
    monkeypatch.setattr(FC, '_store', store)
    monkeypatch.setattr(FC, '_engine', PolicyEngine(autonomy=FULL))
    confirmation.reset()
    mcp = FakeMCP()
    FC.register(mcp)
    delete = mcp.tools['files_delete']
    signature = inspect.signature(delete)
    assert list(signature.parameters) == ['path', 'permanent', 'nonce']
    assert signature.parameters['permanent'].default is False
    assert signature.parameters['nonce'].default == ''
    asked = delete(str(target), permanent=True)
    nonce = asked['output']['confirm']['nonce']
    original_run_id = asked['run_id']
    confirmation.book.approve(nonce)
    executed = delete(str(target), permanent=True, nonce=nonce)
    assert executed['run_id'] == original_run_id
    assert executed['output']['result'] == F.DELETED
    assert executed['may_claim_completion'] is True
    rows = store._conn.execute('SELECT DISTINCT run_id FROM tool_results').fetchall()
    assert [row['run_id'] for row in rows] == [original_run_id]
    store.close()
    confirmation.reset()
    F.reset_jail(None)