"""
HARN-001: one source harness, two tools.

FRIDAY's engineering harness (rules, roles, skills, hooks) lives once, under
`.claude/`. `friday.harness_sync` renders it into the Claude Code layout and
the Hermes profile layout. These tests build a small source tree in a tmp dir,
generate into tmp dirs, and assert that a single source edit reaches both
outputs. Nothing here touches the real Hermes install, ~/.claude, or the
repo's live .claude/.
"""
import ast
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from friday import harness_sync as H

SKILL_DESC = ("Helps users discover and install agent skills when they ask "
              "how do I do X. This skill should be used when browsing.")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _front(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "repo"
    c = root / ".claude"
    _write(c / "rules" / "00-team-rules.md",
           "# Team rules (compact)\n- Plan first for work crossing 2+ files.\n")
    _write(c / "rules" / "01-plan-first.md",
           '---\ndescription: "Enforces plan-first development before any code."\n'
           'paths:\n  - "friday/**"\nalwaysApply: true\n---\n'
           "# Rule 1: Plan-First\nSave a plan before implementing.\n")
    _write(c / "rules" / "03-definition-of-done.md",
           '---\ndescription: "Done means a failing test now passes."\n'
           "alwaysApply: false\n---\n# Rule 3\nTest first, then verify end to end.\n")
    _write(c / "agents" / "friday-qa-engineer.md",
           "---\nname: friday-qa-engineer\n"
           'description: "Writes the failing test first and runs the gate."\n'
           "model: sonnet\ntools:\n  - Read\n  - Grep\n  - Bash\n  - Edit\n---\n"
           "# QA\nWrite the test, watch it fail.\n")
    _write(c / "skills" / "find-skills" / "SKILL.md",
           f"---\nname: find-skills\ndescription: {SKILL_DESC}\n---\n# Find Skills\nBody.\n")
    _write(c / "skills" / "find-skills" / "reference.md", "ref\n")
    _write(c / "hooks" / "session-init.sh", "#!/usr/bin/env bash\necho hi\n")
    _write(c / "settings.json", json.dumps({
        "permissions": {"ask": ["Bash(git commit *)"]},
        "hooks": {"SessionStart": [
            {"type": "command", "command": "bash .claude/hooks/session-init.sh"}]},
    }))
    return root


@pytest.fixture(autouse=True)
def hook_shell(tmp_path, monkeypatch):
    """Pin the hook shell to a stand-in file so layout tests do not depend on what `bash` is on PATH."""
    fake = tmp_path / "fake shell" / "bash"
    _write(fake, "")
    monkeypatch.setenv(H.HOOK_BASH_ENV, str(fake))
    return fake.as_posix()


@pytest.fixture
def outs(tmp_path):
    return tmp_path / "out_claude", tmp_path / "out_hermes"


def _gen(source, outs, **kw):
    return H.render(source, outs[0], outs[1], **kw)


def test_claude_layout_carries_rules_roles_skills_and_hooks(source, outs):
    _gen(source, outs)
    for rel in ("rules/00-team-rules.md", "rules/01-plan-first.md",
                "rules/03-definition-of-done.md", "agents/friday-qa-engineer.md",
                "skills/find-skills/SKILL.md", "skills/find-skills/reference.md",
                "hooks/session-init.sh"):
        assert (outs[0] / rel).read_bytes() == (source / ".claude" / rel).read_bytes()


def test_hermes_layout_has_soul_skills_roles_and_mcp_fragment(source, outs, hook_shell):
    _gen(source, outs)
    hermes = outs[1]
    soul = (hermes / "SOUL.md").read_text(encoding="utf-8")
    assert "Plan first for work crossing 2+ files." in soul      # compact rules
    assert "Save a plan before implementing." in soul             # alwaysApply rule
    assert "Test first, then verify" not in soul                  # on-demand rule stays a skill
    assert "friday-role-qa-engineer" in soul and "find-skills" in soul

    base = hermes / "skills" / "friday-harness"
    rule = (base / "friday-rule-03-definition-of-done" / "SKILL.md").read_text(encoding="utf-8")
    assert "Test first, then verify end to end." in rule
    role = (base / "friday-role-qa-engineer" / "SKILL.md").read_text(encoding="utf-8")
    assert "Write the test, watch it fail." in role
    assert "read_file" in role and "terminal" in role and "patch" in role  # Hermes tool names
    assert (base / "find-skills" / "reference.md").read_text(encoding="utf-8") == "ref\n"

    skill_mds = sorted(base.glob("*/SKILL.md"))
    assert len(skill_mds) == 5          # 3 rules, 1 role, 1 skill
    for skill_md in skill_mds:
        meta = _front(skill_md)
        assert meta["name"] == skill_md.parent.name
        assert len(meta["description"]) <= 60 and meta["description"].endswith(".")

    frag = yaml.safe_load((hermes / "harness.config.yaml").read_text(encoding="utf-8"))
    assert frag["mcp_servers"]["friday"]["url"] == H.DEFAULT_MCP_URL
    assert (hermes / "skills").as_posix() in frag["skills"]["external_dirs"]
    assert frag["mcp_servers"]["friday"]["transport"] == "sse"    # Hermes picks SSE only from this key
    argv = shlex.split(frag["hooks"]["on_session_start"][0]["command"])
    assert argv[0] == hook_shell and argv[1].endswith("agent-hooks/.context-hook.sh")
    assert argv[2].endswith("agent-hooks/session-init.sh")
    assert (hermes / "agent-hooks" / ".context-hook.sh").is_file()
    assert (hermes / "agent-hooks" / "session-init.sh").read_bytes() == \
        b"#!/usr/bin/env bash\necho hi\n"


def test_mcp_url_is_a_parameter(source, outs):
    _gen(source, outs, mcp_url="http://127.0.0.1:9999/sse")
    frag = yaml.safe_load((outs[1] / "harness.config.yaml").read_text(encoding="utf-8"))
    assert frag["mcp_servers"]["friday"]["url"] == "http://127.0.0.1:9999/sse"
    assert frag["mcp_servers"]["friday"]["transport"] == "sse"
    _gen(source, outs, mcp_url="http://127.0.0.1:9999/mcp")        # not an SSE path: left to Hermes
    frag = yaml.safe_load((outs[1] / "harness.config.yaml").read_text(encoding="utf-8"))
    assert "transport" not in frag["mcp_servers"]["friday"]
    _gen(source, outs, mcp_url="http://127.0.0.1:9999/mcp", mcp_transport="sse")
    frag = yaml.safe_load((outs[1] / "harness.config.yaml").read_text(encoding="utf-8"))
    assert frag["mcp_servers"]["friday"]["transport"] == "sse"
    with pytest.raises(ValueError):
        _gen(source, outs, mcp_transport="carrier-pigeon")


def test_one_rule_edit_reaches_both_tools_from_one_regeneration(source, outs):
    _gen(source, outs)
    rule = source / ".claude" / "rules" / "03-definition-of-done.md"
    rule.write_bytes(rule.read_bytes().replace(b"Test first", b"Failing test FIRST"))
    assert H.check(source, outs[0], outs[1])      # drift is visible before regenerating
    _gen(source, outs)
    assert "Failing test FIRST" in (outs[0] / "rules" / "03-definition-of-done.md").read_text(encoding="utf-8")
    hermes_rule = outs[1] / "skills" / "friday-harness" / "friday-rule-03-definition-of-done" / "SKILL.md"
    assert "Failing test FIRST" in hermes_rule.read_text(encoding="utf-8")
    assert H.check(source, outs[0], outs[1]) == []


def test_one_alwaysapply_rule_edit_changes_the_hermes_soul(source, outs):
    _gen(source, outs)
    rule = source / ".claude" / "rules" / "01-plan-first.md"
    rule.write_bytes(rule.read_bytes().replace(b"Save a plan", b"Write the plan down"))
    _gen(source, outs)
    assert "Write the plan down" in (outs[1] / "SOUL.md").read_text(encoding="utf-8")
    assert "Write the plan down" in (outs[0] / "rules" / "01-plan-first.md").read_text(encoding="utf-8")


def test_a_new_role_appears_in_both_and_a_removed_one_leaves_both(source, outs):
    _gen(source, outs)
    new = source / ".claude" / "agents" / "friday-orch.md"
    _write(new, '---\nname: friday-orch\ndescription: "Routes work."\ntools:\n  - Read\n---\n# Orch\n')
    _gen(source, outs)
    assert (outs[0] / "agents" / "friday-orch.md").exists()
    assert (outs[1] / "skills" / "friday-harness" / "friday-role-orch" / "SKILL.md").exists()
    new.unlink()
    result = _gen(source, outs)
    assert not (outs[0] / "agents" / "friday-orch.md").exists()
    assert not (outs[1] / "skills" / "friday-harness" / "friday-role-orch").exists()
    assert result.removed


def test_regeneration_is_idempotent_and_check_is_clean(source, outs):
    _gen(source, outs)
    assert H.check(source, outs[0], outs[1]) == []
    second = _gen(source, outs)
    assert second.written == [] and second.removed == []


def test_hand_edit_of_a_generated_file_is_drift(source, outs):
    _gen(source, outs)
    target = outs[0] / "rules" / "00-team-rules.md"
    target.write_bytes(target.read_bytes() + b"- sneaked in\n")
    assert H.check(source, outs[0], outs[1]) == ["claude:rules/00-team-rules.md"]


def test_existing_soul_persona_is_kept_and_block_replaced_not_duplicated(source, outs):
    outs[1].mkdir(parents=True)
    (outs[1] / "SOUL.md").write_text("You are my persona.\n", encoding="utf-8")
    _gen(source, outs)
    _gen(source, outs)
    soul = (outs[1] / "SOUL.md").read_text(encoding="utf-8")
    assert soul.startswith("You are my persona.")
    assert soul.count(H.BEGIN_MARK) == 1 and soul.count(H.END_MARK) == 1
    assert H.check(source, outs[0], outs[1]) == []


def _hermes_install(tmp_path):
    """<hermes home>/hermes-agent (the code) next to profiles/, config.yaml, SOUL.md."""
    home = tmp_path / "hermeshome"
    install = home / "hermes-agent"
    (home / "profiles" / "friday").mkdir(parents=True)
    install.mkdir()
    return home, install


def test_refuses_the_live_claude_dir_the_home_claude_and_the_hermes_install(source, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    _, install = _hermes_install(tmp_path)
    hermes_out = tmp_path / "ok_hermes"
    for bad in (source / ".claude", home / ".claude", install / "skills"):
        with pytest.raises(H.RefusedTarget):
            H.render(source, bad, hermes_out, hermes_root=install)
    with pytest.raises(H.RefusedTarget):
        H.render(source, tmp_path / "ok_claude", install, hermes_root=install)
    assert not (source / ".claude" / "rules" / ".harness-sync.json").exists()
    # explicit opt-in lets the owner deploy to a live location
    H.render(source, home / ".claude", hermes_out, hermes_root=install, allow_live=True)
    assert (home / ".claude" / "rules" / "00-team-rules.md").exists()


def test_refuses_the_live_hermes_profile_and_home_not_just_the_install(source, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "userhome"))
    hermes_home, install = _hermes_install(tmp_path)
    ok_claude = tmp_path / "ok_claude"
    for bad in (hermes_home / "profiles" / "friday", hermes_home / "profiles" / "new",
                hermes_home, tmp_path / "userhome" / ".hermes" / "profiles" / "friday"):
        with pytest.raises(H.RefusedTarget):
            H.render(source, ok_claude, bad, hermes_root=install)
    extra = tmp_path / "elsewhere" / "profile"
    with pytest.raises(H.RefusedTarget):          # FRIDAY_HERMES_PROFILE_HOME style override
        H.render(source, ok_claude, extra, hermes_root=install, protect=[extra])
    assert not (hermes_home / "profiles" / "friday" / "SOUL.md").exists()
    H.render(source, ok_claude, hermes_home / "profiles" / "friday", hermes_root=install,
             allow_live=True)
    assert (hermes_home / "profiles" / "friday" / "SOUL.md").is_file()


def test_a_link_into_a_live_location_is_still_refused(source, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "userhome"))
    hermes_home, install = _hermes_install(tmp_path)
    link = tmp_path / "innocent"
    target = hermes_home / "profiles" / "friday"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("cannot create a symlink here")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, timeout=30)       # a junction needs no privilege
        if made.returncode != 0:
            pytest.skip("cannot create a symlink or junction here")
    with pytest.raises(H.RefusedTarget):
        H.render(source, tmp_path / "ok_claude", link, hermes_root=install)


def test_a_source_without_a_harness_is_an_error_not_an_empty_success(tmp_path, outs):
    (tmp_path / "empty" / ".claude").mkdir(parents=True)
    with pytest.raises(H.HarnessSourceError):
        H.render(tmp_path / "empty", outs[0], outs[1])


def test_the_real_repo_harness_renders_into_both_layouts(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    src = repo / ".claude"
    claude, hermes = tmp_path / "c", tmp_path / "h"
    H.render(repo, claude, hermes)
    base = hermes / "skills" / "friday-harness"
    for rule in (src / "rules").glob("*.md"):
        assert (claude / "rules" / rule.name).read_bytes() == rule.read_bytes()
        assert (base / f"friday-rule-{rule.stem}" / "SKILL.md").exists()
    for agent in (src / "agents").glob("*.md"):
        assert (claude / "agents" / agent.name).exists()
        assert (base / ("friday-role-" + agent.stem.removeprefix("friday-")) / "SKILL.md").exists()
    for skill in (src / "skills").glob("*/SKILL.md"):
        assert (claude / "skills" / skill.parent.name / "SKILL.md").exists()
        assert (base / skill.parent.name / "SKILL.md").exists()
    assert H.check(repo, claude, hermes) == []


def test_cli_writes_checks_and_refuses(source, outs, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "harness_sync_cli", Path(__file__).resolve().parents[1] / "scripts" / "harness_sync.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    base = ["--source", str(source), "--claude-out", str(outs[0]), "--hermes-out", str(outs[1])]
    assert cli.main(base) == 0
    assert cli.main(base + ["--check"]) == 0
    (outs[0] / "rules" / "00-team-rules.md").write_bytes(b"drifted\n")
    assert cli.main(base + ["--check"]) == 1
    assert "DRIFT claude:rules/00-team-rules.md" in capsys.readouterr().out
    live = ["--source", str(source), "--claude-out", str(source / ".claude"),
            "--hermes-out", str(outs[1])]
    assert cli.main(live) == 2


def test_unbalanced_soul_markers_are_an_error_not_a_second_block(source, outs):
    outs[1].mkdir(parents=True)
    for text in (f"persona\n{H.BEGIN_MARK}\nhalf a block\n",
                 f"persona\n{H.END_MARK}\nthen\n{H.BEGIN_MARK}\n"):
        (outs[1] / "SOUL.md").write_text(text, encoding="utf-8")
        with pytest.raises(H.HarnessSourceError):
            _gen(source, outs)
        assert (outs[1] / "SOUL.md").read_text(encoding="utf-8") == text   # untouched


def test_source_digest_does_not_depend_on_cwd_or_checkout_location(source, tmp_path, monkeypatch):
    first = H.source_digest(source)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert H.source_digest(source) == first
    shutil.copytree(source, tmp_path / "copy")
    assert H.source_digest(tmp_path / "copy") == first
    rule = source / ".claude" / "rules" / "03-definition-of-done.md"
    rule.write_bytes(rule.read_bytes() + b"more\n")
    assert H.source_digest(source) != first


def test_hook_paths_with_spaces_survive_the_hermes_command_split(source, tmp_path):
    spaced = tmp_path / "out dir"
    H.render(source, tmp_path / "c", spaced / "h")
    frag = yaml.safe_load((spaced / "h" / "harness.config.yaml").read_text(encoding="utf-8"))
    argv = shlex.split(frag["hooks"]["on_session_start"][0]["command"])
    assert len(argv) == 3 and all(Path(a).is_file() for a in argv[1:])


def _hermes_valid_hooks():
    """VALID_HOOKS read (not imported) from the located Hermes install; None if absent."""
    from friday import hermes_bridge
    found = hermes_bridge.locate()
    plugins = Path(found["root"]) / "hermes_cli" / "plugins.py" if found else None
    if not plugins or not plugins.is_file():
        return None
    for node in ast.parse(plugins.read_text(encoding="utf-8")).body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if target is not None and getattr(target, "id", "") == "VALID_HOOKS":
            return set(ast.literal_eval(node.value))
    return None


def test_generated_hooks_use_event_names_hermes_accepts(source, outs):
    valid = _hermes_valid_hooks()
    if valid is None:
        pytest.skip("no Hermes install to read VALID_HOOKS from")
    _gen(source, outs)
    frag = yaml.safe_load((outs[1] / "harness.config.yaml").read_text(encoding="utf-8"))
    assert frag["hooks"] and set(frag["hooks"]) <= valid
    for entries in frag["hooks"].values():
        for entry in entries:
            assert isinstance(entry["command"], str) and entry["command"].strip()


def _real_bash(monkeypatch):
    """The bash Hermes would be told to use (same resolver as the generator), or skip."""
    monkeypatch.delenv(H.HOOK_BASH_ENV, raising=False)
    bash, why = H.resolve_hook_bash()
    if bash is None:
        pytest.skip(f"needs bash: {why}")
    return bash


def _run_wrapper(bash, wrapper, script):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    return subprocess.run([bash, Path(wrapper).as_posix(), Path(script).as_posix()],
                          capture_output=True, text=True, timeout=30, env=env)


def test_session_hook_output_reaches_hermes_as_json_context(source, outs, monkeypatch):
    bash = _real_bash(monkeypatch)
    script = source / ".claude" / "hooks" / "tricky.sh"
    _write(script, "#!/usr/bin/env bash\n"
                   "echo 'say \"x\" back\\slash'\n"
                   "echo 'line two'\n"
                   'echo "dir=$CLAUDE_PROJECT_DIR"\n')
    _gen(source, outs)
    wrapper = outs[1] / "agent-hooks" / ".context-hook.sh"
    run = _run_wrapper(bash, wrapper, outs[1] / "agent-hooks" / "tricky.sh")
    data = json.loads(run.stdout)                  # valid JSON despite quotes and backslashes
    assert set(data) == {"context"}
    lines = data["context"].splitlines()
    assert lines[0] == 'say "x" back\\slash' and lines[1] == "line two"
    assert lines[2].startswith("dir=") and lines[2].endswith("/repo")   # defaults to the source repo


def test_a_failing_session_script_is_reported_not_swallowed(source, outs, monkeypatch):
    bash = _real_bash(monkeypatch)
    _write(source / ".claude" / "hooks" / "broken.sh",
           "#!/usr/bin/env bash\necho partial\necho 'boom: no such thing' >&2\nexit 3\n")
    _gen(source, outs)
    run = _run_wrapper(bash, outs[1] / "agent-hooks" / ".context-hook.sh",
                       outs[1] / "agent-hooks" / "broken.sh")
    # Hermes shell_hooks: any non-zero exit is logged with stderr; exit 2 would BLOCK a
    # blocking event, so the wrapper must fail with 1, and must not print a context.
    assert run.returncode == 1
    assert run.stdout.strip() == ""
    assert "boom: no such thing" in run.stderr and "exit 3" in run.stderr
    missing = _run_wrapper(bash, outs[1] / "agent-hooks" / ".context-hook.sh", outs[1] / "nope.sh")
    assert missing.returncode == 1 and "failed" in missing.stderr


def _fake_windows(tmp_path, monkeypatch, *, git_bash: str | None, path_bash: str | None):
    """A Windows-shaped world: git under <Git>/cmd, an optional Git bash, an optional PATH bash."""
    monkeypatch.delenv(H.HOOK_BASH_ENV, raising=False)
    monkeypatch.setattr(H, "_on_windows", lambda: True)
    git = tmp_path / "Git" / "cmd" / "git.exe"
    _write(git, "")
    if git_bash:
        _write(tmp_path / "Git" / git_bash, "")
    wsl = tmp_path / "Users" / "x" / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "bash.exe"
    _write(wsl, "")
    found = {"git": str(git), "bash": str(tmp_path / path_bash) if path_bash else None}
    monkeypatch.setattr(H.shutil, "which", lambda name, *a, **k: found.get(name))
    return wsl


@pytest.mark.parametrize("rel", ["bin/bash.exe", "usr/bin/bash.exe"])
def test_windows_picks_git_for_windows_bash_never_the_wsl_launcher(tmp_path, monkeypatch, rel):
    wsl = _fake_windows(tmp_path, monkeypatch, git_bash=rel, path_bash=None)
    found = {"git": str(tmp_path / "Git" / "cmd" / "git.exe"), "bash": str(wsl)}   # WSL is first on PATH
    monkeypatch.setattr(H.shutil, "which", lambda name, *a, **k: found.get(name))
    bash, why = H.resolve_hook_bash()
    assert bash == (tmp_path / "Git" / rel).as_posix() and why == ""
    assert "windowsapps" not in bash.lower()


def test_an_explicit_override_wins_but_the_wsl_launcher_is_refused_even_then(tmp_path, monkeypatch):
    wsl = _fake_windows(tmp_path, monkeypatch, git_bash="bin/bash.exe", path_bash=None)
    mine = tmp_path / "msys" / "bash.exe"
    _write(mine, "")
    assert H.resolve_hook_bash(str(mine)) == (mine.as_posix(), "")
    monkeypatch.setenv(H.HOOK_BASH_ENV, str(mine))
    assert H.resolve_hook_bash()[0] == mine.as_posix()
    bash, why = H.resolve_hook_bash(str(wsl))
    assert bash is None and "WSL launcher" in why
    bash, why = H.resolve_hook_bash(str(tmp_path / "missing" / "bash.exe"))
    assert bash is None and "not an existing bash" in why


def test_no_suitable_bash_on_windows_emits_no_hook_and_says_why(source, outs, tmp_path, monkeypatch):
    wsl = _fake_windows(tmp_path, monkeypatch, git_bash=None, path_bash=None)
    found = {"git": str(tmp_path / "Git" / "cmd" / "git.exe"), "bash": str(wsl)}
    monkeypatch.setattr(H.shutil, "which", lambda name, *a, **k: found.get(name))
    result = _gen(source, outs)
    frag = yaml.safe_load((outs[1] / "harness.config.yaml").read_text(encoding="utf-8"))
    assert "hooks" not in frag
    assert not (outs[1] / "agent-hooks" / ".context-hook.sh").exists()
    assert len(result.unavailable) == 1
    assert "session-init.sh" in result.unavailable[0] and "WSL launcher" in result.unavailable[0]
    assert H.check(source, outs[0], outs[1]) == []         # the absence is stable, not drift
