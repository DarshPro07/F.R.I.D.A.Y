"""
ONB-001: the golden onboarding journey, end to end, on fakes only.

The acceptance sentence is "a new user completes setup and a useful task".
The unit tests prove each step of the interview; this proves the steps join:
a fresh state root, a first run that asks the first question, the essential
stages answered, setup recorded, then a first task that runs through the
durable-objective executor on what the interview learned and is verified by a
command the task cannot influence. Each step asserts rows, files and returned
dicts that a reopened process would find, never source text.
"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

from friday import evaluation as E
from friday import launcher
from friday import onboarding as O
from friday import workspace as W
from friday.contracts import PERSON
from friday.continuous import ContinuousTaskExecutor
from friday.objectives import TaskStatus, compile_objective
from friday.store import Store
from friday.user_policy import UserPolicy

LEVEL = "Autonomous Routine Work"


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A new user's machine: a state root that does not exist yet, an empty
    memory store and an empty policy database."""
    monkeypatch.delenv(W.ENV_VAR, raising=False)
    W.reset_override()
    home = tmp_path / "home"
    paths = {"home": home, "store": tmp_path / "memory.sqlite3",
             "policy": tmp_path / "policy.sqlite3"}
    store = Store(paths["store"])
    store.ensure_default_workspace()
    interview = O.Onboarding(store, policy=UserPolicy(paths["policy"]))
    yield interview, paths
    store.close()
    W.reset_override()


def _essential_answers(folder) -> dict:
    """What a person running a small shop would say. `company` is declined on
    purpose: declining counts toward the essentials, and the marker has to
    record it rather than pretend it was answered."""
    return {
        "call_me": "Asha", "profession": "I run a Shopify store",
        "languages": ["English", "Hindi"],
        "typical_work": "orders, customer support and product listings",
        "goals": ["answering the same customer emails"],
        "recurring_tasks": ["weekly stock count"],
        "working_hours": "10 to 7, Monday to Saturday",
        "applications": ["Chrome", "Shopify admin"],
        "websites": ["admin.shopify.com"],
        "folders": [str(folder)],
        "model_accounts": ["Claude"],
    }


async def _terminal(store, run_id, timeout=30.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        row = store.objective_run(run_id)
        if row["status"] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED"):
            return row
        await asyncio.sleep(0.05)
    raise AssertionError("the first task never reached a terminal state")


@pytest.mark.asyncio
async def test_a_new_user_completes_setup_and_a_useful_task(fresh, tmp_path, monkeypatch):
    interview, paths = fresh
    home = paths["home"]
    interview_class = O.Onboarding
    folder = tmp_path / "shop"
    folder.mkdir()
    for name in ("orders-march.csv", "stock.xlsx", "returns-policy.docx"):
        (folder / name).write_text(name, encoding="utf-8")

    # ---- 1: a fresh state root is a first run, and the launcher asks ------
    assert not home.exists()
    assert not O.is_onboarded(home)
    notice = interview.first_run_notice(home)
    assert any("What should I call you?" in line for line in notice), notice
    assert any("answer call_me" in line for line in notice), notice
    monkeypatch.setattr(O, "Onboarding", lambda: interview)
    assert launcher.onboarding_at_startup(home) == notice

    # ---- 2: setup cannot be closed before the essentials are answered -----
    with pytest.raises(ValueError, match="essential"):
        interview.finish(home, level=LEVEL)
    assert not O.is_onboarded(home), "a marker written early would stop the asking"

    # ---- 3: the essential stages, one declined ----------------------------
    answers = _essential_answers(folder)
    interview.skip("company", why="no brand name yet")
    for name, value in answers.items():
        out = interview.record({name: value})
    assert out["useful_now"] is True
    assert interview.complete_enough()
    assert interview.pending(), "personalisation continues after the essentials"
    assert launcher.onboarding_at_startup(home)[0].startswith("first run: the essential"), \
        "answered but not finished: the launcher should say how to finish, not re-ask"

    # ---- 4: finishing compiles policy and records setup -------------------
    done = interview.finish(home, level=LEVEL, reason="golden journey")
    assert done["status"] == "onboarded" and done["level"] == LEVEL
    assert O.is_onboarded(home)
    marker = json.loads(O.state_root_marker(home).read_text(encoding="utf-8"))
    assert marker["autonomy_level"] == LEVEL
    assert marker["skipped"] == ["company"]
    assert marker["answered"] == len(answers)
    assert interview.first_run_notice(home) == []
    assert launcher.onboarding_at_startup(home) == []

    # ---- 5: the profile and the policy survive a restart ------------------
    interview.store.close()
    store = Store(paths["store"])
    reopened = interview_class(store, policy=UserPolicy(paths["policy"]))
    profile = reopened.profile()
    assert profile.get("call_me") == "Asha"
    assert profile.get("folders") == [str(folder)]
    assert profile.get("languages") == ["English", "Hindi"]
    assert profile.skipped == ("company",)
    row = store.search_memories("profile.call_me")[0]
    assert row["source"] == "onboarding" and row["memory_type"] == "user"
    assert reopened.policy.level() == LEVEL
    assert reopened.policy.state_of("workspace_access") == "AUTO"
    assert reopened.policy.state_of("spend_money") == "CONFIRM", \
        "the interview must not delegate what FR-POL-003 reserves"
    assert reopened.suggested_workers(), "the answers justify at least one worker"

    # ---- 6: the first useful task, on what the interview learned ---------
    target = reopened.profile().get("folders")[0]
    report = tmp_path / "workspace" / "inventory.md"
    report.parent.mkdir()

    async def local_inventory(_capability, args):
        assert args["folder"] == str(folder), "the task must run on the folder the user named"
        names = sorted(p.name for p in folder.iterdir())
        report.write_text("\n".join(f"- {n}" for n in names) + "\n", encoding="utf-8")
        return {"ok": True, "files": len(names), "report": str(report),
                "verification": {"method": "inventory_report_written"}}

    run = compile_objective(
        store, request="List what is in my shop folder",
        tasks=[{"capability": "local_inventory", "arguments": {"folder": target}}],
        manifest=[{"id": "local_inventory", "description": "list a folder"}],
        objective_summary="first useful task", provenance=PERSON, attended=True)
    executor = ContinuousTaskExecutor(store, local_inventory, executor_id="onb-1")
    await executor.start(run["run_id"])
    final = await _terminal(store, run["run_id"])

    task = store.objective_tasks(run["run_id"])[0]
    assert task["status"] == TaskStatus.SUCCEEDED, task
    assert final["status"] == "COMPLETED", final
    assert task["result"]["files"] == 3
    ledger = json.loads(final["evidence"])
    assert ledger and ledger[0]["passed"] is True, ledger
    assert ledger[0]["method"] == "inventory_report_written"

    # ---- 7: the task is verified by a command it cannot influence --------
    names = sorted(p.name for p in folder.iterdir())
    check = ("import sys; text = open('inventory.md', encoding='utf-8').read(); "
             "sys.exit(0 if all(n in text for n in %r) else 1)" % (names,))
    verdict, code, _detail = E.verify(
        str(report.parent), E.Verifier(command=(sys.executable, "-c", check),
                                       proves="the report names every file"))
    assert (verdict, code) == (E.PASSED, 0)

    # negative control: a report that misses a file must not pass
    report.write_text("- stock.xlsx\n", encoding="utf-8")
    verdict, code, _detail = E.verify(
        str(report.parent), E.Verifier(command=(sys.executable, "-c", check)))
    assert verdict == E.FAILED and code == 1

    store.close()


def test_finish_without_a_level_keeps_the_behaviour_the_product_always_had(fresh, tmp_path):
    """A user who answers the essentials and chooses nothing is not granted
    autonomy on a guess: `finish` falls back to Ask Before Acting."""
    interview, paths = fresh
    for name, value in _essential_answers(tmp_path).items():
        interview.record({name: value})
    interview.skip("company")
    assert interview.finish(paths["home"])["level"] == "Ask Before Acting"
    assert interview.policy.level() == "Ask Before Acting"


def test_an_unreadable_marker_is_not_onboarded(tmp_path):
    marker = O.state_root_marker(tmp_path)
    marker.parent.mkdir(parents=True)
    marker.write_text("{not json", encoding="utf-8")
    assert not O.is_onboarded(tmp_path)


def test_the_console_finishes_setup_and_refuses_to_finish_early(tmp_path, monkeypatch, capsys):
    """The console is the production path to `finish`, so it is driven like one."""
    from friday import config

    monkeypatch.setattr(config, "STATE_ROOT", tmp_path / "home")
    store = Store(tmp_path / "console.sqlite3")
    store.ensure_default_workspace()
    interview = O.Onboarding(store, policy=UserPolicy(tmp_path / "policy.sqlite3"))
    monkeypatch.setattr(O, "Onboarding", lambda workspace_id=None: interview)
    monkeypatch.delenv(W.ENV_VAR, raising=False)
    W.reset_override()

    assert O.console(["finish"]) == 1
    assert "setup is not finished" in capsys.readouterr().out
    assert not O.is_onboarded(tmp_path / "home")

    for name, value in _essential_answers(tmp_path).items():
        interview.record({name: value})
    interview.skip("company")
    assert O.console(["finish", "--level", LEVEL]) == 0
    assert f"autonomy level: {LEVEL}" in capsys.readouterr().out
    assert O.is_onboarded(tmp_path / "home")
    store.close()
    W.reset_override()


# --- the first useful task comes from a real capability, not a demo fake -----
#
# The golden journey above drives a test-supplied capability so it can assert
# the objective engine's wiring in isolation. These prove the complementary
# half the acceptance needs: the first task is derived from the profile and
# runs through the live `files_list` capability - the real toolset, its real
# policy gate and fsjail - with an independent check it cannot influence.

def test_no_first_task_until_a_folder_is_named(fresh):
    interview, _paths = fresh
    assert interview.first_task_plan() is None
    interview.record({"call_me": "Asha"})
    assert interview.first_task_plan() is None, \
        "a first task with nothing to point at would be a guess"


def test_the_first_useful_task_runs_through_a_real_capability(fresh, tmp_path):
    from friday import capabilities
    from friday import contracts as c
    from friday.fsjail import FileJail
    from friday.policy import FULL, PolicyEngine
    from friday.toolsets import files as F

    interview, _paths = fresh
    folder = tmp_path / "shop"
    folder.mkdir()
    for name in ("orders.csv", "returns.docx", "stock.xlsx"):
        (folder / name).write_text(name, encoding="utf-8")
    for name, value in _essential_answers(folder).items():
        interview.record({name: value})
    interview.skip("company")

    plan = interview.first_task_plan()
    assert plan["capability"] == "files_list"
    assert plan["arguments"]["path"] == str(folder)
    assert capabilities.by_id(plan["capability"]) is not None, \
        "the first task must name a live capability, not one invented for the test"
    assert plan["manifest"]["description"] == capabilities.by_id("files_list").description

    # run the plan through the real toolset, with the real gate and fsjail
    F.reset_jail(FileJail(roots=(folder,)))
    try:
        result = F.files_list(c.Run.create("first task", capability="files"),
                              plan["arguments"]["path"], engine=PolicyEngine(autonomy=FULL))
    finally:
        F.reset_jail(None)
    assert result.status == c.SUCCEEDED, result
    assert result.output["count"] == 3
    names = sorted(entry["name"] for entry in result.output["entries"])
    assert names == ["orders.csv", "returns.docx", "stock.xlsx"]
    assert result.verification.method == "dir_listing"

    # verified by a command the task cannot influence
    report = tmp_path / "out" / "inventory.md"
    report.parent.mkdir()
    report.write_text("\n".join(f"- {n}" for n in names) + "\n", encoding="utf-8")
    check = ("import sys; t = open('inventory.md', encoding='utf-8').read(); "
             "sys.exit(0 if all(n in t for n in %r) else 1)" % (names,))
    verdict, code, _detail = E.verify(
        str(report.parent), E.Verifier(command=(sys.executable, "-c", check),
                                       proves="the report names every file"))
    assert (verdict, code) == (E.PASSED, 0)


def test_the_console_proposes_the_first_task(tmp_path, monkeypatch, capsys):
    store = Store(tmp_path / "console.sqlite3")
    store.ensure_default_workspace()
    interview = O.Onboarding(store, policy=UserPolicy(tmp_path / "policy.sqlite3"))
    monkeypatch.setattr(O, "Onboarding", lambda workspace_id=None: interview)
    monkeypatch.delenv(W.ENV_VAR, raising=False)
    W.reset_override()

    assert O.console(["task"]) == 0
    assert "no first task yet" in capsys.readouterr().out
    interview.record({"folders": [str(tmp_path / "shop")]})
    assert O.console(["task"]) == 0
    printed = capsys.readouterr().out
    assert "files_list" in printed and str(tmp_path / "shop") in printed
    store.close()
    W.reset_override()
