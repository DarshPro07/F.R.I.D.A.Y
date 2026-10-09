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
