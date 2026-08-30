#!/usr/bin/env python3
import os
import sys
import time
import socket
import threading
import subprocess
from pathlib import Path

# Base environment configuration
DIR_ROOT = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8000
MAX_WAIT = 45

# Targets and execution flags
T_SERVER = ("server.py",)
T_AGENT = ("agent_friday.py", "dev")

VENV_PATHS = (
    (".venv", "Scripts", "python.exe"),
    (".venv", "bin", "python"),
)

def log_msg(msg: str) -> None:
    print(f"[launcher] {msg}", flush=True)

def find_runtime() -> str:
    for p in VENV_PATHS:
        target = DIR_ROOT.joinpath(*p)
        if target.exists():
            return str(target)
    return sys.executable

def check_sock(h: str, p: int, t: float = 0.5) -> bool:
    with socket.socket() as s:
        s.settimeout(t)
        return s.connect_ex((h, p)) == 0

def check_ready(h: str, p: int, t: float, p_obj=None) -> bool:
    limit = time.monotonic() + t
    while time.monotonic() < limit:
        if p_obj and p_obj.poll() is not None:
            return False
        if check_sock(h, p):
            return True
        time.sleep(0.25)
    return False

def exec_sub(args, bin_path: str) -> subprocess.Popen:
    sys_env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    return subprocess.Popen(
        [bin_path, *args],
        cwd=str(DIR_ROOT),
        env=sys_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace", # fallback formatting to prevent crashes on random chars
        bufsize=1,
    )

def stream_out(label: str, p_obj: subprocess.Popen) -> None:
    for data in p_obj.stdout:
        print(f"[{label}] {data.rstrip()}", flush=True)

def dispatch(label: str, args, bin_path: str, registry: list) -> subprocess.Popen:
    p_obj = exec_sub(args, bin_path)
    registry.append((label, p_obj))
    threading.Thread(target=stream_out, args=(label, p_obj), daemon=True).start()
    return p_obj

def terminate_proc(p_obj: subprocess.Popen) -> None:
    if p_obj.poll() is not None:
        return
    if os.name != "nt":
        p_obj.terminate()
        return
    # Taskkill handles worker child-trees spawned by LiveKit dev subroutines
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p_obj.pid)], capture_output=True)

def run_preflight(bin_path: str) -> list:
    errs = []
    if not Path(bin_path).exists():
        errs.append(f"missing interpreter: {bin_path}")
    for script in (T_SERVER[0], T_AGENT[0]):
        if not (DIR_ROOT / script).exists():
            errs.append(f"missing runner target: {script}")
    if not (DIR_ROOT / ".env").exists():
        errs.append(".env setup required (copy template file)")
    return errs

def main() -> int:
    py_bin = find_runtime()
    log_msg(f"root   : {DIR_ROOT}")
    log_msg(f"python : {py_bin}")

    issues = run_preflight(py_bin)
    for err in issues:
        log_msg(f"ERR: {err}")

    if "--check" in sys.argv:
        status = "active" if check_sock(HOST, PORT) else "vacant"
        log_msg(f"port {PORT}: {status}")
        log_msg(f"preflight: {'FAILED' if issues else 'PASSED'}")
        return 1 if issues else 0

    if issues:
        return 1

    active_jobs = []
    try:
        if not check_sock(HOST, PORT):
            log_msg("spinning up mcp core...")
            srv = dispatch("server", T_SERVER, py_bin, active_jobs)
            if not check_ready(HOST, PORT, MAX_WAIT, srv):
                log_msg(f"mcp socket timeout on :{PORT} - killing execution")
                return 1
            log_msg(f"mcp core bound: http://{HOST}:{PORT}/sse")
        else:
            log_msg(f"port :{PORT} occupied - recycling binding context")

        log_msg("spinning up voice worker...")
        dispatch("agent", T_AGENT, py_bin, active_jobs)
        log_msg("runtime initialized. catch signal (ctrl+c) to exit.")

        while True:
            for name, proc in active_jobs:
                st_code = proc.poll()
                if st_code is not None:
                    log_msg(f"process '{name}' dropped with exit flag {st_code} - collapsing stack")
                    return st_code or 1
            time.sleep(0.3)

    except KeyboardInterrupt:
        log_msg("signal caught, shutting down execution tree...")
        return 0
    finally:
        for _, proc in reversed(active_jobs):
            terminate_proc(proc)
        log_msg("environment teardown complete.")

if __name__ == "__main__":
    rc = main()
    # Prevents instant window collapse on manual execution instances
    if rc and sys.stdin and sys.stdin.isatty():
        input("\nPress Enter to destroy window...")
    sys.exit(rc)
