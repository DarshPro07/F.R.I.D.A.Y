"""The rest of the integration-gap plan: SVC-01, LEARN-01, CENSUS-01, HEALTH-01.

Plus the per-operation permission field that landed when GATE-01's fail-closed
gate met a provider declaring one permission across three operations, two of
which its own notes call open.
"""
import pathlib

import pytest

from friday import fabric
from friday import fabric_memory
from friday import fabric_process as fp
from friday import fabric_service as fs


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    # A real database per test: what the fabric learns is now durable, so a
    # test that wrote into the live store would teach production from fixtures.
    monkeypatch.setenv("ADA_DB", str(tmp_path / "t.sqlite3"))
    from friday.toolsets import memory as M
    M.reset_store(None)
    fabric_memory.forget()
    yield
    fabric_memory.forget()
    M.reset_store(None)
    fp.stop_all()


def test_what_the_fabric_learns_survives_a_restart():
    """The defect this replaces: the tally lived in a module dict, so it was
    relearned from zero on every start - and Friday starts most days."""
    for _ in range(3):
        fabric_memory.record("p", "go", False)
    assert fabric_memory.score("p", "go") < 0
    # A "restart" is the cache going away; the store is what remains.
    fabric_memory.forget()
    assert fabric_memory.score("p", "go") < 0


def test_a_store_that_will_not_write_does_not_cost_the_call(monkeypatch):
    monkeypatch.setattr(fabric_memory, "_store",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    fabric_memory.record("p", "go", True)   # must not raise
    assert fabric_memory.score("p", "go") == 0


def test_pruning_drops_nothing_inside_the_window():
    fabric_memory.record("p", "go", True)
    assert fabric_memory.prune() == 0
    assert fabric_memory.report()


# --- per-operation permissions (the GATE-01 follow-up) ---------------------


def test_knowing_is_not_doing():
    """`security.skill` reads an attack procedure and stays gated;
    `security.search` reads the index and must not be."""
    provider = fabric.get("security_skills")
    assert "security.authorized_scope" in provider.permissions
    assert set(provider.open_operations) == {"catalogue", "search"}
    assert "skill" not in provider.open_operations


def test_an_open_operation_needs_no_grant():
    out = fabric.call("security_skills", "search", query="credential dumping")
    assert out.status == "succeeded", out.error


def test_a_gated_operation_still_refuses_without_the_grant():
    out = fabric.call("security_skills", "skill",
                      name="abusing-dpapi-for-credential-access")
    assert out.status == "failed"
    assert "security.authorized_scope" in out.error


def test_ranking_and_enforcement_agree():
    """A provider `candidates()` hides that `call()` would allow is a
    capability that silently vanishes from the menu."""
    pool = fabric.candidates("security", "search", authorized=frozenset())
    assert any(p.id == "security_skills" for p in pool)
    gated = fabric.candidates("security", "skill", authorized=frozenset())
    assert not any(p.id == "security_skills" for p in gated)


# --- LEARN-01: selection learns from outcomes ------------------------------


def test_one_outcome_is_not_a_habit():
    fabric_memory.record("p", "go", True)
    assert fabric_memory.score("p", "go") == 0


def test_repeated_success_earns_a_bonus():
    for _ in range(3):
        fabric_memory.record("p", "go", True)
    assert fabric_memory.score("p", "go") > 0


def test_repeated_failure_earns_a_penalty():
    for _ in range(3):
        fabric_memory.record("p", "go", False)
    assert fabric_memory.score("p", "go") < 0


def test_a_split_record_teaches_nothing():
    for _ in range(3):
        fabric_memory.record("p", "go", True)
        fabric_memory.record("p", "go", False)
    assert fabric_memory.score("p", "go") == 0


def test_the_adjustment_is_capped():
    for _ in range(500):
        fabric_memory.record("p", "go", False)
    assert fabric_memory.score("p", "go") == -fabric_memory.CAP


class _P:
    def __init__(self, pid):
        self.id = pid


def test_rank_is_stable_for_providers_it_knows_nothing_about():
    """Cost and risk are the primary rule; this only breaks ties."""
    order = (_P("a"), _P("b"), _P("c"))
    assert [p.id for p in fabric_memory.rank(order, "go")] == ["a", "b", "c"]


def test_a_failing_provider_sinks_below_its_fallback():
    for _ in range(4):
        fabric_memory.record("a", "go", False)
        fabric_memory.record("b", "go", True)
    ranked = fabric_memory.rank((_P("a"), _P("b")), "go")
    assert [p.id for p in ranked] == ["b", "a"]


def test_a_refusal_is_not_recorded_as_a_provider_failure():
    """A permission gate firing says nothing about whether the provider works."""
    fabric.call("security_skills", "skill", name="anything")
    assert fabric_memory.score("security_skills", "skill") == 0


def test_a_real_call_is_recorded():
    fabric.call("dummy", "echo", text="hi", authorized=frozenset({"x"}))
    fabric.call("dummy", "echo", text="hi", authorized=frozenset({"x"}))
    assert fabric_memory.score("dummy", "echo") > 0


def test_report_is_json_shaped():
    fabric_memory.record("p", "go", True)
    row = fabric_memory.report()[0]
    assert {"provider", "operation", "calls", "succeeded", "failed",
            "adjustment"} <= set(row)


# --- SVC-01: the HTTP service contract -------------------------------------


def test_a_non_loopback_base_url_is_refused():
    """A third-party web app Friday started must not be reachable off-box."""
    service = fs.Service(spec=fp.Spec(argv=("x",)),
                         base_url="http://0.0.0.0:{port}")
    child = fp.Child(provider_id="x", spec=service.spec, port=9)
    with pytest.raises(fs.ServiceError) as exc:
        fs._base(service, child)
    assert "loopback" in str(exc.value)


def test_the_url_comes_from_the_supervisors_port():
    """One source of truth: no port field on the descriptor, no port table."""
    service = fs.Service(spec=fp.Spec(argv=("x",)))
    child = fp.Child(provider_id="x", spec=service.spec, port=54321)
    assert fs._base(service, child) == "http://127.0.0.1:54321"


def test_only_idempotent_gets_are_retried():
    """A retried POST is two of whatever it made."""
    assert fs._should_retry(fs.Endpoint(method="GET"))
    assert not fs._should_retry(fs.Endpoint(method="POST"))


def test_an_unknown_placeholder_is_refused():
    with pytest.raises(fs.ServiceError):
        fs._fill("/api/{missing}", {"other": 1})


def test_evidence_does_not_overclaim():
    text = fs._evidence("p", fs.Endpoint(), "http://127.0.0.1:1/", 200, 0.1)
    assert "not a check of what the service did" in text


def test_health_on_a_service_never_started_is_registered():
    out = fs.health("never-started", fs.Service(spec=fp.Spec(argv=("x",))))
    assert out["state"] == fabric.REGISTERED


def test_an_unknown_operation_is_refused():
    class _Prov:
        id = "svc"
    out = fs.request(_Prov(), "nope", fs.Service(spec=fp.Spec(argv=("x",))), {})
    assert out.status == "failed" and "nope" in out.error


# --- CENSUS-01: unclassified clones become impossible ----------------------


def _matrix():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "integration_matrix",
        pathlib.Path(__file__).resolve().parent.parent
        / "scripts" / "integration_matrix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_clone_lands_in_exactly_one_bucket():
    data = _matrix().survey()
    assert data["clones"] > 0
    statuses = {row["status"] for row in data["rows"]}
    assert statuses <= {"INTEGRATED", "REFERENCE_ONLY", "UNCLASSIFIED"}
    # Every unclassified row is named, so --check can report it by name.
    named = {row["upstream"] for row in data["rows"]
             if row["status"] == "UNCLASSIFIED"}
    assert named == set(data["unclassified"])


def test_check_fails_on_an_unclassified_clone(monkeypatch):
    """The gate itself, not the current data.

    The first version of this test asserted `main(--check) == (1 if
    unclassified else 0)`, which is true whatever the code does - it restated
    the implementation instead of checking it. This forces the condition.
    """
    module = _matrix()
    monkeypatch.setattr(module, "clones", lambda: ["some-new-clone"])
    monkeypatch.setattr(module, "providers", lambda: {})
    monkeypatch.setattr(module, "REFERENCE_ONLY", {})
    assert module.main(["--check"]) == 1


def test_check_passes_once_everything_is_classified(monkeypatch):
    module = _matrix()
    monkeypatch.setattr(module, "clones", lambda: ["some-new-clone"])
    monkeypatch.setattr(module, "providers", lambda: {})
    monkeypatch.setattr(module, "REFERENCE_ONLY",
                        {"some-new-clone": "read for patterns only"})
    assert module.main(["--check"]) == 0


def test_a_demotion_needs_a_reason():
    """A REFERENCE_ONLY entry with an empty reason is 'forgotten' wearing
    'decided' as a disguise."""
    module = _matrix()
    assert all(reason.strip() for reason in module.REFERENCE_ONLY.values())


# --- HEALTH-01: presence is not function -----------------------------------


def test_an_empty_entry_file_is_degraded_not_ready(tmp_path, monkeypatch):
    from friday.fabric_adapters import _skillpack
    monkeypatch.setattr(_skillpack, "UPSTREAM", tmp_path)
    pack = tmp_path / "somepack"
    pack.mkdir()
    (pack / "SKILL.md").write_text("#\n", encoding="utf-8")
    out = _skillpack.health("somepack", "SKILL.md")
    assert out["state"] == fabric.DEGRADED
    assert "empty" in out["detail"]


def test_a_real_entry_file_is_ready(tmp_path, monkeypatch):
    from friday.fabric_adapters import _skillpack
    monkeypatch.setattr(_skillpack, "UPSTREAM", tmp_path)
    pack = tmp_path / "somepack"
    pack.mkdir()
    (pack / "SKILL.md").write_text("# A real skill\n" + "x" * 200,
                                   encoding="utf-8")
    assert _skillpack.health("somepack", "SKILL.md")["state"] == fabric.READY


def test_a_missing_clone_is_unavailable(tmp_path, monkeypatch):
    from friday.fabric_adapters import _skillpack
    monkeypatch.setattr(_skillpack, "UPSTREAM", tmp_path)
    out = _skillpack.health("nothing-here", "SKILL.md")
    assert out["state"] == fabric.UNAVAILABLE
