"""
Finding out where a capability lives must not load what implements it.

The regression this exists for was mine. Resolution originally imported all 21
toolset modules and inspected them with `getmembers`, which is the obvious way
to find every run-taking function and cost **1.6 seconds** on the first call -
paid by anything that touched the runtime, every test process included. Before
that layer existed `build_dispatch` imported three modules; resolution quietly
turned that into all of them, and with them OpenCV, Playwright, PIL and
whatever else a toolset happens to need.

It is a bug with no symptom. Nothing fails, no test goes red, the machine is
just slower to start and heavier at idle, and the architecture's rule that
installed is not the same as running has quietly stopped holding.

The fix was to read the source instead of importing it: the same answer in
about 13ms. These gates keep it that way - one measuring what gets imported,
one measuring how long, and one proving the answer is still right.
"""
from __future__ import annotations
import subprocess
import sys
HEAVY = ('mediapipe', 'cv2', 'playwright', 'build123d', 'crawl4ai', 'torch', 'PIL', 'numpy')
RESOLVE = f"\nimport sys, time, json\nstart = time.perf_counter()\nfrom friday import capability_runtime as R\ntable = R.resolutions()\nelapsed = time.perf_counter() - start\nloaded = sorted(m for m in {HEAVY!r} if m in sys.modules)\n# The direct measurement: which toolset modules resolution actually imported.\n# The eager version imported all 21 of them, and every heavy dependency they\n# pull in came with them.\ntoolsets = sorted(m for m in sys.modules\n                  if m.startswith(\"friday.toolsets.\")\n                  and not m.endswith(\"__init__\"))\nprint(json.dumps({{\"seconds\": elapsed, \"loaded\": loaded, \"toolsets\": toolsets,\n                  \"resolved\": len(table), \"reachable\": len(R.reachable())}}))\n"


def _resolve_in_a_clean_process() -> dict:
    import json

    finished = subprocess.run(
        [sys.executable, "-c", RESOLVE],
        capture_output=True, text=True, timeout=180)
    assert finished.returncode == 0, finished.stderr[-2000:]
    return json.loads(finished.stdout.strip().splitlines()[-1])


def test_resolution_imports_nothing_heavy():
    """
    A fresh interpreter, resolution, and nothing that costs real money to
    import. Run in a subprocess because the test session has already imported
    half the world by the time this runs - asserting on this process's
    sys.modules would measure the suite, not the runtime.
    """
    measured = _resolve_in_a_clean_process()

    assert measured["resolved"] > 100, (
        f"only {measured['resolved']} capabilities resolved; this gate would "
        f"pass trivially against an empty table")
    assert measured["loaded"] == [], (
        f"resolving capability names imported {measured['loaded']} - finding "
        f"out where a capability lives must not load what implements it")


def test_resolution_imports_no_toolset_module():
    """
    The direct form of the gate, and the one that actually holds.

    This used to be a stopwatch: resolution had to finish inside 0.9s, on the
    reasoning that the eager version took 1.6s. That is a proxy, and it
    drifted - two new toolsets in CORE-02B took the parse from 296ms to
    ~800ms simply by adding 600 lines for the AST reader to get through, and
    the gate started failing for a reason that had nothing to do with what it
    was guarding.

    What the eager bug actually did was *import all 21 toolset modules*, and
    with them MediaPipe, OpenCV and Playwright. So assert that, which is exact,
    deterministic, and does not care how fast the machine is.
    """
    measured = _resolve_in_a_clean_process()
    assert measured['resolved'] > 100, 'resolution found nothing to measure'
    assert measured['toolsets'] == [], f"resolution imported {len(measured['toolsets'])} toolset module(s): {measured['toolsets'][:5]}... Finding out where a capability lives must not load what implements it"


def test_resolution_is_not_pathologically_slow():
    """
    A coarse backstop only. The real guard is the two tests above; this one
    exists so that an accidental O(n^2) - re-parsing the same file once per
    capability, which did happen - shows up as something rather than nothing.
    """
    measured = _resolve_in_a_clean_process()
    assert measured['seconds'] < 3.0, f"resolution took {measured['seconds']:.2f}s, which is slow enough to be a bug rather than a cost"


def test_the_fast_answer_is_still_the_right_answer():
    """
    Speed that lost the answer would be worse than the slow version. The AST
    reader has to find the same implementations the importer did.
    """
    measured = _resolve_in_a_clean_process()

    assert measured["reachable"] > 100, (
        f"{measured['reachable']} reachable; resolution got faster by finding "
        f"less")


def test_loading_a_capability_is_what_imports_its_dependencies():
    """
    The other half of lazy: deferred, not abandoned. A capability that is
    actually called must still get its module.
    """
    from friday import capability_runtime as R

    resolution = R.resolutions()["files_roots"]
    assert callable(resolution.load()), \
        "resolution can name the implementation but not load it"
