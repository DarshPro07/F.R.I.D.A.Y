"""
EXEC-SBX-001 slice 1: a scoped command runner on a Windows AppContainer, and
the escape suite it has to pass before commands are restored to scoped
delegations.

Every probe is a real Python, Node or PowerShell process (and their children)
started inside the sandbox. Each probe script first runs a CONTROL (read a file
in the read root, write a file in the write root) and prints the result in the
same output, so a DENIED further down is the boundary and not an interpreter
that failed to start. Everything lives under tmp_path; nothing outside it is
granted or touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from friday import appcontainer, scoped_runner
from friday.contracts import Envelope

HAS_OS_BOUNDARY = appcontainer.available()
needs_os_boundary = pytest.mark.skipif(
    not HAS_OS_BOUNDARY, reason="needs a Windows AppContainer (the OS boundary)")

WINDIR = os.environ.get("WINDIR", r"C:\Windows")
POWERSHELL = str(Path(WINDIR) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
NODE = shutil.which("node") or ""
IN_TEXT = "inside-the-read-root"
SECRET_TEXT = "outside-secret-5c1e"

#: The interpreter copy the container reads is built once per session: copying
#: the standard library is the slow part, and it is not what is under test.
RUNTIME: dict[str, Path] = {}


@pytest.fixture(scope="session", autouse=True)
def runtime_dir(tmp_path_factory):
    RUNTIME["dir"] = tmp_path_factory.mktemp("scoped-runtime") / "_runtime"
    return RUNTIME["dir"]


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.root_r = tmp_path / "root_r"
        self.root_w = tmp_path / "root_w"
        self.outside = tmp_path / "outside"
        for directory in (self.root_r, self.root_w, self.outside):
            directory.mkdir()
        (self.root_r / "in.txt").write_text(IN_TEXT, encoding="utf-8")
        self.secret = self.outside / "secret.txt"
        self.secret.write_text(SECRET_TEXT, encoding="utf-8")
        self.new_outside = self.outside / "new.txt"

    def envelope(self, **changes) -> Envelope:
        fields = dict(run_id="run-scope-1", cwd=str(self.root_w),
                      read_roots=(str(self.root_r),), write_roots=(str(self.root_w),))
        fields.update(changes)
        return Envelope(**fields)

    def runner(self, **changes) -> scoped_runner.ScopedRunner:
        return scoped_runner.ScopedRunner(self.envelope(**changes), runtime_dir=RUNTIME["dir"])


@pytest.fixture
def world(tmp_path):
    assert "'" not in str(tmp_path), "the probes quote paths with single quotes"
    return World(tmp_path)


def variants(target: Path, cwd: Path) -> dict[str, str]:
    """One file, spelled the ways a path check is usually fooled by."""
    absolute = str(target)
    drive, rest = target.drive, str(target)[len(target.drive):]
    letter = drive[0]
    return {
        "absolute": absolute,
        "traversal": os.path.relpath(absolute, cwd),
        "extended": "\\\\?\\" + absolute,
        "upper": absolute.upper(),
        "admin_share_localhost": f"\\\\localhost\\{letter}$" + rest,
        "admin_share_ip": f"\\\\127.0.0.1\\{letter}$" + rest,
    }


# --- the probe programs: (label, op, path) -> "label:OK" / "label:DENIED" ----

def python_code(items) -> str:
    return (
        "import sys\n"
        "def probe(label, op, path):\n"
        "    try:\n"
        "        if op == 'read':\n"
        "            open(path, 'rb').read()\n"
        "        else:\n"
        "            open(path, 'wb').write(b'x')\n"
        "        print(label + ':OK')\n"
        "    except Exception:\n"
        "        print(label + ':DENIED')\n"
        + "".join(f"probe({label!r}, {op!r}, {path!r})\n" for label, op, path in items))


def node_code(items) -> str:
    def quote(text: str) -> str:
        return "'" + text.replace("\\", "\\\\") + "'"
    return (
        "const fs = require('fs');\n"
        "function probe(label, op, path) {\n"
        "  try {\n"
        "    if (op === 'read') { fs.readFileSync(path); } else { fs.writeFileSync(path, 'x'); }\n"
        "    console.log(label + ':OK');\n"
        "  } catch (e) { console.log(label + ':DENIED'); }\n"
        "}\n"
        + "".join(f"probe({quote(label)}, {quote(op)}, {quote(path)});\n"
                  for label, op, path in items))


def powershell_code(items) -> str:
    return (
        "function probe($label, $op, $path) { try { "
        "if ($op -eq 'read') { [void][IO.File]::ReadAllBytes($path) } "
        "else { [IO.File]::WriteAllBytes($path, [byte[]](120)) }; "
        "Write-Output ($label + ':OK') } catch { Write-Output ($label + ':DENIED') } }; "
        + "; ".join(f"probe '{label}' '{op}' '{path}'" for label, op, path in items))


def python_vector(items):
    return ["python", "-c", python_code(items)]


def node_vector(items):
    return [NODE, "-e", node_code(items)]


def powershell_vector(items):
    return [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", powershell_code(items)]


INTERPRETERS = {
    "python": python_vector,
    "node": pytest.param(node_vector, marks=pytest.mark.skipif(
        not NODE, reason="node is not installed")),
    "powershell": powershell_vector,
}


def results(execution) -> dict[str, str]:
    found = {}
    for line in execution.stdout.splitlines():
        if ":" in line:
            label, _, verdict = line.strip().rpartition(":")
            found[label] = verdict
    return found


def probe_items(world: World) -> list[tuple[str, str, str]]:
    items = [("control_read", "read", str(world.root_r / "in.txt")),
             ("control_write", "write", str(world.root_w / "control.txt"))]
    for name, path in variants(world.secret, world.root_w).items():
        items.append((f"read_{name}", "read", path))
    for name, path in variants(world.new_outside, world.root_w).items():
        items.append((f"write_{name}", "write", path))
    items.append(("write_into_read_root", "write", str(world.root_r / "blocked.txt")))
    return items


@needs_os_boundary
@pytest.mark.parametrize("make_vector", INTERPRETERS.values(), ids=INTERPRETERS.keys())
def test_each_interpreter_reaches_its_roots_and_nothing_else(world, make_vector):
    items = probe_items(world)
    with world.runner() as runner:
        ran = runner.run(make_vector(items), timeout=90)
    found = results(ran)
    assert found.get("control_read") == "OK", (ran.exit_code, ran.stdout, ran.stderr)
    assert found.get("control_write") == "OK", (ran.exit_code, ran.stdout, ran.stderr)
    assert (world.root_w / "control.txt").is_file()
    for label, op, _ in items[2:]:
        assert found.get(label) == "DENIED", (label, ran.stdout, ran.stderr)
    assert world.secret.read_text(encoding="utf-8") == SECRET_TEXT
    assert not world.new_outside.exists()
    assert not (world.root_r / "blocked.txt").exists()
    assert sorted(p.name for p in world.outside.iterdir()) == ["secret.txt"]


@needs_os_boundary
@pytest.mark.parametrize("make_vector", INTERPRETERS.values(), ids=INTERPRETERS.keys())
def test_a_junction_inside_the_write_root_gives_no_reach(world, make_vector):
    import _winapi
    _winapi.CreateJunction(str(world.outside), str(world.root_w / "jn"))
    items = [("control_read", "read", str(world.root_r / "in.txt")),
             ("control_write", "write", str(world.root_w / "control.txt")),
             ("read_through_junction", "read", str(world.root_w / "jn" / "secret.txt")),
             ("write_through_junction", "write", str(world.root_w / "jn" / "new.txt"))]
    with world.runner() as runner:
        ran = runner.run(make_vector(items), timeout=90)
    found = results(ran)
    assert found.get("control_read") == "OK" and found.get("control_write") == "OK", (
        ran.stdout, ran.stderr)
    assert found.get("read_through_junction") == "DENIED", ran.stdout
    assert found.get("write_through_junction") == "DENIED", ran.stdout
    assert not world.new_outside.exists()
    assert world.secret.read_text(encoding="utf-8") == SECRET_TEXT


@needs_os_boundary
def test_a_symlink_to_an_outside_file_gives_no_read(world):
    link = world.root_w / "link.txt"
    try:
        os.symlink(world.secret, link)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"this host cannot create symlinks: {error}")
    items = [("control_read", "read", str(world.root_r / "in.txt")),
             ("read_through_symlink", "read", str(link))]
    with world.runner() as runner:
        ran = runner.run(python_vector(items), timeout=60)
    found = results(ran)
    assert found.get("control_read") == "OK", (ran.stdout, ran.stderr)
    assert found.get("read_through_symlink") == "DENIED", ran.stdout


# --- children get no more reach than their parent ---------------------------

def child_items(world: World):
    return [("control_read", "read", str(world.root_r / "in.txt")),
            ("child_read_outside", "read", str(world.secret)),
            ("child_write_outside", "write", str(world.new_outside))]


PYTHON_PARENT_PYTHON_CHILD = (
    "import subprocess, sys\n"
    "r = subprocess.run([sys.executable, '-c', sys.argv[1]], capture_output=True, text=True)\n"
    "print(r.stdout)\nprint(r.stderr)\n")
PYTHON_PARENT_POWERSHELL_CHILD = (
    "import subprocess, sys\n"
    "r = subprocess.run([sys.argv[2], '-NoProfile', '-NonInteractive', '-Command', sys.argv[1]],\n"
    "                   capture_output=True, text=True)\n"
    "print(r.stdout)\nprint(r.stderr)\n")
#: Measured on this host: inside the container node's child_process hangs in
#: uv_spawn whenever a child has a piped stdio (stdio 'ignore' works), so the
#: PowerShell child writes its verdicts to a file in the write root and the
#: node parent prints that file. That is a limit of node here, not a denial.
NODE_PARENT_POWERSHELL_CHILD = (
    "const cp = require('child_process'), fs = require('fs');\n"
    "const command = process.argv[1] + ' | Out-File -Encoding ascii -LiteralPath ' + process.argv[3];\n"
    "cp.spawnSync(process.argv[2], ['-NoProfile', '-NonInteractive', '-Command', command],\n"
    "             {stdio: 'ignore'});\n"
    "console.log(fs.readFileSync(process.argv[3].slice(1, -1), 'utf8'));\n")


@needs_os_boundary
@pytest.mark.parametrize("name", ["python_to_python", "python_to_powershell", "node_to_powershell"])
def test_child_processes_get_no_more_reach(world, name):
    items = child_items(world)
    if name == "python_to_python":
        vector = ["python", "-c", PYTHON_PARENT_PYTHON_CHILD, python_code(items)]
    elif name == "python_to_powershell":
        vector = ["python", "-c", PYTHON_PARENT_POWERSHELL_CHILD, powershell_code(items), POWERSHELL]
    else:
        if not NODE:
            pytest.skip("node is not installed")
        out = world.root_w / "child_out.txt"
        vector = [NODE, "-e", NODE_PARENT_POWERSHELL_CHILD,
                  "& { " + powershell_code(items) + " }", POWERSHELL, f"'{out}'"]
    with world.runner() as runner:
        ran = runner.run(vector, timeout=120)
    found = results(ran)
    assert found.get("control_read") == "OK", (ran.exit_code, ran.stdout, ran.stderr)
    assert found.get("child_read_outside") == "DENIED", (ran.stdout, ran.stderr)
    assert found.get("child_write_outside") == "DENIED", (ran.stdout, ran.stderr)
    assert not world.new_outside.exists()


# --- grants come off again ---------------------------------------------------

def acl(path: Path) -> str:
    done = subprocess.run(["icacls", str(path)], capture_output=True, text=True, timeout=60)
    return done.stdout + done.stderr


@needs_os_boundary
def test_every_grant_is_removed_when_the_context_exits(world):
    items = [("control_write", "write", str(world.root_w / "made" / "ignored.txt"))]
    (world.root_w / "made").mkdir()
    with world.runner() as runner:
        sid = runner.sid
        assert sid.startswith("S-1-15-2-")
        ran = runner.run(python_vector(items), timeout=60)
        assert results(ran).get("control_write") == "OK"
        inside = acl(world.root_w)
        assert sid in inside or "friday-scope-" in inside
    for path in (world.root_r, world.root_w, world.root_w / "made",
                 world.root_w / "made" / "ignored.txt", world.root_r / "in.txt",
                 world.outside, world.secret):
        text = acl(path)
        assert sid not in text and "friday-scope-" not in text, (path, text)


# --- what is refused before anything is granted -----------------------------

class NothingMayBeCreated:
    """Stands in for AppContainer: building one at all is the failure."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("a container was created for a refused envelope")


@pytest.fixture
def no_container(monkeypatch):
    monkeypatch.setattr(appcontainer, "AppContainer", NothingMayBeCreated)

    def refuse_runtime(*args, **kwargs):
        raise AssertionError("the runtime was provisioned for a refused envelope")
    monkeypatch.setattr(appcontainer, "provision_runtime", refuse_runtime)


def make_junction(target: Path, link: Path) -> None:
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
@pytest.mark.parametrize("case", ["drive_root", "home", "junction", "missing", "relative",
                                  "windir", "not_a_directory", "ancestor_of_home"])
def test_a_bad_write_root_is_refused_and_no_container_is_made(world, no_container, case, tmp_path):
    if case == "drive_root":
        bad = Path(tmp_path.anchor)
    elif case == "home":
        bad = Path.home()
    elif case == "ancestor_of_home":
        bad = Path.home().parent
    elif case == "junction":
        bad = tmp_path / "jroot"
        make_junction(world.root_w, bad)
    elif case == "missing":
        bad = tmp_path / "does-not-exist"
    elif case == "relative":
        bad = Path("root_w")
    elif case == "windir":
        bad = Path(WINDIR) / "Temp"
    else:
        bad = world.root_r / "in.txt"
    runner = world.runner(cwd=str(bad), write_roots=(str(bad),))
    with pytest.raises(scoped_runner.ScopedRunnerError):
        runner.__enter__()


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
def test_a_bad_read_root_is_refused_too(world, no_container):
    runner = world.runner(read_roots=(str(world.root_r), str(Path.home())))
    with pytest.raises(scoped_runner.ScopedRunnerError):
        runner.__enter__()


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
def test_an_envelope_with_no_write_root_is_refused(world, no_container):
    runner = world.runner(write_roots=())
    with pytest.raises(scoped_runner.ScopedRunnerError, match="write root"):
        runner.__enter__()


def test_without_an_os_boundary_the_runner_refuses_and_does_not_fall_back(
        world, no_container, monkeypatch):
    monkeypatch.setattr(appcontainer, "available", lambda: False)
    runner = world.runner()
    with pytest.raises(scoped_runner.ScopedRunnerError, match="AppContainer"):
        runner.__enter__()


def test_run_outside_the_context_is_refused(world):
    with pytest.raises(scoped_runner.ScopedRunnerError):
        world.runner().run(["python", "-c", "pass"], timeout=5)


# === slice 2: parity with the file jail, one profile per runner, a sweep ====

import json
import sys
import uuid

from friday import config, fsjail


def acl_tree(path: Path) -> str:
    done = subprocess.run(["icacls", str(path), "/T", "/C"], capture_output=True,
                          text=True, timeout=120)
    return done.stdout + done.stderr


class RecordingContainer(appcontainer.AppContainer):
    """The real container; it only remembers every SID it made."""
    made: list[str] = []

    def create(self) -> str:
        sid = super().create()
        RecordingContainer.made.append(sid)
        return sid


#: Protected by fsjail.DENY_PATTERNS wherever they sit under a root.
PROTECTED = (".env", ".git/config", ".ssh/id_ed25519", "cert.pem", "credentials.json")
KEEP = "keep-me"


def protected_world(world: World) -> list[Path]:
    paths = []
    for name in PROTECTED:
        path = world.root_w / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(KEEP, encoding="utf-8")
        paths.append(path)
    (world.root_w / "plain.txt").write_text(KEEP, encoding="utf-8")
    return paths


@needs_os_boundary
@pytest.mark.parametrize("make_vector", INTERPRETERS.values(), ids=INTERPRETERS.keys())
def test_protected_files_inside_a_granted_root_are_denied_and_the_rest_is_not(world, make_vector):
    paths = protected_world(world)
    items = [("control_read", "read", str(world.root_r / "in.txt")),
             ("control_write", "write", str(world.root_w / "control.txt")),
             ("plain_read", "read", str(world.root_w / "plain.txt")),
             ("plain_write", "write", str(world.root_w / "plain.txt"))]
    labels = []
    for name, path in zip(PROTECTED, paths):
        tag = name.replace("/", "_")
        items += [(f"read_{tag}", "read", str(path)), (f"write_{tag}", "write", str(path))]
        labels += [f"read_{tag}", f"write_{tag}"]
    with world.runner() as runner:
        sid = runner.sid
        ran = runner.run(make_vector(items), timeout=120)
    found = results(ran)
    assert found.get("control_read") == "OK", (ran.exit_code, ran.stdout, ran.stderr)
    assert found.get("control_write") == "OK", (ran.exit_code, ran.stdout, ran.stderr)
    assert found.get("plain_read") == "OK" and found.get("plain_write") == "OK", ran.stdout
    for label in labels:
        assert found.get(label) == "DENIED", (label, ran.stdout, ran.stderr)
    for path in paths:
        assert path.read_text(encoding="utf-8") == KEEP, path
    for path in paths + [world.root_w / ".git", world.root_w / ".ssh", world.root_w]:
        text = acl(path)
        assert sid not in text and "friday-scope-" not in text, (path, text)


# --- roots that would hand over FRIDAY's own state --------------------------

@pytest.fixture
def state_root(tmp_path, monkeypatch):
    """A stand-in state root: tmp/state/data with its work areas."""
    data = tmp_path / "state" / "data"
    workspace = data / "workspace"
    for directory in (workspace, data / "logs"):
        directory.mkdir(parents=True)
    monkeypatch.setattr(config, "STATE_ROOT", data.parent)
    monkeypatch.setattr(fsjail, "DATA_DIR", data)
    monkeypatch.setattr(fsjail, "WORK_AREAS", (workspace, data / "artifacts"))
    return data


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
@pytest.mark.parametrize("case", ["contains_data_dir", "data_dir_itself", "inside_data_dir"])
@pytest.mark.parametrize("kind", ["write", "read"])
def test_a_root_holding_or_inside_the_state_dir_is_refused_before_a_container(
        world, no_container, state_root, case, kind):
    bad = {"contains_data_dir": state_root.parent, "data_dir_itself": state_root,
           "inside_data_dir": state_root / "logs"}[case]
    if kind == "write":
        changes = {"cwd": str(bad), "write_roots": (str(bad),)}
    else:
        changes = {"read_roots": (str(world.root_r), str(bad))}
    with pytest.raises(scoped_runner.ScopedRunnerError, match="state"):
        world.runner(**changes).__enter__()


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
def test_a_root_inside_a_work_area_is_accepted(state_root):
    sub = state_root / "workspace" / "project"
    sub.mkdir()
    assert scoped_runner._checked_root(str(sub), "write") == sub.resolve()


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
def test_a_root_under_the_real_workspace_is_accepted_and_the_real_state_dir_is_not():
    sub = fsjail.DEFAULT_WORKSPACE / f"scoped-runner-test-{uuid.uuid4().hex[:8]}"
    sub.mkdir(parents=True)
    try:
        assert scoped_runner._checked_root(str(sub), "write") == sub.resolve()
    finally:
        shutil.rmtree(sub, ignore_errors=True)
    with pytest.raises(scoped_runner.ScopedRunnerError):
        scoped_runner._checked_root(str(fsjail.DATA_DIR), "write")


@pytest.fixture
def two_trees(tmp_path, monkeypatch):
    """FRIDAY's install tree and its state tree apart (FRIDAY_HOME elsewhere, or a
    frozen install keeping state in %LOCALAPPDATA%): a/install and b/home."""
    install, home = tmp_path / "a" / "install", tmp_path / "b" / "home"
    for directory in (install / "friday", home / "logs", home / "vault",
                      home / "data" / "workspace"):
        directory.mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_ROOT", install)
    monkeypatch.setattr(config, "STATE_ROOT", home)
    monkeypatch.setattr(fsjail, "DATA_DIR", home / "data")
    monkeypatch.setattr(fsjail, "WORK_AREAS", (home / "data" / "workspace",
                                               home / "data" / "artifacts"))
    return install, home


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
@pytest.mark.parametrize("case", [
    "install_itself", "inside_install", "contains_install",
    "state_itself", "state_logs", "state_vault", "contains_state"])
@pytest.mark.parametrize("kind", ["write", "read"])
def test_a_root_over_the_install_or_state_tree_is_refused_before_a_container(
        world, no_container, two_trees, case, kind):
    install, home = two_trees
    bad = {"install_itself": install, "inside_install": install / "friday",
           "contains_install": install.parent, "state_itself": home,
           "state_logs": home / "logs", "state_vault": home / "vault",
           "contains_state": home.parent}[case]
    if kind == "write":
        changes = {"cwd": str(bad), "write_roots": (str(bad),)}
    else:
        changes = {"read_roots": (str(world.root_r), str(bad))}
    with pytest.raises(scoped_runner.ScopedRunnerError, match="FRIDAY's"):
        world.runner(**changes).__enter__()


@pytest.mark.skipif(os.name != "nt", reason="Windows paths")
def test_a_root_inside_the_work_area_of_a_separate_state_tree_is_accepted(two_trees):
    install, home = two_trees
    sub = home / "data" / "workspace" / "project"
    sub.mkdir()
    assert scoped_runner._checked_root(str(sub), "write") == sub.resolve()
    with pytest.raises(scoped_runner.ScopedRunnerError):
        scoped_runner._checked_root(str(home / "data"), "write")


# --- a unique profile per runner ---------------------------------------------

@needs_os_boundary
def test_two_runners_with_one_run_id_get_their_own_profile_and_sid(world):
    items = [("control_read", "read", str(world.root_r / "in.txt")),
             ("control_write", "write", str(world.root_w / "control.txt"))]
    first, second = world.runner(), world.runner()
    with first, second:
        assert first.sid and second.sid and first.sid != second.sid
        assert first.profile != second.profile
        assert first.profile.startswith("friday-scope-")
    # Closing the first one must leave the second able to use its roots.
    first, second = world.runner(), world.runner()
    with second:
        with first:
            pass
        ran = second.run(python_vector(items), timeout=60)
    found = results(ran)
    assert found.get("control_read") == "OK" and found.get("control_write") == "OK", (
        ran.stdout, ran.stderr)


# --- the sweep of leftover grants --------------------------------------------

def dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def throwaway_lease(world: World, lease_dir: Path, pid: int):
    """Grants and a cut-off path made with the real helpers for a profile nothing
    holds open, and the lease a runner would have written for them."""
    name = "friday-scope-test-" + uuid.uuid4().hex[:8]
    container = appcontainer.AppContainer(name)
    sid = container.create()
    denied = world.root_w / ".env"
    denied.write_text(KEEP, encoding="utf-8")
    container.grant(world.root_r, "RX")
    container.grant(world.root_w, "M")
    assert scoped_runner._hold(lease_dir, denied, name, sid)
    lease_dir.mkdir(parents=True, exist_ok=True)
    lease = lease_dir / f"{name}.json"
    lease.write_text(json.dumps({
        "pid": pid, "sid": sid, "profile": name, "scratch": "",
        "granted": [str(world.root_r), str(world.root_w)], "denied": [str(denied)]}),
        encoding="utf-8")
    return container, name, sid, lease, denied


def profile_exists(name: str) -> bool:
    """Whether the AppContainer profile `name` exists. Creation reports "already
    exists" only for a profile that is still there. The probe leaves things as it
    found them: a profile it had to make it deletes again, one that was there stays."""
    probe = appcontainer.AppContainer(name)
    probe.create()
    exists = not probe._created
    if exists:
        probe.sid = ""  # close() then frees the SID without deleting the profile
    probe.close()
    return exists


@needs_os_boundary
def test_sweep_cleans_the_lease_of_a_dead_process_and_leaves_a_live_one(world, tmp_path):
    lease_dir = tmp_path / "leases"
    (tmp_path / "live").mkdir()
    live_world = World(tmp_path / "live")
    # No context manager and no close() before the assertions: nothing but the
    # sweep may delete the dead run's profile.
    container, name, sid, dead, denied = throwaway_lease(world, lease_dir, dead_pid())
    live_container, live_name, live_sid, live, live_denied = throwaway_lease(
        live_world, lease_dir, os.getpid())
    try:
        assert sid in acl(world.root_w) and sid not in acl(denied)  # cut off
        assert profile_exists(name), "the dead run's profile must exist before the sweep"
        live_before = live.read_text(encoding="utf-8")
        cleaned = scoped_runner.sweep(lease_dir)
        assert cleaned == [name]
        for path in (world.root_r, world.root_w, denied):
            assert sid not in acl(path), path
        assert "(I)" in acl(denied), "the cut-off file inherits again"
        assert not dead.exists()
        assert not profile_exists(name), "the sweep left the dead run's profile behind"
        assert live.read_text(encoding="utf-8") == live_before
        assert profile_exists(live_name), "the sweep removed a live run's profile"
        assert live_sid in acl(live_world.root_w) and live_sid not in acl(live_denied)
    finally:
        container.close()
        live_container.close()


@needs_os_boundary
def test_a_runner_sweeps_a_dead_runs_leftovers_when_it_opens(world):
    lease_dir = RUNTIME["dir"].parent / "scope-leases"
    container, name, sid, dead, denied = throwaway_lease(world, lease_dir, dead_pid())
    try:
        with world.runner():
            assert sid not in acl(world.root_w)
            assert not dead.exists()
    finally:
        container.close()


@needs_os_boundary
def test_an_open_runner_has_a_lease_and_a_clean_exit_removes_it(world):
    lease_dir = RUNTIME["dir"].parent / "scope-leases"
    with world.runner() as runner:
        lease = lease_dir / f"{runner.profile}.json"
        record = json.loads(lease.read_text(encoding="utf-8"))
        assert record["pid"] == os.getpid() and record["sid"] == runner.sid
        assert record["profile"] == runner.profile
        assert str(world.root_w.resolve()) in record["granted"]
    assert not lease.exists()


# --- the walk is capped ------------------------------------------------------

@needs_os_boundary
def test_a_root_larger_than_the_walk_cap_is_refused_and_nothing_is_left_granted(
        world, tmp_path, monkeypatch):
    for index in range(10):
        (world.root_w / f"f{index}.txt").write_text(KEEP, encoding="utf-8")
    monkeypatch.setattr(scoped_runner, "MAX_WALK", 5, raising=False)
    monkeypatch.setattr(appcontainer, "AppContainer", RecordingContainer)
    RecordingContainer.made.clear()
    with pytest.raises(scoped_runner.ScopedRunnerError, match="walk"):
        world.runner().__enter__()
    assert RecordingContainer.made, "the walk was refused before anything was granted"
    text = acl_tree(tmp_path)
    for sid in RecordingContainer.made:
        assert sid not in text
    assert "friday-scope-" not in text


# --- exact restore and the registry shared by all runners ---------------------

def snapshot(world: World) -> dict[Path, str]:
    """The full ACL listing of every protected path and of .git's contents."""
    paths = [world.root_w / name for name in PROTECTED] + [world.root_w / ".git"]
    return {path: acl_tree(path) for path in paths}


def explicit_user_read(path: Path) -> None:
    """An explicit ACE of the path's own (the Users group, read), before any run."""
    done = subprocess.run(["icacls", str(path), "/grant", "*S-1-5-32-545:(R)"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr


def guarded_items(world: World) -> list[tuple[str, str, str]]:
    return [("control_read", "read", str(world.root_r / "in.txt")),
            ("control_write", "write", str(world.root_w / "control.txt")),
            ("plain_read", "read", str(world.root_w / "plain.txt")),
            ("env_read", "read", str(world.root_w / ".env"))]


@needs_os_boundary
def test_a_run_leaves_every_protected_path_with_exactly_its_original_acl(world):
    protected_world(world)
    explicit_user_read(world.root_w / "credentials.json")
    before = snapshot(world)
    assert before[world.root_w / "credentials.json"].count(":(R)") == 1
    with world.runner() as runner:
        ran = runner.run(python_vector(guarded_items(world)), timeout=60)
        found = results(ran)
        assert found.get("control_read") == "OK" and found.get("control_write") == "OK", (
            ran.stdout, ran.stderr)
        assert found.get("env_read") == "DENIED", (ran.stdout, ran.stderr)
    after = snapshot(world)
    for path, text in before.items():
        assert after[path] == text, (path, text, after[path])
    assert after[world.root_w / "credentials.json"].count(":(R)") == 1
    registry = RUNTIME["dir"].parent / "scope-leases"
    assert json.loads((registry / "protected.json").read_text(encoding="utf-8")) == {}
    assert not (registry / "protected.lock").exists()


@needs_os_boundary
def test_overlapping_runners_keep_a_path_denied_until_the_last_one_leaves(world):
    protected_world(world)
    before = snapshot(world)
    first, second = world.runner(), world.runner()
    first.__enter__()
    try:
        with second:
            first.__exit__()
            ran = second.run(python_vector(guarded_items(world)), timeout=60)
            found = results(ran)
            assert found.get("control_read") == "OK" and found.get("control_write") == "OK", (
                ran.stdout, ran.stderr)
            assert found.get("plain_read") == "OK", (ran.stdout, ran.stderr)
            assert found.get("env_read") == "DENIED", (ran.stdout, ran.stderr)
    finally:
        first.__exit__()
    assert snapshot(world) == before


def hold_for(world: World):
    """A container nothing holds open, with the write root granted to it."""
    name = "friday-scope-test-" + uuid.uuid4().hex[:8]
    container = appcontainer.AppContainer(name)
    sid = container.create()
    container.grant(world.root_w, "M")
    return container, name, sid


def finish_lease(lease_dir: Path, name: str, sid: str, pid: int, targets: list[Path],
                 world: World) -> Path:
    for target in targets:
        assert scoped_runner._hold(lease_dir, target, name, sid)
    lease_dir.mkdir(parents=True, exist_ok=True)
    lease = lease_dir / f"{name}.json"
    lease.write_text(json.dumps({
        "pid": pid, "sid": sid, "profile": name, "scratch": "",
        "granted": [str(world.root_w)], "denied": [str(t) for t in targets]}),
        encoding="utf-8")
    return lease


@needs_os_boundary
def test_sweep_restores_exactly_when_the_dead_holder_was_the_last(world, tmp_path):
    protected_world(world)
    explicit_user_read(world.root_w / "credentials.json")
    before = snapshot(world)
    lease_dir = tmp_path / "leases"
    targets = [world.root_w / name for name in (".env", "credentials.json", ".git")]
    container, name, sid = hold_for(world)
    try:
        lease = finish_lease(lease_dir, name, sid, dead_pid(), targets, world)
        assert snapshot(world) != before, "the paths were cut off"
        assert scoped_runner.sweep(lease_dir) == [name]
        assert not lease.exists()
    finally:
        container.close()
    assert snapshot(world) == before


@needs_os_boundary
def test_sweep_keeps_a_path_cut_off_while_a_live_holder_remains(world, tmp_path):
    protected_world(world)
    before = snapshot(world)
    lease_dir = tmp_path / "leases"
    env = world.root_w / ".env"
    dead_c, dead_name, dead_sid = hold_for(world)
    live_c, live_name, live_sid = hold_for(world)
    try:
        dead = finish_lease(lease_dir, dead_name, dead_sid, dead_pid(), [env], world)
        live = finish_lease(lease_dir, live_name, live_sid, os.getpid(), [env], world)
        cut = acl(env)
        assert "(I)" not in cut
        assert scoped_runner.sweep(lease_dir) == [dead_name]
        assert not dead.exists() and live.exists()
        assert acl(env) == cut, "still cut off for the live holder"
        registry = json.loads((lease_dir / "protected.json").read_text(encoding="utf-8"))
        holders = [entry["holders"] for entry in registry.values()]
        assert holders == [[live_name]], registry
        # The live holder dies too: now the last one is gone and the path is restored.
        live.write_text(json.dumps({**json.loads(live.read_text(encoding="utf-8")),
                                    "pid": dead_pid()}), encoding="utf-8")
        assert scoped_runner.sweep(lease_dir) == [live_name]
        assert json.loads((lease_dir / "protected.json").read_text(encoding="utf-8")) == {}
    finally:
        dead_c.close()
        live_c.close()
    assert snapshot(world) == before


# --- the cut-off decision comes from the SDDL that was read ---------------------

SID_A = "S-1-15-2-1-2-3-4-5-6-7"
SID_B = "S-1-15-2-9-9-9-9-9-9-9"


@pytest.mark.parametrize("sddl, expected", [
    (f"D:AI(A;OICIID;0x1301bf;;;{SID_A})(A;ID;FA;;;SY)", True),
    (f"D:(A;OICI;0x1301bf;;;{SID_A})", False),              # explicit, not inherited
    ("D:PAI(A;;FA;;;SY)", False),                          # protected, no entry for the SID
    (f"D:AI(A;OICIID;0x1301bf;;;{SID_B})", False),          # somebody else's grant
    (f"D:PAI(A;OICI;0x1301bf;;;{SID_A})(A;OICIID;FA;;;{SID_B})", False),
])
def test_the_inherited_grant_is_read_from_the_sddl(sddl, expected):
    assert scoped_runner._inherited_grant(sddl, SID_A) is expected


@pytest.mark.parametrize("sddl", ["", "O:SYG:SY", "D:(A;ID)", "D:(A;OICIID;FA;;;)", "D:((A;;FA;;;SY)"])
def test_an_sddl_that_cannot_be_read_raises_instead_of_reading_as_no_grant(sddl):
    with pytest.raises(scoped_runner.ScopedRunnerError):
        scoped_runner._inherited_grant(sddl, SID_A)


@needs_os_boundary
def test_a_failing_icacls_listing_cannot_leave_a_protected_file_readable(world, monkeypatch):
    """The decision is made from the SDDL, so an `icacls <path>` listing that
    fails cannot make the runner treat a protected file as never granted."""
    protected_world(world)
    real_run = subprocess.run

    def listing_fails(command, *args, **kwargs):
        if isinstance(command, list) and command[:1] == ["icacls"] and len(command) == 2:
            return subprocess.CompletedProcess(command, 5, "", "Access is denied.")
        return real_run(command, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(scoped_runner.subprocess, "run", listing_fails)
        with world.runner() as runner:
            ran = runner.run(python_vector(guarded_items(world)), timeout=60)
    found = results(ran)
    assert found.get("control_read") == "OK" and found.get("control_write") == "OK", (
        ran.stdout, ran.stderr)
    assert found.get("env_read") == "DENIED", (ran.stdout, ran.stderr)


@needs_os_boundary
def test_an_unreadable_acl_refuses_the_run_and_leaves_nothing_behind(world, monkeypatch):
    paths = protected_world(world)
    target = world.root_w / ".env"
    before = {path: acl_tree(path) for path in paths}
    names = []

    class Spy(appcontainer.AppContainer):
        def __init__(self, name):
            super().__init__(name)
            names.append(name)

    real_read = scoped_runner._read_sddl

    def unreadable(path):
        if Path(path) == target:
            raise scoped_runner.ScopedRunnerError("could not read the ACL of .env (error 5)")
        return real_read(path)

    with monkeypatch.context() as patched:
        patched.setattr(appcontainer, "AppContainer", Spy)
        patched.setattr(scoped_runner, "_read_sddl", unreadable)
        with pytest.raises(scoped_runner.ScopedRunnerError, match="could not read the ACL"):
            world.runner().__enter__()
    assert len(names) == 1
    assert not profile_exists(names[0]), "a container was left behind"
    leases = RUNTIME["dir"].parent / "scope-leases"
    assert not (leases / f"{names[0]}.json").exists()
    registry = leases / "protected.json"
    assert not registry.exists() or json.loads(registry.read_text(encoding="utf-8")) == {}
    assert not (leases / "protected.lock").exists()
    assert {path: acl_tree(path) for path in paths} == before
    assert "friday-scope-" not in acl(world.root_w) and "friday-scope-" not in acl(target)


# --- the registry lock ---------------------------------------------------------

def test_a_stale_registry_lock_of_a_dead_writer_is_taken_over(tmp_path, monkeypatch):
    monkeypatch.setattr(scoped_runner, "LOCK_STALE", 0.0)
    lock = tmp_path / "protected.lock"
    lock.write_text(str(dead_pid()), encoding="utf-8")
    with scoped_runner._registry(tmp_path) as registry:
        registry.data["x"] = {"sddl": "D:", "holders": []}
    assert not lock.exists()
    assert "x" in json.loads((tmp_path / "protected.json").read_text(encoding="utf-8"))


def test_a_registry_lock_held_by_a_live_process_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(scoped_runner, "LOCK_STALE", 0.0)
    monkeypatch.setattr(scoped_runner, "LOCK_WAIT", 0.3)
    lock = tmp_path / "protected.lock"
    lock.write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(scoped_runner.ScopedRunnerError, match="lock"):
        with scoped_runner._registry(tmp_path):
            pass
    assert lock.exists(), "a live writer's lock is not taken"
