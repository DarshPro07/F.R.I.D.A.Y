"""The release gate's own honesty rules.

`scripts/release_gate.py` is the one command that decides whether Friday is
production-ready, so the ways it could LIE matter more than the ways it could
crash. Each test here pins one rule from PRD §16/§20 that the gate exists to
enforce.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import release_gate as RG  # noqa: E402


def _result(name="X", verdict=RG.PASS, exit_code=0, mandatory=True, limits=None):
    return RG.GateResult(name, "req", verdict, "detail", "cmd", exit_code,
                         1.0, mandatory, "", list(limits or []))


class TestTheGateCannotReportAnUnearnedPass:

    def test_a_skipped_soak_is_not_a_pass(self, tmp_path):
        """The soak is the one gate that cannot be approximated. Reporting a
        skip as success is exactly the unverified green claim §16 forbids."""
        r = RG.gate_soak(sys.executable, str(tmp_path), tmp_path, 0.0)
        assert r.verdict == RG.NOT_RUN
        assert r.verdict != RG.PASS
        assert r.limitations, "a skipped soak must say what is unknown"

    def test_a_soak_that_ran_but_did_not_return_PASS_fails(self, tmp_path):
        """A SMOKE or INCONCLUSIVE verdict is not a pass. Both have been
        produced by real runs here: INCONCLUSIVE caught a 3 h stall."""
        target = tmp_path / "soak"
        target.mkdir(parents=True)
        (target / "report.json").write_text(json.dumps(
            {"verdict": "INCONCLUSIVE", "gaps": [{"gap_s": 12000}],
             "detection_floor_mb_per_hour": 5.0}), encoding="utf-8")

        def fake_run(cmd, *, timeout, env=None):
            return 0, "done", 1.0            # the RUNNER succeeded...

        original, RG._run = RG._run, fake_run
        try:
            r = RG.gate_soak(sys.executable, str(tmp_path), tmp_path, 8.0)
        finally:
            RG._run = original
        assert r.verdict == RG.FAIL, (
            "an INCONCLUSIVE soak with exit code 0 was reported as a pass")
        assert any("INCONCLUSIVE" in l for l in r.limitations)
        assert any("sampling gap" in l for l in r.limitations)

    def test_the_soak_pass_states_its_detection_floor(self, tmp_path):
        """A PASS must never imply more precision than the run had."""
        target = tmp_path / "soak"
        target.mkdir(parents=True)
        (target / "report.json").write_text(json.dumps(
            {"verdict": "PASS", "detection_floor_mb_per_hour": 3.2}),
            encoding="utf-8")

        def fake_run(cmd, *, timeout, env=None):
            return 0, "done", 1.0

        original, RG._run = RG._run, fake_run
        try:
            r = RG.gate_soak(sys.executable, str(tmp_path), tmp_path, 8.0)
        finally:
            RG._run = original
        assert r.verdict == RG.PASS
        assert any("3.2 MB/hour" in l for l in r.limitations), r.limitations

    def test_a_pass_with_no_command_behind_it_fails_the_audit_gate(self):
        """§16's Audit row: no unverified 'green' claims. A verdict with no
        exit code is an opinion, and the report must refuse to carry it."""
        good = RG.gate_audit([_result(exit_code=0)], {"ram_percent_used": 50})
        assert good.verdict == RG.PASS

        bad = RG.gate_audit([_result(name="Invented", exit_code=None)],
                            {"ram_percent_used": 50})
        assert bad.verdict == RG.FAIL
        assert "unbacked" in bad.detail

    def test_readiness_requires_every_mandatory_gate(self):
        """The whole point of §20: ready means EVERY mandatory gate on the
        SAME candidate, not most of them."""
        for verdict in (RG.FAIL, RG.NOT_RUN, RG.BLOCKED):
            gates = [_result(name="A"), _result(name="B", verdict=verdict)]
            ready = all(g.verdict == RG.PASS for g in gates if g.mandatory)
            assert not ready, f"{verdict} counted as ready"


class TestTheReportIsHonestAboutConditions:

    def test_the_report_records_host_load(self):
        """A green suite on a machine at 98% RAM with a full disk is not the
        same evidence as a green suite on a healthy one - both produced
        false-looking code failures here on 2026-09-06."""
        host = {"ram_percent_used": 97.0, "ram_available_mb": 300,
                "system_drive_free_gb": 3.0, "platform": "Windows",
                "python": "3.11.15", "utc": "2026-09-06T12:00:00+00:00"}
        text = RG.render([_result()], host, {"sha": "abc", "branch": "main"}, True)
        assert "97.0%" in text and "300 MB free" in text
        assert "3.0 GB free" in text

    def test_a_low_disk_host_is_recorded_as_a_limitation(self):
        audit = RG.gate_audit([_result()], {"ram_percent_used": 60,
                                            "ram_available_mb": 4000,
                                            "system_drive_free_gb": 3.0})
        assert any("disk" in l for l in audit.limitations), audit.limitations

    def test_a_dirty_tree_is_named_in_the_report(self):
        """A gate run against uncommitted changes does not describe any
        commit, and the reader has to be told."""
        text = RG.render([_result()], {"utc": "t"},
                         {"sha": "abc123", "branch": "main", "dirty": True}, True)
        assert "dirty" in text.lower()

    def test_the_report_carries_the_command_for_every_gate(self):
        text = RG.render([_result()], {"utc": "t"}, {"sha": "a"}, True)
        assert "## Reproduce" in text and "cmd" in text

    def test_not_ready_is_stated_plainly(self):
        text = RG.render([_result(verdict=RG.FAIL, exit_code=1)],
                         {"utc": "t"}, {"sha": "a"}, False)
        assert "NOT PRODUCTION READY" in text
        assert "**FAIL**" in text


class TestTheProviderGateDoesNotOverclaim:

    def test_without_live_it_says_so(self, tmp_path):
        """§19.4: never infer health from configured credentials. Without a
        live probe this gate checks routing LOGIC and must not imply more."""
        def fake_run(cmd, *, timeout, env=None):
            return 0, "30 passed", 1.0

        original, RG._run = RG._run, fake_run
        try:
            r = RG.gate_provider_routing(sys.executable, str(tmp_path), live=False)
        finally:
            RG._run = original
        assert r.verdict == RG.PASS
        assert any("no live provider probe" in l for l in r.limitations), r.limitations


    def test_live_actually_runs_the_live_suite(self, tmp_path):
        """A flag that only changes the wording of a limitation is worse than
        no flag: the reader believes a route was probed when the same
        deterministic tests ran either way."""
        seen = []

        def fake_run(cmd, *, timeout, env=None):
            seen.append((list(cmd), dict(env or {})))
            return 0, "30 passed", 1.0

        original, RG._run = RG._run, fake_run
        try:
            RG.gate_provider_routing(sys.executable, str(tmp_path), live=True)
        finally:
            RG._run = original
        assert len(seen) == 2, f"--live did not run a second command: {seen}"
        live_cmd, live_env = seen[1]
        assert any("tests/live" in part for part in live_cmd), live_cmd
        assert live_env.get("FRIDAY_LIVE_PROVIDER_TESTS") == "1", live_env

    def test_a_failing_live_probe_fails_the_gate(self, tmp_path):
        calls = []

        def fake_run(cmd, *, timeout, env=None):
            calls.append(cmd)
            return (0, "30 passed", 1.0) if len(calls) == 1 else (1, "2 failed", 1.0)

        original, RG._run = RG._run, fake_run
        try:
            r = RG.gate_provider_routing(sys.executable, str(tmp_path), live=True)
        finally:
            RG._run = original
        assert r.verdict == RG.FAIL, "a failing live probe was reported as a pass"
