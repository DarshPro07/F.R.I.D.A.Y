"""
CAP-FORGE-003: a new repository is analysed in a quarantine that holds no
secret, no FRIDAY memory, no user file and no standing network.

Every probe here is a planted Python repository doing what a hostile one would:
reading an environment secret, reading a file outside its tree, opening a
socket. The host's "secret" is a sentinel set for the test and the "user file"
is a temp file, never the real home or `data/`.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from friday import appcontainer, execution, quarantine
from friday.config import DATA_DIR
from friday.store import Store

SECRET = "sk-host-secret-7f3a91c2d45e"
USER_FILE_TEXT = "user-private-data-91b"

#: The strong tier wherever the host has it; the tripwire tier is the explicit
#: fallback so the recording tests still run on a host with no AppContainer.
HAS_OS_BOUNDARY = appcontainer.available()
ISOLATION = "os" if HAS_OS_BOUNDARY else "tripwire"
needs_os_boundary = pytest.mark.skipif(
    not HAS_OS_BOUNDARY, reason="needs a Windows AppContainer (the OS boundary)")
#: The interpreter copy the container reads is built once per session: copying
#: the standard library is the slow part, and it is not what is under test.
RUNTIME: dict[str, Path] = {}


def boxed(**options) -> quarantine.Quarantine:
    options.setdefault("isolation", ISOLATION)
    options.setdefault("runtime_dir", RUNTIME.get("dir"))
    return quarantine.Quarantine(**options)


@pytest.fixture(scope="session", autouse=True)
def runtime_dir(tmp_path_factory):
    RUNTIME["dir"] = tmp_path_factory.mktemp("container-runtime")
    return RUNTIME["dir"]

PROFILE_KEYS = {"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
                "TMPDIR"}
ALLOWED_KEYS = PROFILE_KEYS | {
    "PATH", "SYSTEMROOT", "FRIDAY_QUARANTINE", "PYTHONDONTWRITEBYTECODE",
    "FRIDAY_EXECUTION_ENV", "FRIDAY_EXECUTION_EGRESS", "NO_PROXY", "HTTP_PROXY",
    "HTTPS_PROXY"}


#: Quarantines opened and icacls grants run, per test function (a parametrized
#: function sums its cases). Setting a quarantine up is the expensive part of a
#: probe, so `test_probe_cases_share_one_quarantine_each` bounds it.
TALLY: dict[str, dict[str, int]] = {}


@pytest.fixture(autouse=True)
def tally(request, monkeypatch):
    counts = TALLY.setdefault(request.node.originalname, {"quarantines": 0, "grants": 0})
    opened, granted = quarantine.Quarantine.__enter__, appcontainer.AppContainer.grant

    def counting_enter(self):
        counts["quarantines"] += 1
        return opened(self)

    def counting_grant(self, *args, **kwargs):
        counts["grants"] += 1
        return granted(self, *args, **kwargs)

    monkeypatch.setattr(quarantine.Quarantine, "__enter__", counting_enter)
    monkeypatch.setattr(appcontainer.AppContainer, "grant", counting_grant)


@pytest.fixture(autouse=True)
def host_secrets(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FRIDAY_PAIRING_TOKEN"):
        monkeypatch.setenv(name, SECRET)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ada.sqlite3")


@pytest.fixture
def base(tmp_path):
    """Laid out like the real one: `<data>/quarantine`, with the database a
    sibling of it. The database here is a stand-in file."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "ada.sqlite3").write_bytes(b"SQLite format 3\0LIVE-ROWS")
    return data / "quarantine"


def planted(tmp_path: Path, files: dict[str, str], name: str = "planted") -> Path:
    repo = tmp_path / name
    for relative, text in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return repo


def user_file(tmp_path: Path) -> Path:
    path = tmp_path / "user_home" / "private.txt"
    path.parent.mkdir()
    path.write_text(USER_FILE_TEXT, encoding="utf-8")
    return path


def stored(store: Store, run_id: str, event: str) -> list[dict]:
    return [e for e in store.objective_events(run_id) if e["event"] == event]


def output(analysis: quarantine.Analysis) -> str:
    return analysis.result.stdout + analysis.result.stderr


def link_dir(target: Path, link: Path) -> None:
    """A directory symlink, or on Windows without the privilege a junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError) as error:
        failure = error
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True, timeout=30)
        if made.returncode == 0:
            return
        failure = made.stdout + made.stderr
    pytest.skip(f"cannot create a directory link here: {failure}")


# --- secrets ----------------------------------------------------------------

ENV_SECRET_READS = (
    'os.environ["ANTHROPIC_API_KEY"]',
    'os.environ.get("ANTHROPIC_API_KEY")',
    'os.getenv("ANTHROPIC_API_KEY")',
    '"ANTHROPIC_API_KEY" in os.environ',
)


def test_planted_repo_reading_an_env_secret_is_denied_and_recorded(tmp_path, store, base):
    # One quarantine, one script and one child run per read: the setup is what
    # costs, and each read is still judged on its own run.
    repo = planted(tmp_path, {f"probe_{index}.py": f"import os\nprint('got', {read})\n"
                              for index, read in enumerate(ENV_SECRET_READS)})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        analyses = [q.run_python(f"probe_{index}.py") for index in range(len(ENV_SECRET_READS))]
        run_id = q.run_id
    for index, (read, analysis) in enumerate(zip(ENV_SECRET_READS, analyses)):
        assert analysis.result.exit_code != 0, read
        assert [(d.kind, d.target) for d in analysis.denials] == [
            ("env", "ANTHROPIC_API_KEY")], read
        assert SECRET not in output(analysis), read
        recorded = [e for e in stored(store, run_id, "quarantine.denied")
                    if e["detail"]["target_script"] == f"probe_{index}.py"]
        assert len(recorded) == 1, read
        assert recorded[0]["detail"]["kind"] == "env", read
        assert recorded[0]["detail"]["target"] == "ANTHROPIC_API_KEY", read
    assert len(stored(store, run_id, "quarantine.denied")) == len(ENV_SECRET_READS)
    assert SECRET not in json.dumps(store.objective_events(run_id), default=str)


def test_a_denial_that_the_repo_catches_is_still_recorded(tmp_path, store, base):
    repo = planted(tmp_path, {"probe.py": (
        "import os\n"
        "try:\n    os.environ['OPENAI_API_KEY']\nexcept PermissionError:\n    print('hidden')\n")})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        analysis = q.run_python("probe.py")
    assert analysis.result.exit_code == 0 and "hidden" in analysis.result.stdout
    assert [(d.kind, d.target) for d in analysis.denials] == [("env", "OPENAI_API_KEY")]


def test_environment_is_built_from_nothing_and_rebound_into_the_root(tmp_path, store, base):
    repo = planted(tmp_path, {"probe.py": (
        "import json, os\n"
        "env = dict(os.environ)\n"
        "print(json.dumps({'env': env, 'home': os.listdir(env['HOME'])}))\n")})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        analysis = q.run_python("probe.py")
        root = q.root
    seen = json.loads(analysis.result.stdout)
    env = seen["env"]
    assert set(env) <= ALLOWED_KEYS
    assert env["FRIDAY_QUARANTINE"] == "1"
    assert SECRET not in json.dumps(env)
    for key in PROFILE_KEYS & set(env):
        assert Path(env[key]).resolve().is_relative_to(root), key
    assert env["USERPROFILE"] != os.environ.get("USERPROFILE")
    assert seen["home"] == []
    assert env["FRIDAY_EXECUTION_EGRESS"] == "DENY_ALL"
    assert not analysis.denied


# --- files ------------------------------------------------------------------

READ_TEXT = "import sys\nprint(open(sys.argv[1]).read())\n"
READ_BYTES = "import sys\nprint(open(sys.argv[1], 'rb').read())\n"
LIST_DIR = "import os, sys\nprint(os.listdir(os.path.dirname(sys.argv[1])))\n"
WRITE_NEXT_TO = "import sys\nopen(sys.argv[1] + '.new', 'w').write('x')\n"
DELETE_IT = "import os, sys\nos.remove(sys.argv[1])\n"
USER_FILE_PROBES = {"read_text": READ_TEXT, "read_bytes": READ_BYTES,
                    "list_dir": LIST_DIR, "write_next_to": WRITE_NEXT_TO,
                    "delete_it": DELETE_IT}


def test_planted_repo_touching_a_user_file_is_denied_and_recorded(tmp_path, store, base):
    victim = user_file(tmp_path)
    repo = planted(tmp_path, {f"{name}.py": script for name, script in USER_FILE_PROBES.items()})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        for name in USER_FILE_PROBES:
            analysis = q.run_python(f"{name}.py", str(victim))
            assert analysis.result.exit_code != 0, name
            assert USER_FILE_TEXT not in output(analysis), name
            # Checked after every probe, not at the end: a probe that got
            # through would change what the next one sees.
            assert victim.read_text(encoding="utf-8") == USER_FILE_TEXT, name
            assert not Path(str(victim) + ".new").exists(), name
            assert analysis.denials and all(d.kind == "file" for d in analysis.denials), name
            recorded = [e for e in stored(store, q.run_id, "quarantine.denied")
                        if e["detail"]["target_script"] == f"{name}.py"]
            assert len(recorded) == len(analysis.denials), name
            assert all(e["detail"]["kind"] == "file" for e in recorded), name


def test_a_relative_traversal_out_of_the_root_is_denied(tmp_path, store, base):
    victim = user_file(tmp_path)
    repo = planted(tmp_path, {"probe.py": READ_TEXT})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        relative = os.path.relpath(victim, q.repo)
        analysis = q.run_python("probe.py", relative)
    assert [d.kind for d in analysis.denials] == ["file"]
    assert USER_FILE_TEXT not in output(analysis)


def test_the_data_directory_and_sibling_quarantines_are_out_of_reach(tmp_path, store, base):
    repo = planted(tmp_path, {"probe.py": (
        "import os\n"
        "home = os.environ['HOME']\n"
        "for hop in ('ada.sqlite3', ''):\n"
        "    target = os.path.join(home, '..', '..', '..', hop)\n"
        "    try:\n"
        "        print(open(target, 'rb').read() if hop else os.listdir(target))\n"
        "    except PermissionError:\n"
        "        print('denied')\n"
        "print(os.listdir(os.path.join(home, '..', '..')))\n")})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        analysis = q.run_python("probe.py")
    assert "LIVE-ROWS" not in output(analysis)
    assert "ada.sqlite3" not in analysis.result.stdout
    assert {d.kind for d in analysis.denials} == {"file"}
    assert len(analysis.denials) == 3


def test_the_guard_and_record_are_not_the_repos_to_touch(tmp_path, store, base):
    repo = planted(tmp_path, {"probe.py": "import sys\nopen(sys.argv[1], 'w').write('')\n"})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        record = q.root / "_record" / "denials.jsonl"
        analysis = q.run_python("probe.py", str(record))
    assert [d.kind for d in analysis.denials] == ["file"]


def test_control_the_same_probe_reads_the_file_through_the_plain_job_object(tmp_path):
    """Why the guard exists: the Job Object alone does not stop an
    absolute-path read, so the denials above are the guard's doing."""
    victim = user_file(tmp_path)
    repo = planted(tmp_path, {"probe.py": READ_TEXT})
    with execution.NativeExecutionEnvironment(repo) as plain:
        ran = plain.run([sys.executable, "probe.py", str(victim)])
    assert USER_FILE_TEXT in ran.stdout


# --- network and processes --------------------------------------------------

def test_planted_socket_is_denied_and_nothing_connects(tmp_path, store, base):
    repo = planted(tmp_path, {"probe.py": (
        "import socket, sys\n"
        "s = socket.socket()\n"
        "s.connect(('127.0.0.1', int(sys.argv[1])))\n"
        "s.sendall(b'stolen')\n")})
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with boxed(store=store, base=base) as q:
            q.acquire(repo)
            analysis = q.run_python("probe.py", str(server.getsockname()[1]))
            run_id = q.run_id
        server.settimeout(0.5)
        with pytest.raises(TimeoutError):
            server.accept()
    assert analysis.result.exit_code != 0
    assert [d.kind for d in analysis.denials] == ["network"]
    assert stored(store, run_id, "quarantine.denied")[0]["detail"]["kind"] == "network"


def test_name_resolution_is_denied_too(tmp_path, store, base):
    repo = planted(tmp_path, {"probe.py": (
        "import socket\nsocket.getaddrinfo('example.invalid', 443)\n")})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        analysis = q.run_python("probe.py")
    assert [d.kind for d in analysis.denials] == ["network"]


def test_a_child_process_is_denied(tmp_path, store, base):
    repo = planted(tmp_path, {"probe.py": (
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', 'print(1)'])\n")})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        analysis = q.run_python("probe.py")
    assert [d.kind for d in analysis.denials] == ["process"]


# --- the OS boundary: refused by the kernel, with the audit hook out of it ---
#
# These run a planted script straight under the container, NOT through
# quarantine_guard. Nothing in the process watches it, so every refusal below
# is the kernel's. This is what shows the boundary is not the hook.

def bare(q, script: str, *args: str, timeout: float = 30.0) -> execution.Execution:
    return q._env.run([q._python, "-I", "-B", script, *args], timeout=timeout)


OS_OPS = (
    "import os, sys\n"
    "victim = sys.argv[1]\n"
    "for label, action in (\n"
    "    ('read', lambda: open(victim).read()),\n"
    "    ('list', lambda: os.listdir(os.path.dirname(victim))),\n"
    "    ('write', lambda: open(victim + '.new', 'w').write('x')),\n"
    "    ('append', lambda: open(victim, 'a').write('x')),\n"
    "    ('delete', lambda: os.remove(victim)),\n"
    "):\n"
    "    try:\n"
    "        action(); print(label, 'ALLOWED')\n"
    "    except OSError:\n"
    "        print(label, 'DENIED')\n")
WINAPI_READ = (
    "import _winapi, sys\n"
    "try:\n"
    "    handle = _winapi.CreateFile(sys.argv[1], 0x80000000, 1, 0, 3, 0x80, 0)\n"
    "    print('READ', _winapi.ReadFile(handle, 200)[0])\n"
    "except OSError as error:\n"
    "    print('DENIED', error.winerror)\n")
SPAWN_GRANDCHILD = (
    "import _winapi, subprocess, sys\n"
    "exe, victim, out = sys.argv[1:4]\n"
    "line = subprocess.list2cmdline([exe, '-I', '-B', 'grand.py', victim, out])\n"
    "process = _winapi.CreateProcess(None, line, None, None, False, 0x08000000,\n"
    "                                None, None, subprocess.STARTUPINFO())[0]\n"
    "_winapi.WaitForSingleObject(process, 60000)\n"
    "print('grandchild exit', _winapi.GetExitCodeProcess(process))\n")
GRANDCHILD = (
    "import sys\n"
    "try:\n"
    "    text = open(sys.argv[1]).read()\n"
    "except OSError:\n"
    "    text = 'DENIED'\n"
    "open(sys.argv[2], 'w').write(text)\n")
RAW_NETWORK = (
    "import _socket, asyncio, sys\n"
    "port, udp_port = int(sys.argv[1]), int(sys.argv[2])\n"
    "raw = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)\n"
    "raw.settimeout(3)\n"
    "try:\n"
    "    raw.connect(('127.0.0.1', port)); raw.send(b'stolen'); print('raw CONNECTED')\n"
    "except OSError:\n"
    "    print('raw DENIED')\n"
    "async def go():\n"
    "    reader, writer = await asyncio.wait_for(\n"
    "        asyncio.open_connection('127.0.0.1', port), 3)\n"
    "    writer.write(b'stolen'); await writer.drain()\n"
    "try:\n"
    "    asyncio.run(go()); print('proactor CONNECTED')\n"
    "except OSError:\n"
    "    print('proactor DENIED')\n"
    "datagram = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)\n"
    "try:\n"
    "    datagram.sendto(b'stolen', ('127.0.0.1', udp_port)); print('udp SENT')\n"
    "except OSError:\n"
    "    print('udp DENIED')\n")


@needs_os_boundary
def test_without_the_hook_the_kernel_still_denies_every_file_route(tmp_path, store, base):
    victim = user_file(tmp_path)
    repo = planted(tmp_path, {"ops.py": OS_OPS, "winapi.py": WINAPI_READ})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        ops = bare(q, "ops.py", str(victim))
        winapi = bare(q, "winapi.py", str(victim))
    assert [line.split()[1] for line in ops.stdout.splitlines()] == ["DENIED"] * 5, ops
    assert winapi.stdout.split() == ["DENIED", "5"], winapi      # ERROR_ACCESS_DENIED
    assert USER_FILE_TEXT not in ops.stdout + winapi.stdout
    assert victim.read_text(encoding="utf-8") == USER_FILE_TEXT
    assert not Path(str(victim) + ".new").exists()


@pytest.mark.skipif(os.name != "nt", reason="_winapi is Windows-only")
def test_control_the_tripwire_tier_alone_does_not_stop_a_winapi_read(tmp_path, store, base):
    """Why the default is the OS tier: with only the Job Object and no hook in
    the way, the same planted read goes through. Not a defect of the tripwire
    tier, which never claimed to be a boundary; the reason it is opt-in."""
    victim = user_file(tmp_path)
    repo = planted(tmp_path, {"winapi.py": WINAPI_READ})
    with boxed(store=store, base=base, isolation="tripwire") as q:
        q.acquire(repo)
        ran = bare(q, "winapi.py", str(victim))
    assert "READ" in ran.stdout and USER_FILE_TEXT in ran.stdout


@needs_os_boundary
def test_a_winapi_read_through_the_guard_is_refused_and_never_leaks(tmp_path, store, base):
    """The verifier's bypass: ordinary Python, no `open`, no audit event the
    old list knew. On a build whose `_winapi.CreateFile` is audited the hook
    records it; on any build the kernel refuses it."""
    victim = user_file(tmp_path)
    repo = planted(tmp_path, {"probe.py": WINAPI_READ})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        analysis = q.run_python("probe.py", str(victim))
    assert USER_FILE_TEXT not in output(analysis) and "READ" not in analysis.result.stdout
    assert "DENIED" in analysis.result.stdout or analysis.denials


@needs_os_boundary
def test_a_grandchild_made_with_winapi_createprocess_has_no_more_reach(tmp_path, store, base):
    victim = user_file(tmp_path)
    repo = planted(tmp_path, {"spawn.py": SPAWN_GRANDCHILD, "grand.py": GRANDCHILD})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        out = q.repo / "grand.out"
        ran = bare(q, "spawn.py", q._python, str(victim), str(out))
        verdict = out.read_text(encoding="utf-8") if out.exists() else "NO-OUTPUT"
    assert "grandchild exit 0" in ran.stdout, ran
    assert verdict == "DENIED"
    assert USER_FILE_TEXT not in ran.stdout + ran.stderr + verdict


@needs_os_boundary
def test_without_the_hook_the_kernel_denies_tcp_asyncio_and_udp(tmp_path, store, base):
    repo = planted(tmp_path, {"net.py": RAW_NETWORK})
    with socket.socket() as server, socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM) as datagram:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        datagram.bind(("127.0.0.1", 0))
        with boxed(store=store, base=base) as q:
            q.acquire(repo)
            ran = bare(q, "net.py", str(server.getsockname()[1]),
                       str(datagram.getsockname()[1]))
        assert "raw DENIED" in ran.stdout and "proactor DENIED" in ran.stdout, ran
        assert "CONNECTED" not in ran.stdout
        server.settimeout(0.5)
        with pytest.raises(TimeoutError):
            server.accept()
        datagram.settimeout(0.5)
        with pytest.raises(TimeoutError):
            datagram.recvfrom(64)


@needs_os_boundary
def test_one_quarantine_cannot_reach_another_at_the_os_level(tmp_path, store, base):
    with boxed(store=store, base=base) as first, boxed(store=store, base=base) as second:
        first.acquire(planted(tmp_path, {"secret.txt": USER_FILE_TEXT}, name="first"))
        second.acquire(planted(tmp_path, {"ops.py": OS_OPS}, name="second"))
        target = first.repo / "secret.txt"
        ran = bare(second, "ops.py", str(target))
        assert (first.repo / "secret.txt").read_text(encoding="utf-8") == USER_FILE_TEXT
    assert [line.split()[1] for line in ran.stdout.splitlines()] == ["DENIED"] * 5, ran
    assert USER_FILE_TEXT not in ran.stdout


def load_guard():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "quarantine_guard_under_test", Path(quarantine.__file__).with_name("quarantine_guard.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_guard_records_the_winapi_routes_the_old_list_missed(tmp_path):
    guard = load_guard()
    root = tmp_path / "root"
    root.mkdir()
    record = tmp_path / "record.jsonl"
    fd = os.open(record, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    guard._config.update(root=guard._fold(str(root)), repo=str(root),
                         readonly_dirs=[], read_roots=[], fd=fd)
    victim = user_file(tmp_path)
    try:
        guard._inspect("_winapi.CreateFile", (str(root / "ok.txt"), 0x80000000, 1, 0, 3, 0x80, 0))
        for event, args in (
                ("_winapi.CreateFile", (str(victim), 0x80000000, 1, 0, 3, 0x80, 0)),
                ("_winapi.CreateFile", (str(victim), 0x40000000, 0, 0, 2, 0x80, 0)),
                ("_winapi.CreateProcess", (None, "cmd /c dir", None)),
                ("_winapi.OpenProcess", (0x1F0FFF, False, 4)),
                ("_winapi.CreateJunction", (str(victim), str(root / "j"))),
                ("_winapi.CreateNamedPipe", ("\\\\.\\pipe\\x", 3, 0, 1, 0, 0, 0, 0))):
            with pytest.raises(PermissionError):
                guard._inspect(event, args)
    finally:
        os.close(fd)
    kinds = [json.loads(line)["kind"] for line in record.read_text().splitlines()]
    assert kinds == ["file", "file", "process", "process", "native", "native"]


def test_a_container_is_refused_any_egress_but_deny_all(tmp_path):
    with pytest.raises(execution.ExecutionError, match="DENY_ALL"):
        execution.NativeExecutionEnvironment(
            tmp_path, container=object(), egress=execution.Egress(execution.ALLOW_ALL))


def test_a_container_that_cannot_start_a_command_is_a_refusal_not_a_fallback(tmp_path):
    class Broken:
        def run(self, *args, **kwargs):
            raise appcontainer.AppContainerError("no token")

    with execution.NativeExecutionEnvironment(tmp_path, container=Broken()) as boxed_env:
        ran = boxed_env.run([sys.executable, "-c", "print('uncontained')"])
        assert boxed_env.strength().startswith("APPCONTAINER+")
    assert ran.exit_code == 127 and "uncontained" not in ran.stdout
    assert "no token" in ran.stderr


# --- a repository that is not hostile still runs ----------------------------

def test_a_benign_repo_runs_clean_inside_its_root(tmp_path, store, base):
    repo = planted(tmp_path, {
        "helper.py": "VALUE = 41\n",
        "probe.py": (
            "import hashlib, json, os, pathlib, sqlite3\n"
            "import helper\n"
            "home = pathlib.Path(os.environ['HOME'])\n"
            "(home / 'note.txt').write_text(str(helper.VALUE + 1))\n"
            "open('local.txt', 'w').write('ok')\n"
            "print(json.dumps([(home / 'note.txt').read_text(), os.devnull != '']))\n")})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        analysis = q.run_python("probe.py")
        run_id = q.run_id
        root = q.root
    assert not root.exists()        # files the child wrote do not block cleanup
    assert analysis.result.ok, analysis.result.stderr
    assert json.loads(analysis.result.stdout) == ["42", True]
    assert not analysis.denied
    assert [e["event"] for e in store.objective_events(run_id)] == [
        "quarantine.acquired", "quarantine.ran"]


# --- taking a repository in -------------------------------------------------

def test_a_repo_containing_a_link_is_refused_before_anything_is_copied(
        tmp_path, store, base):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "stolen.txt").write_text("x", encoding="utf-8")
    repo = planted(tmp_path, {"a.py": "x = 1\n"})
    link_dir(outside, repo / "escape")
    with boxed(store=store, base=base) as q:
        with pytest.raises(quarantine.QuarantineError, match="escape"):
            q.acquire(repo)
        assert list(q.repo.iterdir()) == []
    assert (outside / "stolen.txt").read_text(encoding="utf-8") == "x"


def test_a_link_as_the_repo_root_is_refused(tmp_path, store, base):
    real = planted(tmp_path, {"a.py": "x = 1\n"}, name="real")
    link_dir(real, tmp_path / "front")
    with boxed(store=store, base=base) as q:
        with pytest.raises(quarantine.QuarantineError, match="link"):
            q.acquire(tmp_path / "front")


def test_size_and_count_caps_refuse_the_repo(tmp_path, store, base):
    many = planted(tmp_path, {f"f{i}.py": "x\n" for i in range(6)}, name="many")
    big = planted(tmp_path, {"blob.bin": "x" * 5000}, name="big")
    with boxed(store=store, base=base, max_files=5) as q:
        with pytest.raises(quarantine.QuarantineError, match="entries"):
            q.acquire(many)
        assert list(q.repo.iterdir()) == []
    with boxed(store=store, base=base, max_bytes=1000) as q:
        with pytest.raises(quarantine.QuarantineError, match="bytes"):
            q.acquire(big)


def test_a_repo_that_contains_the_quarantine_is_refused(tmp_path, store):
    (tmp_path / "work").mkdir()
    with boxed(store=store, base=tmp_path / "work" / "q") as q:
        with pytest.raises(quarantine.QuarantineError, match="contains the quarantine"):
            q.acquire(tmp_path / "work")


# --- cleanup ----------------------------------------------------------------

def test_the_root_is_deleted_on_exit_and_on_error(tmp_path, store, base):
    repo = planted(tmp_path, {"a.py": "x = 1\n"})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        root = q.root
        assert root.is_dir() and root.is_relative_to(base)
    assert not root.exists() and list(base.iterdir()) == []
    with pytest.raises(RuntimeError, match="boom"):
        with boxed(store=store, base=base) as q:
            root = q.root
            raise RuntimeError("boom")
    assert not root.exists()


def test_cleanup_removes_a_link_and_never_follows_it(tmp_path, store, base):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    with boxed(store=store, base=base) as q:
        q.acquire(planted(tmp_path, {"a.py": "x = 1\n"}))
        link_dir(outside, q.repo / "planted_link")
        root = q.root
    assert not root.exists()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_the_default_root_is_under_data_but_never_the_database():
    assert quarantine.default_base() == DATA_DIR / "quarantine"
    assert quarantine.default_base() != DATA_DIR / "ada.sqlite3"


# --- the tier is told truthfully --------------------------------------------

def test_the_explicit_tripwire_tier_never_claims_an_os_boundary(tmp_path, store, base):
    repo = planted(tmp_path, {"a.py": "x = 1\n"})
    with boxed(store=store, base=base, isolation="tripwire") as q:
        q.acquire(repo)
        q.run_python("a.py")
        report = q.report()
    assert report["strength"] in ("JOB_OBJECT+TRIPWIRE", "PROCESS_ONLY+TRIPWIRE")
    assert "APPCONTAINER" not in json.dumps(report).upper()
    assert report["isolation"] == "tripwire"
    assert report["egress"] == "DENY_ALL"
    assert "not an OS boundary" in report["enforcement"]
    assert SECRET not in json.dumps(report)


@needs_os_boundary
def test_the_default_tier_is_the_os_boundary_and_says_so(tmp_path, store, base):
    repo = planted(tmp_path, {"a.py": "x = 1\n"})
    with boxed(store=store, base=base) as q:
        q.acquire(repo)
        ran = q.run_python("a.py")
        report = q.report()
    assert ran.result.ok, ran.result.stderr
    assert ran.strength in ("APPCONTAINER+JOB_OBJECT+TRIPWIRE", "APPCONTAINER+PROCESS_ONLY+TRIPWIRE")
    assert report["isolation"] == "os" and report["strength"] == ran.strength
    assert "AppContainer" in report["enforcement"] and "records" in report["enforcement"]
    assert SECRET not in json.dumps(report)


def test_with_no_os_boundary_the_default_refuses_rather_than_falling_back(
        tmp_path, store, base, monkeypatch):
    monkeypatch.setattr(appcontainer, "available", lambda: False)
    with pytest.raises(quarantine.QuarantineError, match="no OS boundary"):
        with quarantine.Quarantine(store=store, base=base):
            pass
    assert not base.exists() or list(base.iterdir()) == []


def test_an_unknown_isolation_is_refused(store, base):
    with pytest.raises(quarantine.QuarantineError, match="isolation"):
        quarantine.Quarantine(store=store, base=base, isolation="trust-me")


def test_running_before_acquiring_is_refused(store, base):
    with boxed(store=store, base=base) as q:
        with pytest.raises(quarantine.QuarantineError, match="acquire"):
            q.run_python("a.py")


# --- what the probe cases cost ----------------------------------------------

def test_probe_cases_share_one_quarantine_each():
    """Every probe of a case runs in the one quarantine its setup paid for. A
    quarantine per probe is what made this file slow: an AppContainer profile,
    an icacls grant and a tree delete each time."""
    cases = (test_planted_repo_reading_an_env_secret_is_denied_and_recorded,
             test_planted_repo_touching_a_user_file_is_denied_and_recorded)
    for case in cases:
        if case.__name__ not in TALLY or not TALLY[case.__name__]["quarantines"]:
            pytest.skip(f"{case.__name__} did not run in this session")
        counts = TALLY[case.__name__]
        assert counts["quarantines"] == 1, (case.__name__, counts)
        assert counts["grants"] <= 1, (case.__name__, counts)
    # Fast by dropping probes would defeat the file; the sets keep their size.
    assert len(ENV_SECRET_READS) == 4 and len(USER_FILE_PROBES) == 5


# --- the execution seam it rides on -----------------------------------------

def test_execution_profile_replaces_the_host_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/host/home")
    with execution.NativeExecutionEnvironment(tmp_path) as plain:
        assert plain.environment()["HOME"] == "/host/home"
        assert not plain.strength().endswith("TRIPWIRE")
    with execution.NativeExecutionEnvironment(
            tmp_path, profile={"PATH": "x"}, guard="TRIPWIRE") as boxed:
        env = boxed.environment()
        assert env["PATH"] == "x" and "HOME" not in env
        assert env["FRIDAY_EXECUTION_EGRESS"] == "DENY_ALL"
        assert boxed.strength().endswith("+TRIPWIRE")
        assert boxed.report()["profile"] == ["PATH"]
