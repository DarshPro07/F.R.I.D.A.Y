"""The Android adapter stays capability-scoped (MOBILE-001).

PRD 64: the mobile adapter is not the product brain. These tests prove a
phone action cannot exceed FRIDAY's authority: the fabric permission gate,
then a per-call MobileGrant (apps, actions, expiry, approval) checked before
the device bridge is ever touched, with an audit row either way. The device
bridge is a stub; no phone is involved.
"""
from __future__ import annotations

import pytest

from friday import fabric
from friday import trust as T
from friday.fabric_adapters import android_phone as A

PERMISSION = frozenset({"mobile.app_interaction"})
APP = "com.example.notes"


class StubBridge:
    def __init__(self):
        self.calls = []
        self.front = APP

    def foreground(self):
        self.calls.append(("foreground",))
        return self.front

    def status(self):
        self.calls.append(("status",))
        return {"device": "stub-phone", "authorised": True}

    def open_app(self, app):
        self.calls.append(("open_app", app))
        return {"opened": app}

    def read_screen(self, app):
        self.calls.append(("read_screen", app))
        return {"app": app, "text": "hello"}

    def tap_text(self, app, text):
        self.calls.append(("tap_text", app, text))
        return {"tapped": text}


def grant(**over) -> dict:
    base = dict(apps=(APP,), actions=("open_app", "read_screen"),
                expires_at=T.expiry_in(1), approval_id="apr-mobile-1")
    base.update(over)
    return base


@pytest.fixture
def phone(tmp_path):
    bridge = StubBridge()
    A.register_bridge(bridge)
    log = T.AuditLog(tmp_path / "audit.sqlite3")
    T.configure_audit(log)
    fabric.reload()
    yield bridge, log
    A.clear_bridge()
    fabric._ACTIVE.pop("android_phone", None)
    T.configure_audit(None)
    log.close()


def act(operation, *, authorized=PERMISSION, **args):
    return fabric.call("android_phone", operation, run_id="obj-mobile",
                       authorized=authorized, **args)


def test_the_descriptor_is_restricted_builtin_and_needs_the_app_permission():
    fabric.reload()
    p = fabric.get("android_phone")
    assert p.family == "mobile" and p.risk == "restricted"
    assert p.integration_mode == fabric.BUILTIN and p.upstream == ""
    assert p.permissions == ("mobile.app_interaction",)
    assert p.open_operations == ("status",)
    assert set(p.operations) == {"status", "open_app", "read_screen", "tap_text"}


def test_with_no_bridge_the_provider_is_unavailable_and_nothing_runs():
    A.clear_bridge()
    fabric._ACTIVE.pop("android_phone", None)
    fabric.reload()
    assert fabric.activate("android_phone").state == fabric.UNAVAILABLE
    out = act("open_app", app=APP, mobile_authorization=grant())
    assert out.status == "failed" and "UNAVAILABLE" in out.error


def test_status_is_open_but_acting_needs_the_permission(phone):
    bridge, _ = phone
    assert act("status", authorized=frozenset()).status == "succeeded"
    out = act("open_app", authorized=frozenset(), app=APP,
              mobile_authorization=grant())
    assert out.status == "failed" and "not granted" in out.error
    assert ("open_app", APP) not in bridge.calls


def test_permission_alone_is_not_authority_a_grant_is_required(phone):
    bridge, log = phone
    out = act("open_app", app=APP)
    assert out.status == "failed" and "mobile_authorization" in out.error
    assert bridge.calls == []
    assert [r["decision"] for r in log.query()] == ["DENY"]


@pytest.mark.parametrize("operation,args,over,why", [
    ("open_app", {"app": "com.bank.pay"}, {}, "outside"),
    ("tap_text", {"app": APP, "text": "Save"}, {}, "not in"),
    ("open_app", {"app": APP}, {"expires_at": "2000-01-01T00:00:00+00:00"}, "expired"),
    ("tap_text", {"app": APP, "text": "Enter your password"},
     {"actions": ("tap_text",)}, "sensitive"),
    ("open_app", {"app": APP}, {"approval_id": ""}, "invalid"),
])
def test_an_out_of_scope_action_is_refused_before_the_device_and_audited(
        phone, operation, args, over, why):
    bridge, log = phone
    out = act(operation, mobile_authorization=grant(**over), **args)
    assert out.status == "failed" and why in out.error
    assert bridge.calls == []
    assert [r["decision"] for r in log.query()] == ["DENY"]


def test_an_in_scope_action_reaches_the_device_and_is_audited(phone):
    bridge, log = phone
    out = act("read_screen", app=APP, mobile_authorization=grant())
    assert out.status == "succeeded", out.error
    assert bridge.calls == [("foreground",), ("read_screen", APP)]
    assert out.output == {"app": APP, "text": "hello"}
    rows = log.query()
    assert [r["decision"] for r in rows] == ["ALLOW"]
    assert rows[0]["target"] == APP and rows[0]["detail"]["approval_id"] == "apr-mobile-1"


@pytest.mark.parametrize("operation,args", [
    ("read_screen", {}),
    ("tap_text", {"text": "Save"}),
])
def test_a_granted_app_cannot_read_or_tap_another_app_in_front(phone, operation, args):
    bridge, log = phone
    bridge.front = "com.bank.pay"
    out = act(operation, app=APP, **args, mobile_authorization=grant(
        actions=("read_screen", "tap_text")))
    assert out.status == "failed" and "foreground" in out.error
    assert bridge.calls == [("foreground",)]
    assert [r["decision"] for r in log.query()] == ["DENY"]


def test_the_caller_cannot_supply_the_bridge_or_any_other_argument(phone):
    bridge, _ = phone
    out = act("open_app", app=APP, mobile_authorization=grant(),
              bridge=StubBridge())
    assert out.status == "failed" and "bridge" in out.error
    assert bridge.calls == []
