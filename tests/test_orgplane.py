"""OrganizationControlPlane - neutrality, optionality, honest degradation."""
import json
from friday import orgplane


def make(tmp_path):
    return orgplane.LocalControlPlane(tmp_path / 'org.sqlite3')


def test_operation_lifecycle(tmp_path):
    plane = make(tmp_path)
    op = plane.create_operation('WatchCo Experiment', goal='20 qualified purchase intents')
    op_id = op['op_id']
    w1 = plane.assign_work(op_id, 'market validation', assignee='hermes')
    plane.assign_work(op_id, 'supplier research', assignee='hermes')
    plane.record_work_product(w1['work_id'], 'validation report: viable')
    plane.record_decision(op_id, 'proceed to supplier stage')
    plane.record_cost(op_id, 420.5, detail='research tokens')
    status = plane.get_status(op_id)
    assert status['objective'] == '20 qualified purchase intents'
    assert status['work_total'] == 2
    assert status['work_done'] == 1
    assert status['cost_total'] == 420.5
    assert status['decisions'] == ['proceed to supplier stage']


def test_pause_resume(tmp_path):
    plane = make(tmp_path)
    op_id = plane.create_operation('x')['op_id']
    assert plane.pause(op_id)['state'] == 'PAUSED'
    assert plane.resume(op_id)['state'] == 'ACTIVE'


def test_customer_shape_has_no_backend_jargon(tmp_path):
    """The invisibility rule: status speaks objective/work/progress/cost,
    never 'paperclip', 'heartbeat', 'tenant', 'agent role'."""
    plane = make(tmp_path)
    op_id = plane.create_operation('shop', goal='first sale')['op_id']
    text = json.dumps(plane.get_status(op_id)).lower()
    for jargon in ('paperclip', 'heartbeat', 'tenant', 'company_id'):
        assert jargon not in text


def test_paperclip_adapter_degrades_honestly(tmp_path):
    """Paperclip is not installed here: the adapter must SAY so on every
    result while still doing the durable local work - never fake
    connectivity."""
    adapter = orgplane.PaperclipAdapter(fallback=make(tmp_path))
    ok, _ = adapter.available()
    assert ok is False
    op = adapter.create_operation('degraded op', goal='g')
    assert op['status'] == 'succeeded'
    assert 'unavailable' in op['paperclip']


def test_simple_tasks_never_touch_the_plane():
    """Structural optionality: the Hermes delegate path must not import
    orgplane - a simple task cannot invoke Paperclip even by accident."""
    import inspect
    from friday import hermes_bridge
    from friday.tools import hermes_control
    for module in (hermes_bridge, hermes_control):
        source = inspect.getsource(module)
        assert 'orgplane' not in source


def test_control_plane_accessor_prefers_truth(tmp_path):
    plane = orgplane.control_plane()
    assert plane.name == 'local'