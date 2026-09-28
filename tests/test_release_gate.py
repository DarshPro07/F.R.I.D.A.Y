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
from types import SimpleNamespace

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


class TestTheNegativeControlGateWantsARealRedPath:
    """VERIFY-002: §79's controls, each named test demonstrated here."""

    @staticmethod
    def _control(*tests, host_skips=None):
        return SimpleNamespace(tests=tests, open_row="",
                               host_skips=dict(host_skips or {}))

    @staticmethod
    def _gate(tmp_path, controls, outcomes):
        """Run the gate with a fake pytest. `outcomes` maps a node ID to the
        outcome of each of its cases; a node absent from it did not run. The
        fake writes the JUnit report pytest would have written."""
        calls = []

        def fake_run(cmd, *, timeout, env=None):
            calls.append(list(cmd))
            if "tests/test_negative_controls.py" in cmd:
                return 0, "16 passed", 1.0
            report = next(Path(part.split("=", 1)[1]) for part in cmd
                          if part.startswith("--junitxml="))
            cases = []
            for node in (part for part in cmd if "::" in part):
                path, *classes, name = node.split("::")
                classname = ".".join([path[:-3].replace("/", "."), *classes])
                for index, outcome in enumerate(outcomes.get(node, [])):
                    inner = {"failed": "<failure/>", "skipped": "<skipped/>"}.get(outcome, "")
                    cases.append(f'<testcase classname="{classname}" '
                                 f'name="{name}[{index}]">{inner}</testcase>')
            report.write_text("<testsuites><testsuite>" + "".join(cases)
                              + "</testsuite></testsuites>", encoding="utf-8")
            failed = any("failed" in outcomes.get(n, []) for n in cmd)
            return (1 if failed else 0), "done", 1.0

        original, RG._run = RG._run, fake_run
        try:
            result = RG.gate_negative_controls(sys.executable, str(tmp_path),
                                               controls=controls)
        finally:
            RG._run = original
        return result, calls

    def test_a_control_with_no_test_keeps_the_gate_red(self, tmp_path):
        controls = {"secret": self._control("t.py::a"),
                    "compaction": SimpleNamespace(tests=(), open_row="CTX-003",
                                                  host_skips={})}
        r, _ = self._gate(tmp_path, controls, {"t.py::a": ["passed"]})
        assert r.verdict == RG.FAIL, "a planned control was counted as a red path"
        assert any("compaction" in l and "CTX-003" in l for l in r.limitations)

    def test_a_control_whose_cases_all_skipped_is_not_demonstrated(self, tmp_path):
        controls = {"kernel": self._control("t.py::a", host_skips={"t.py::a": "no symlinks"})}
        r, _ = self._gate(tmp_path, controls, {"t.py::a": ["skipped", "skipped"]})
        assert r.verdict == RG.FAIL, "exit 0 with every case skipped read as a pass"
        assert any("no red-path case passed" in l for l in r.limitations), r.limitations
        assert not any("did not run" in l for l in r.limitations), r.limitations

    def test_a_skipped_key_test_fails_even_when_a_sibling_passed(self, tmp_path):
        """The negative control of a control can skip while its sibling passes;
        counting the control as demonstrated would hide exactly that."""
        controls = {"workspace": self._control("t.py::guard", "t.py::unfiltered_leak")}
        r, _ = self._gate(tmp_path, controls, {"t.py::guard": ["passed"],
                                               "t.py::unfiltered_leak": ["skipped"]})
        assert r.verdict == RG.FAIL
        assert any("unfiltered_leak" in l and "not an allowed" in l
                   for l in r.limitations)

    def test_an_allowed_host_skip_is_a_limitation_not_a_failure(self, tmp_path):
        controls = {"kernel": self._control("t.py::alias", "t.py::symlink",
                                            host_skips={"t.py::symlink": "no symlinks"})}
        r, _ = self._gate(tmp_path, controls, {"t.py::alias": ["passed"] * 3,
                                               "t.py::symlink": ["skipped"]})
        assert r.verdict == RG.PASS and r.exit_code == 0
        assert any("symlink" in l and "no symlinks" in l for l in r.limitations)

    def test_a_named_test_that_did_not_run_fails_the_gate(self, tmp_path):
        controls = {"secret": self._control("t.py::a", "t.py::renamed_away")}
        r, _ = self._gate(tmp_path, controls, {"t.py::a": ["passed"]})
        assert r.verdict == RG.FAIL
        assert any("renamed_away" in l and "did not run" in l for l in r.limitations)
        assert not any("t.py::a " in l for l in r.limitations), r.limitations

    def test_a_test_inside_a_class_is_matched(self, tmp_path):
        """JUnit writes `pkg.module.Class` as one dotted classname; the gate
        must find where the module ends rather than guess the longest path."""
        controls = {"gate": self._control("tests/t.py::TestGate::test_a")}
        r, _ = self._gate(tmp_path, controls,
                          {"tests/t.py::TestGate::test_a": ["passed"]})
        assert r.verdict == RG.PASS, r.limitations

    def test_a_failing_red_path_fails_the_gate(self, tmp_path):
        controls = {"secret": self._control("t.py::a")}
        r, _ = self._gate(tmp_path, controls, {"t.py::a": ["failed", "passed"]})
        assert r.verdict == RG.FAIL
        assert r.exit_code == 1
        assert any("t.py::a failed" in l for l in r.limitations), r.limitations

    def test_every_named_test_passing_passes_the_gate(self, tmp_path):
        controls = {"secret": self._control("t.py::a"),
                    "kernel": self._control("t.py::b", "t.py::c")}
        r, calls = self._gate(tmp_path, controls, {"t.py::a": ["passed"],
                                                   "t.py::b": ["passed"],
                                                   "t.py::c": ["passed", "passed"]})
        assert r.verdict == RG.PASS and r.exit_code == 0
        assert any("t.py::b" in c and "t.py::c" in c for c in calls)

    def test_the_real_registry_passes_only_when_every_control_has_a_test(self, tmp_path):
        controls = RG._negative_controls()
        assert len(controls) == 12
        every = {t: ["passed"] for c in controls.values() for t in c.tests}
        r, _ = self._gate(tmp_path, None, every)
        expected = (RG.PASS if all(c.tests for c in controls.values())
                    else RG.FAIL)
        assert r.verdict == expected

    def test_each_run_gets_a_basetemp_pytest_can_create(self, tmp_path):
        """pytest creates `--basetemp` without its parents. A nested path made
        every child run error at setup - found by running the gate for real,
        which reported FAIL rather than a pass, as it should."""
        controls = {"secret": self._control("t.py::a")}
        _, calls = self._gate(tmp_path, controls, {"t.py::a": ["passed"]})
        for cmd in calls:
            base = Path(cmd[cmd.index("--basetemp") + 1])
            assert base.parent == tmp_path, cmd

    def test_a_stale_report_from_an_earlier_run_is_not_judged(self, tmp_path):
        """A child that could not start writes no report; the report an
        earlier gate left at the same path must not be read as this run's."""
        (tmp_path / "neg-0.xml").write_text(
            '<testsuites><testsuite><testcase classname="t" name="a"/>'
            "</testsuite></testsuites>", encoding="utf-8")

        def cannot_start(cmd, *, timeout, env=None):
            if "tests/test_negative_controls.py" in cmd:
                return 0, "16 passed", 1.0
            return 127, "could not start: no interpreter", 0.0

        original, RG._run = RG._run, cannot_start
        try:
            r = RG.gate_negative_controls(sys.executable, str(tmp_path),
                                          controls={"secret": self._control("t.py::a")})
        finally:
            RG._run = original
        assert r.verdict == RG.FAIL
        assert any("did not run" in l for l in r.limitations), r.limitations
