"""
The 2026-09-02 upstream set: commerce, company playbooks, harness templates,
transcription, and the OpenMausBot skills.

Each provider is tested for the thing that makes it safe rather than for the
thing that makes it work: the allowlist, the gate, the honest UNAVAILABLE.
"""

from __future__ import annotations

import pytest

from friday import contracts as c
from friday import fabric
from friday.fabric_adapters import (agent_reach_transcribe as ar,
                                    company_playbooks as cp,
                                    harness_templates as ht,
                                    mausbot_skills as mb,
                                    medusa_commerce as md,
                                    smartstore_commerce as ss)

NEW = ("company_playbooks", "harness_templates", "agent_reach_transcribe",
       "medusa_commerce", "smartstore_commerce", "mausbot_skills")


@pytest.fixture(autouse=True)
def clean():
    fabric.reload()
    yield
    fabric.reload()


@pytest.mark.parametrize("provider_id", NEW)
def test_every_new_provider_is_pinned_and_registered(provider_id):
    p = fabric.get(provider_id)
    assert len(p.commit) == 40
    assert p.family in fabric.FAMILIES


def test_commerce_is_a_real_family_with_medusa_first():
    ids = [p.id for p in fabric.candidates("commerce", "products")]
    assert ids[0] == "medusa_commerce"
    assert "smartstore_commerce" in ids


def test_smartstore_is_copyleft_and_isolated():
    p = fabric.get("smartstore_commerce")
    assert p.license_mode == fabric.COPYLEFT
    assert p.integration_mode in fabric.ISOLATED_MODES
    assert p.imported is False


@pytest.mark.parametrize("module", (md, ss))
def test_commerce_writes_are_gated_and_reads_are_open(module):
    p = module.DESCRIPTOR
    assert "commerce.write" in p.permissions
    writes = set(p.operations) - set(p.open_operations)
    assert writes and all("create" in w or "update" in w or "adjust" in w
                          for w in writes), writes
    assert "products" in p.open_operations


def test_commerce_never_exposes_payment_or_refund():
    for module in (md, ss):
        ops = " ".join(module.DESCRIPTOR.operations)
        assert "pay" not in ops and "refund" not in ops and "capture" not in ops


def test_a_write_without_a_grant_is_refused_before_any_http(monkeypatch):
    called = []
    monkeypatch.setattr(md, "call", lambda *a, **k: called.append(1))
    r = fabric.call("medusa_commerce", "product_create", body={"title": "x"})
    assert r.status == c.FAILED and "commerce.write" in r.error
    assert called == []


def test_absent_store_is_unavailable_with_the_reason(monkeypatch):
    monkeypatch.setenv(md.ENV_URL, "http://127.0.0.1:1")
    probe = md.health()
    assert probe["state"] == fabric.UNAVAILABLE
    assert md.ENV_URL in probe["detail"]


def test_medusa_id_must_be_a_bare_id():
    with pytest.raises(fabric.FabricError):
        md._fill("/admin/products/{id}", {"id": "../admin/users"})
    assert md._fill("/admin/products/{id}", {"id": "prod_1"}) == "/admin/products/prod_1"


def test_smartstore_key_is_numeric():
    with pytest.raises(fabric.FabricError):
        ss._fill("/odata/v1/Products({id})", {"id": "1) or 1=1"})


# --- playbooks and skill packs --------------------------------------------

cloned = pytest.mark.skipif(
    not cp._skillpack.pack_root(cp.UPSTREAM).is_dir(), reason="not cloned")


@cloned
def test_playbooks_read_one_at_a_time_and_refuse_unknown_names():
    assert "operations-pg" in cp.call("executives", None)
    text = cp.call("playbook", None, name="operations-pg")
    assert "Paul Graham" in text
    with pytest.raises(fabric.FabricError, match="known"):
        cp.call("playbook", None, name="../../../.env")
    with pytest.raises(fabric.FabricError):
        cp.call("skill", None, name="nope")


@cloned
def test_harness_checklist_is_structured():
    out = ht.call("checklist", None)
    assert out["count"] > 10 and all(isinstance(i, str) for i in out["items"])
    with pytest.raises(fabric.FabricError, match="known"):
        ht.call("template", None, name="README")


def test_mausbot_enterprise_tree_is_unreachable():
    assert set(mb.SKILLS) == {"phone-harness", "create-verification-skill"}
    with pytest.raises(fabric.FabricError):
        mb.call("skill", None, name="enterprise/LICENSE")


# --- transcription ----------------------------------------------------------

def test_transcribe_without_the_key_fails_by_alias_never_value(monkeypatch):
    monkeypatch.setattr(ar, "_groq_key", lambda: "")
    r = ar.call("transcribe", None, source="x.mp3")
    assert r.status == c.FAILED and ar.SECRET_ALIAS in r.error


def test_transcribe_puts_the_key_in_env_never_argv(monkeypatch):
    seen = {}
    monkeypatch.setattr(ar, "_groq_key", lambda: "sk-secret")
    monkeypatch.setattr(ar, "_call", lambda op, h, **kw: seen.update(kw) or None)
    ar.call("transcribe", None, source="x.mp3")
    assert seen["secrets"][ar.ENV_NAME] == "sk-secret"
    assert "sk-secret" not in " ".join(ar.COMMANDS["transcribe"].argv)


def test_agent_reach_exposes_only_the_two_safe_verbs():
    assert set(ar.DESCRIPTOR.operations) == {"doctor", "transcribe"}
    assert not any(v in " ".join(ar.COMMANDS) for v in ("install", "skill", "configure"))
