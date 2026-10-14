"""PKG-001: the Windows Sandbox run definition for FRIDAY.exe and the host's
verdict on what the sandbox reports. Nothing here needs Sandbox or a running
FRIDAY: the .wsb is parsed as XML, main() is driven only up to its refusals,
and verdict() is a pure function over the guest's result.json text."""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts import validate_exe_clean as clean
from scripts import validate_exe_sandbox as sbx

SHA = "ab" * 32
PASSING_DOCTOR = "  PASS     core imports  ok\n  FAIL     livekit credentials  none\n"


def _wsb(tmp_path):
    dirs = [tmp_path / name for name in ("dist", "guest", "out")]
    for folder in dirs:
        folder.mkdir()
    return ET.fromstring(sbx.build_wsb(*dirs, memory_mb=2048)), dirs


def _records(**overrides):
    records = []
    for journey in sbx.JOURNEYS:
        detail = {"note": "ok"}
        if journey == "SBX-00":
            detail = {"zip_sha256": SHA}
        elif journey == "SBX-03":
            detail = {"returncode": 2, "stdout": PASSING_DOCTOR}
        records.append({"id": journey, "ok": True, "detail": detail})
    for journey, change in overrides.items():
        record = next(r for r in records if r["id"] == journey.replace("_", "-"))
        record.update(change)
    return records


def _judge(records):
    return sbx.verdict(json.dumps(records), sbx.JOURNEYS, SHA)


def test_wsb_disables_network_and_clipboard_explicitly(tmp_path):
    root, _ = _wsb(tmp_path)
    for switch in ("Networking", "ClipboardRedirection", "vGPU", "AudioInput", "VideoInput",
                   "PrinterRedirection"):
        assert root.findtext(switch) == "Disable", switch
    assert root.findtext("ProtectedClient") == "Enable"
    assert root.findtext("MemoryInMB") == "2048"


def test_every_input_folder_is_read_only_and_only_results_is_writable(tmp_path):
    root, (dist, guest, out) = _wsb(tmp_path)
    mapped = {folder.findtext("SandboxFolder"): (folder.findtext("HostFolder"), folder.findtext("ReadOnly"))
              for folder in root.iter("MappedFolder")}
    assert mapped == {
        "C:\\In\\dist": (str(dist), "true"),
        "C:\\In\\guest": (str(guest), "true"),
        "C:\\Out": (str(out), "false"),
    }


def test_wsb_maps_no_repo_or_secret_paths(tmp_path):
    root, _ = _wsb(tmp_path)
    text = ET.tostring(root, encoding="unicode")
    assert str(sbx.ROOT) not in text
    assert ".env" not in text and "%" not in text
    for folder in root.iter("HostFolder"):
        assert not Path(folder.text).resolve().is_relative_to(sbx.ROOT)


def test_logon_command_runs_the_mapped_guest_script(tmp_path):
    root, _ = _wsb(tmp_path)
    command = root.find("LogonCommand").findtext("Command")
    assert command == ("powershell.exe -NoProfile -ExecutionPolicy Bypass "
                       "-File C:\\In\\guest\\sandbox_guest.ps1")
    assert sbx.GUEST_SCRIPT.name == "sandbox_guest.ps1" and sbx.GUEST_SCRIPT.is_file()


def test_main_refuses_without_sandbox_and_names_the_enable_command(monkeypatch, capsys):
    monkeypatch.setattr(clean, "sandbox_available", lambda system_root=None: False)
    assert sbx.main(["--jobs-parked"]) == 2
    out = capsys.readouterr().out
    assert "BLOCKED_EXTERNAL" in out
    assert 'Enable-WindowsOptionalFeature -FeatureName "Containers-DisposableClientVM" -All -Online' in out


def test_main_refuses_without_jobs_parked_flag(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(clean, "sandbox_available", lambda system_root=None: True)
    monkeypatch.setattr(sbx, "sandbox_running", lambda: False)
    archive = tmp_path / "FRIDAY-1.0-win64.zip"
    archive.write_bytes(b"zip")
    Path(f"{archive}.sha256").write_text(f"{SHA}  {archive.name}\n", encoding="utf-8")
    assert sbx.main(["--zip", str(archive)]) == 2
    assert "--jobs-parked" in capsys.readouterr().out
    # The order holds: a running sandbox and a missing artifact are refused too.
    monkeypatch.setattr(sbx, "sandbox_running", lambda: True)
    assert sbx.main(["--jobs-parked", "--zip", str(archive)]) == 2
    monkeypatch.setattr(sbx, "sandbox_running", lambda: False)
    assert sbx.main(["--jobs-parked", "--zip", str(tmp_path / "absent.zip")]) == 2
    Path(f"{archive}.sha256").unlink()
    assert sbx.main(["--jobs-parked", "--zip", str(archive)]) == 2


def test_verdict_rejects_missing_duplicate_and_unknown_journeys():
    records = _records()
    ok, reasons = _judge(records[:-1])
    assert not ok and any("missing" in r and "SBX-08" in r for r in reasons)
    ok, reasons = _judge(records + [dict(records[4])])
    assert not ok and any("duplicate" in r for r in reasons)
    ok, reasons = _judge(records + [{"id": "SBX-99", "ok": True, "detail": {}}])
    assert not ok and any("unknown" in r for r in reasons)
    ok, reasons = _judge(_records(SBX_05={"ok": False}))
    assert not ok and any("SBX-05" in r for r in reasons)
    ok, _ = _judge(_records(SBX_05={"ok": "true"}))
    assert not ok


def test_verdict_rejects_hash_mismatch_and_missing_hash():
    ok, reasons = _judge(_records(SBX_00={"detail": {"zip_sha256": "cd" * 32}}))
    assert not ok and any("sha256" in r for r in reasons)
    ok, reasons = _judge(_records(SBX_00={"detail": {}}))
    assert not ok and any("sha256" in r for r in reasons)


def test_verdict_rejudges_doctor_with_the_clean_rule():
    bad = "  PASS     core imports  ok\n  FAIL     python runtime  missing\n"
    assert not clean.doctor_verdict(1, bad)[0]
    ok, reasons = _judge(_records(SBX_03={"ok": True, "detail": {"returncode": 1, "stdout": bad}}))
    assert not ok and any("SBX-03" in r for r in reasons)
    ok, _ = _judge(_records(SBX_03={"detail": {"returncode": "0", "stdout": PASSING_DOCTOR}}))
    assert not ok


def test_verdict_rejects_oversized_input():
    records = _records()
    records[1]["detail"] = {"note": "x" * (1 << 20)}
    ok, reasons = sbx.verdict(json.dumps(records), sbx.JOURNEYS, SHA)
    assert not ok and any("1 MB" in r for r in reasons)


def test_verdict_accepts_a_complete_passing_result():
    assert _judge(_records()) == (True, [])
    upper = _records(SBX_00={"detail": {"zip_sha256": SHA.upper()}})
    assert _judge(upper) == (True, [])


def test_verdict_rejects_empty_and_garbled_result():
    for text in ("", "   ", "{not json", "{}", "null", "[1, 2]", '"SBX-00"'):
        ok, reasons = sbx.verdict(text, sbx.JOURNEYS, SHA)
        assert not ok and reasons, text


def test_verdict_and_detail_fail_closed_on_deeply_nested_input():
    hostile = '[' * 200000  # under 1 MB, but json.loads raises RecursionError
    ok, reasons = sbx.verdict(hostile, sbx.JOURNEYS, SHA)
    assert not ok and reasons
    assert sbx._detail(hostile, 'SBX-00') == {}


def test_guest_script_names_every_journey_and_uses_the_pin_session():
    script = sbx.GUEST_SCRIPT.read_text(encoding="utf-8")
    for journey in sbx.JOURNEYS:
        assert f"'{journey}'" in script, journey
    for needle in ("/api/auth/pin/set", "/api/auth/pin/verify", "/api/objective",
                   "/api/objective/status", "-WebSession", "memory_remember", "memory_recall",
                   "--no-agent", "--no-browser", "--doctor", "--stop", "--status", "--paths",
                   "Microsoft-Windows-Windows Defender/Operational"):
        assert needle in script, needle


def test_guest_driver_executes_a_real_objective_run(tmp_path):
    """The Python the guest hands to the installed FRIDAY.exe really drives
    a persisted run to COMPLETED: here from source, in its own process and
    state root, on a run made the way POST /api/objective makes one."""
    script = sbx.GUEST_SCRIPT.read_text(encoding="utf-8")
    driver = tmp_path / "drive.py"
    driver.write_text(script.split("$driver = @'\n", 1)[1].split("\n'@", 1)[0], encoding="utf-8")
    setup = (
        "import json, runpy, sys\n"
        "from friday import contracts as c\n"
        "from friday.toolsets import objectives as OT\n"
        "tasks = json.dumps([{'capability': 'memory_remember', 'arguments': "
        "{'subject': 'sandbox canary', 'value': '42'}, 'dependencies': []}])\n"
        "made = OT.objective_start(c.Run.create('test', capability='objectives'), 'remember', tasks=tasks)\n"
        "sys.argv = [sys.argv[1], made.output['run_id']]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n")
    env = {**os.environ, "FRIDAY_HOME": str(tmp_path / "home"), "ADA_DB": str(tmp_path / "ada.sqlite3"),
           "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, "-c", setup, str(driver)], capture_output=True, text=True,
                          timeout=300, cwd=sbx.ROOT, env=env, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr[-2000:]
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert report["status"] == "COMPLETED" and report["summary"]["succeeded"] == 1, report


def test_guest_script_publishes_result_json_only_at_the_end():
    script = sbx.GUEST_SCRIPT.read_text(encoding="utf-8")
    partial = script.index("result.partial.json")
    final = script.index("'result.json'")
    shutdown = script.index("shutdown /s /t 5")
    assert partial < final < shutdown
    assert script.count("'result.json'") == 1
    last_journey = max(script.index(f"'{journey}'") for journey in sbx.JOURNEYS)
    assert last_journey < final
