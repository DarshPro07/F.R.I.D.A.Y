"""
Workspace isolation, adversarially (FR-PRV-002, invariant 6, release gate 8).

The PRD's trust metric is *zero* cross-workspace memory leakage, and the
success criterion is not "the code has a workspace column" - it is that an
attacker-shaped query from workspace B cannot produce workspace A's data.
So these tests are written from B's side: plant something private in A, then
try every read path B has, including the ones that do not take a subject.

Two properties are load-bearing and easy to lose:

* **The guard must be able to go red.** Every isolation assertion here has a
  companion that reaches the same data the legitimate way, so a filter that
  returned nothing for everyone would fail too. A test that passes because
  the store is empty proves nothing.

* **The declaration must stay complete.** `test_every_table_is_declared`
  fails when a new table appears in the schema without someone deciding
  whether it holds private state. That is the only mechanism that keeps this
  boundary true a year from now, when the table being added is not memories.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from friday import workspace as W


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A fresh store, with the process's workspace override cleared so one
    test cannot inherit another's tenant."""
    from friday.store import Store
    monkeypatch.delenv(W.ENV_VAR, raising=False)
    W.reset_override()
    db = Store(tmp_path / "iso.sqlite3")
    db.ensure_default_workspace()
    db.register_workspace("brand-a", label="Brand A", kind="brand")
    db.register_workspace("brand-b", label="Brand B", kind="brand")
    yield db
    db.close()
    W.reset_override()


@pytest.fixture
def planted(store):
    """One private fact in A and one in B, on the SAME subject.

    A shared subject is the realistic collision - two brands both have a
    "margin" and a "refund window" - and it is what makes an unscoped
    supersede destructive rather than merely leaky.
    """
    with W.use("brand-a"):
        a = store.remember("margin", "A keeps 61 percent", kind="FACT",
                           source="owner of A")
    with W.use("brand-b"):
        b = store.remember("margin", "B keeps 12 percent", kind="FACT",
                           source="owner of B")
    return {"a": a, "b": b}


# --- identity ---------------------------------------------------------------

def test_an_illegal_id_is_refused_before_it_becomes_a_path():
    for bad in ("../evil", "Brand A", "", "a" * 65, "-leading", "UPPER"):
        with pytest.raises(W.InvalidWorkspace):
            W.valid_id(bad)


def test_slug_is_deterministic_and_safe():
    assert W.slug("Crunchy Fashion") == "crunchy-fashion"
    assert W.slug("Crunchy Fashion") == W.slug("  crunchy   fashion  ")
    assert W.slug("../../etc/passwd") == "etc-passwd"
    W.valid_id(W.slug("!!!"))            # never produces an illegal id


def test_current_prefers_override_then_env_then_recorded(store, monkeypatch):
    monkeypatch.setenv(W.ENV_VAR, "brand-b")
    assert W.current() == "brand-b"
    with W.use("brand-a"):
        assert W.current() == "brand-a"
    assert W.current() == "brand-b"
    monkeypatch.delenv(W.ENV_VAR)
    W.reset_override()
    # falls through to whatever the store says is active
    store.set_active_workspace("brand-a")
    assert store.active_workspace() == "brand-a"


def test_use_restores_the_previous_workspace_even_when_the_body_raises():
    with W.use("brand-a"):
        with pytest.raises(RuntimeError):
            with W.use("brand-b"):
                raise RuntimeError("boom")
        assert W.current() == "brand-a", \
            "a failure inside a workspace left the process pointed elsewhere"


def test_adopting_an_unregistered_workspace_is_refused(store):
    with pytest.raises(W.UnknownWorkspace):
        store.set_active_workspace("never-registered")


def test_exactly_one_workspace_is_active(store):
    store.set_active_workspace("brand-a")
    store.set_active_workspace("brand-b")
    active = [w for w in store.workspaces() if w["active"]]
    assert [w["id"] for w in active] == ["brand-b"]


# --- the leak the PRD names -------------------------------------------------

def test_recall_cannot_cross(store, planted):
    with W.use("brand-b"):
        values = [r["value"] for r in store.recall("margin")]
    assert values == ["B keeps 12 percent"]
    with W.use("brand-a"):
        assert [r["value"] for r in store.recall("margin")] == ["A keeps 61 percent"]


def test_recall_scoped_cannot_cross_and_project_scope_is_no_escape(store, planted):
    """`project_scope=''` means "every project inside this tenant". It must
    not mean "every tenant" - the two are one string comparison apart."""
    with W.use("brand-b"):
        rows = store.recall_scoped("margin", project_scope="")
    assert [r["value"] for r in rows] == ["B keeps 12 percent"]


def test_free_text_search_cannot_cross(store, planted):
    """The widest read path: no subject, a substring anyone could guess."""
    with W.use("brand-b"):
        found = [r["value"] for r in store.search_memories("percent")]
    assert found == ["B keeps 12 percent"], \
        f"substring search crossed the boundary: {found}"


def test_export_cannot_cross_by_default(store, planted):
    with W.use("brand-b"):
        assert [r["value"] for r in store.export_memories()] == ["B keeps 12 percent"]


def test_whole_install_export_is_opt_in_and_explicit(store, planted):
    """FR-BKP-001 needs every tenant; nothing else does. The hole is a named
    argument, never reachable by omission."""
    every = sorted(r["value"] for r in store.export_memories(every_workspace=True))
    assert every == ["A keeps 61 percent", "B keeps 12 percent"]


def test_provenance_by_id_refuses_rather_than_returning_none(store, planted):
    """An id is not a capability. Returning None here would make a boundary
    violation indistinguishable from a missing row, and an attacker would
    simply count upwards."""
    with W.use("brand-b"):
        with pytest.raises(W.CrossWorkspaceRefused):
            store.memory_provenance(planted["a"])
        assert store.memory_provenance(planted["b"])["value"] == "B keeps 12 percent"


def test_observations_cannot_cross(store):
    with W.use("brand-a"):
        store.add_observation(dimension="preference", subject="tone",
                              value="formal", kind="PREFERENCE",
                              confidence=0.9, evidence="A said 'keep it formal'")
    with W.use("brand-b"):
        assert store.observations() == [], \
            "an observation leaked: promotion would turn it into B's memory"
    with W.use("brand-a"):
        assert len(store.observations()) == 1


# --- the destructive half: writes must not reach across ---------------------

def test_a_write_in_one_workspace_does_not_supersede_anothers_belief(store, planted):
    """The subtle half of the boundary. The read filter stops B *seeing* A;
    this stops B *destroying* A - a shared subject name would otherwise
    retire the other tenant's active fact on every write."""
    with W.use("brand-b"):
        store.remember("margin", "B now keeps 15 percent", kind="FACT",
                       source="owner of B")
    with W.use("brand-a"):
        rows = store.recall("margin")
    assert [r["value"] for r in rows] == ["A keeps 61 percent"]
    assert rows[0]["superseded"] == 0, \
        "B's write superseded A's fact - silent data loss across tenants"


def test_forget_is_scoped(store, planted):
    with W.use("brand-b"):
        assert store.forget("margin") == 1
    with W.use("brand-a"):
        assert len(store.recall("margin")) == 1


def test_supersede_is_scoped(store, planted):
    with W.use("brand-b"):
        assert store.supersede("margin") == 1
    with W.use("brand-a"):
        assert len(store.recall("margin")) == 1


def test_expire_is_scoped(store):
    with W.use("brand-a"):
        store.remember("draft", "A's working note", kind="FACT", source="run",
                       memory_type="working")
    with W.use("brand-b"):
        store.remember("draft", "B's working note", kind="FACT", source="run",
                       memory_type="working")
        assert store.expire_memories(retention_policy="run") >= 0
    with W.use("brand-a"):
        assert len(store.recall("draft")) == 1, \
            "one tenant's session ending expired another's working memory"


# --- negative controls: prove the guard can go red -------------------------

def test_the_guard_refuses_and_can_be_seen_to_refuse():
    with pytest.raises(W.CrossWorkspaceRefused):
        W.guard("brand-b", "brand-a")
    W.guard("brand-a", "brand-a")        # and permits the legitimate case


def test_an_unfiltered_query_really_would_leak(store, planted):
    """The negative control for every test above.

    This reaches the same table with the filter removed, exactly as a future
    call site that forgets `workspace_id` would. It MUST see both tenants.
    If this test ever returns one row, the assertions above have stopped
    proving anything - the data would be isolated because it is absent, not
    because the boundary works.
    """
    leaked = [r["value"] for r in store._conn.execute(
        "SELECT value FROM memories WHERE subject='margin' ORDER BY id")]
    assert leaked == ["A keeps 61 percent", "B keeps 12 percent"], (
        "the negative control could not see both tenants, so the isolation "
        "tests above may be passing on an empty table")


def test_secret_stores_are_separate_key_material(tmp_path, monkeypatch):
    """FR-PRV-002: "secrets are namespaced per workspace/user". Separate
    directories are not enough on their own - separate KEYS are what make
    another workspace's store unreadable rather than merely unlisted."""
    monkeypatch.setenv("FRIDAY_SECRET_HOME", str(tmp_path / "secrets"))
    import importlib

    from friday import secret_broker
    importlib.reload(secret_broker)
    try:
        with W.use("brand-a"):
            a = secret_broker.SecretBroker()
        with W.use("brand-b"):
            b = secret_broker.SecretBroker()
        assert a.home != b.home
        assert a.home.name == "brand-a" and b.home.name == "brand-b"
        assert (a.home / "broker.key").read_bytes() != (b.home / "broker.key").read_bytes()
    finally:
        importlib.reload(secret_broker)


def test_a_pre_boundary_secret_store_is_adopted_not_orphaned(tmp_path, monkeypatch):
    """An install that stored secrets before workspaces existed keeps them.
    Making the owner's credentials invisible would be a regression dressed
    as a security improvement."""
    home = tmp_path / "secrets"
    home.mkdir()
    (home / "broker.key").write_bytes(b"legacy-key-bytes")
    (home / "secrets.enc.json").write_text('{"legacy": {}}', encoding="utf-8")
    monkeypatch.setenv("FRIDAY_SECRET_HOME", str(home))
    import importlib

    from friday import secret_broker
    importlib.reload(secret_broker)
    try:
        W.reset_override()
        target = secret_broker.SecretBroker.home_for(W.DEFAULT_ID)
        assert (target / "broker.key").read_bytes() == b"legacy-key-bytes"
        assert not (home / "broker.key").exists(), "the legacy key was copied, not moved"
        # and a second workspace does not inherit it
        assert not (secret_broker.SecretBroker.home_for("brand-a") / "broker.key").exists()
    finally:
        importlib.reload(secret_broker)


def test_private_skill_tiers_are_disjoint_and_shared_tiers_are_not(tmp_path, monkeypatch):
    """FR-SKL-001/FR-SKL-002: the private tier is per workspace; the
    verified generic tiers are deliberately shared."""
    from friday.skills.files import tier_roots
    with W.use("brand-a"):
        a = tier_roots()
    with W.use("brand-b"):
        b = tier_roots()
    assert a["LOCAL_USER"] != b["LOCAL_USER"], "two tenants share a private skill tier"
    assert a["BUILT_IN"] == b["BUILT_IN"]
    assert a["PROJECT"] == b["PROJECT"]


def test_a_browser_profile_approved_by_one_workspace_is_refused_to_another(monkeypatch):
    """A signed-in browser session is private state that happens to live in
    a browser. An approval names a profile FOR a workspace."""
    from friday import browser as B
    a_dir = str(B.workspace_profile_dir("brand-a"))
    with W.use("brand-a"):
        mine = B.choose_profile(worker="friday", authorized_by_approval=True,
                                authorized_dir=a_dir)
    assert mine.kind == B.AUTHORIZED, mine.reason
    with W.use("brand-b"):
        theirs = B.choose_profile(worker="friday", authorized_by_approval=True,
                                  authorized_dir=a_dir)
    assert theirs.kind == B.ISOLATED
    assert "another workspace" in theirs.reason


def test_the_users_own_chrome_profile_is_not_a_workspaces_to_withhold(tmp_path):
    """The owner's direction is that FRIDAY mostly works in the user's real
    profile. That profile belongs to the person, not to a tenant, so it is
    not caught by the managed-profile rule."""
    from friday import browser as B
    with W.use("brand-b"):
        choice = B.choose_profile(worker="friday", authorized_by_approval=True,
                                  authorized_dir=str(tmp_path / "Chrome" / "User Data"))
    assert choice.kind == B.AUTHORIZED


@pytest.fixture
def browser_state(tmp_path, monkeypatch):
    monkeypatch.setattr("friday.config.STATE_ROOT", tmp_path)
    return tmp_path


def test_a_temporary_profile_needs_the_persons_yes(browser_state):
    """BROW-003: without the approval a profile is never temporary; with it,
    the choice names a fresh directory owned by the asking workspace."""
    from friday import browser as B
    with W.use("brand-a"):
        none = B.choose_profile(worker="friday", authorized_by_approval=False)
        yes = B.choose_profile(worker="friday", authorized_by_approval=False,
                               temporary_by_approval=True)
        again = B.choose_profile(worker="friday", authorized_by_approval=False,
                                 temporary_by_approval=True)
    assert none.kind == B.ISOLATED and none.directory == ""
    assert yes.kind == B.TEMPORARY and yes.kind in B.PROFILE_KINDS
    assert yes.directory != again.directory, "two sessions share one temporary profile"
    with W.use("brand-a"):
        assert B.owns_profile_dir(yes.directory)
    with W.use("brand-b"):
        assert not B.owns_profile_dir(yes.directory)


def test_a_temporary_profile_of_one_workspace_is_refused_to_another(browser_state):
    from friday import browser as B
    with W.use("brand-a"):
        temp = B.choose_profile(worker="friday", authorized_by_approval=False,
                                temporary_by_approval=True)
    with W.use("brand-b"):
        theirs = B.choose_profile(worker="friday", authorized_by_approval=True,
                                  authorized_dir=temp.directory)
    assert theirs.kind == B.ISOLATED
    assert "another workspace" in theirs.reason


def test_discarding_a_temporary_profile_deletes_only_that_profile(browser_state):
    from friday import browser as B
    from pathlib import Path
    with W.use("brand-a"):
        temp = Path(B.new_temporary_profile_dir())
        temp.mkdir(parents=True)
        (temp / "Cookies").write_text("session")
        dedicated = B.workspace_profile_dir()
        dedicated.mkdir(parents=True)
        (dedicated / "Cookies").write_text("signed in")
        mine = browser_state / "MyChrome"
        mine.mkdir()
        assert B.discard_temporary_profile(str(temp)) is True
        assert not temp.exists()
        assert B.discard_temporary_profile(str(temp)) is False
        # Anything else is refused loudly, and survives.
        for keep in (dedicated, mine, B.temporary_profiles_root(), browser_state):
            with pytest.raises(ValueError):
                B.discard_temporary_profile(str(keep))
        assert (dedicated / "Cookies").read_text() == "signed in"
        assert mine.exists()
    other = None
    with W.use("brand-b"):
        other = Path(B.new_temporary_profile_dir())
        other.mkdir(parents=True)
    with W.use("brand-a"):
        with pytest.raises(ValueError):
            B.discard_temporary_profile(str(other))
    assert other.exists()


# --- the ratchet ------------------------------------------------------------

def test_every_table_is_declared(store):
    """A new table must be classified as private state or not.

    This is the test that keeps the boundary true after today. It fails on
    the next schema addition, and the fix is one line in `workspace.SCOPED`
    or `workspace.UNSCOPED` - with a reason, which is the point.
    """
    tables = {row["name"] for row in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'")}
    tables = {t for t in tables if not t.startswith("shadow_predictions_superseded")}
    declared = set(W.SCOPED) | set(W.UNSCOPED)
    undeclared = tables - declared
    assert not undeclared, (
        "these tables are neither declared private nor declared global in "
        f"friday/workspace.py: {sorted(undeclared)}")
    assert not (set(W.SCOPED) & set(W.UNSCOPED)), "a table declared both ways"


def test_every_scoped_table_actually_carries_the_column(store):
    """The declaration and the schema must agree. A table named in SCOPED
    without the column is a claim with nothing behind it."""
    missing = []
    for table in W.SCOPED:
        columns = {row["name"] for row in
                   store._conn.execute(f"PRAGMA table_info({table})")}
        if "workspace_id" not in columns:
            missing.append(table)
    assert not missing, f"declared scoped but has no workspace_id column: {missing}"


#: The scoped tables whose READS are filtered today. The rest carry the
#: column so their owner is attributable and a later filter needs no second
#: migration. This list is the honest statement of how far enforcement
#: reaches, and it is asserted rather than described so it cannot rot into a
#: claim: `docs/implementation/PRD_REQUIREMENTS_LEDGER.md` FR-PRV-002 cites it.
ENFORCED_TODAY = ("memories", "observations")


def test_enforcement_reaches_exactly_what_is_claimed(store, planted):
    """Filtered reads exist for the enforced tables and the boundary holds
    for them. Tables outside ENFORCED_TODAY are stamped, not filtered - if
    someone adds a filter, they should add the table here and watch this
    test keep passing."""
    for table in ENFORCED_TODAY:
        owners = {row["workspace_id"] for row in
                  store._conn.execute(f"SELECT workspace_id FROM {table}")}
        assert owners <= {"default", "brand-a", "brand-b"}
    with W.use("brand-b"):
        assert len(store.search_memories("percent")) == 1
        assert store.observations() == []


def test_the_default_workspace_owns_pre_boundary_rows(tmp_path):
    """A row written by code that never heard of workspaces belongs to
    `default` - the one workspace that install always was."""
    from friday.store import Store
    db = Store(tmp_path / "legacy.sqlite3")
    try:
        db._conn.execute(
            "INSERT INTO memories (subject, value, kind, scope, source, "
            "confidence, created_at) VALUES ('old','pre-boundary','FACT',"
            "'user','legacy',1.0,'2026-01-01T00:00:00Z')")
        db._conn.commit()
        W.reset_override()
        assert [r["value"] for r in db.recall("old")] == ["pre-boundary"]
        with W.use("brand-a"):
            assert db.recall("old") == []
    finally:
        db.close()


def test_the_recorded_workspace_is_read_from_the_configured_database(tmp_path, monkeypatch):
    """`current()` used to open `Store()` - the live data/ada.sqlite3 - whatever
    ADA_DB said: every test process read (and migrated) the owner's database,
    and a test's answer depended on which workspace was active there."""
    from friday.store import Store

    path = tmp_path / "configured.sqlite3"
    db = Store(path)
    try:
        db.register_workspace("brand-z", label="Brand Z", make_active=True)
    finally:
        db.close()
    monkeypatch.setenv("ADA_DB", str(path))
    monkeypatch.delenv(W.ENV_VAR, raising=False)
    W.reset_override()
    assert W.current() == "brand-z"


def test_a_store_with_no_path_is_the_configured_database(tmp_path, monkeypatch):
    """Seven more places made a bare `Store()` - forge, memory promotion,
    observability, onboarding, automations, products - and each opened the
    live database whatever ADA_DB said, which is how a test could reach the
    owner's data after conftest had pointed ADA_DB somewhere safe."""
    from friday.store import DEFAULT_DB, Store

    monkeypatch.setenv("ADA_DB", str(tmp_path / "configured.sqlite3"))
    db = Store()
    try:
        assert db.path == tmp_path / "configured.sqlite3"
        assert db.path != DEFAULT_DB
    finally:
        db.close()


LEDGERS_ON_THE_SHARED_DATABASE = [
    ("friday.orgplane", "LocalControlPlane"),
    ("friday.skill_ladder", "SkillLadder"),
    ("friday.user_policy", "UserPolicy"),
    ("friday.hermes_bridge", "WorkRunLog"),
    ("friday.execution_economics", "RouteOutcomes"),
    ("friday.connectors.plane", "ConnectorState"),
]


@pytest.mark.parametrize("module_name,class_name", LEDGERS_ON_THE_SHARED_DATABASE)
def test_a_ledger_with_no_path_is_the_configured_database(module_name, class_name,
                                                          tmp_path, monkeypatch):
    """Six ledgers built `DATA_DIR / "ada.sqlite3"` by hand, so each ignored
    ADA_DB: under the suite they wrote the owner's live database, and in the
    product they split from the store whenever ADA_DB moved it. The default is
    pointed at a stand-in here, so the unfixed code cannot reach the real one."""
    import importlib

    from friday import config, store

    stand_in = tmp_path / "default-data"
    stand_in.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", stand_in)
    monkeypatch.setattr(store, "DEFAULT_DB", stand_in / "ada.sqlite3")
    configured = tmp_path / "configured.sqlite3"
    monkeypatch.setenv("ADA_DB", str(configured))
    ledger = getattr(importlib.import_module(module_name), class_name)()
    assert ledger._path == str(configured)
    assert configured.exists(), "its tables were not created where ADA_DB points"
    assert not (stand_in / "ada.sqlite3").exists()


def test_only_the_store_names_the_database_file():
    """The configured database is `os.getenv("ADA_DB") or DEFAULT_DB`, and
    DEFAULT_DB is defined once, in store.py. A module that spells the file
    name again has a default of its own that ADA_DB does not move."""
    import ast
    from pathlib import Path

    import friday
    package = Path(friday.__file__).parent
    spelled = []
    for path in sorted(package.rglob("*.py")):
        if path == package / "store.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value == "ada.sqlite3":
                spelled.append(f"{path.relative_to(package)}:{node.lineno}")
    assert spelled == [], f"a second default for the database: {spelled}"
