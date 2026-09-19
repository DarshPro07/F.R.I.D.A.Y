"""
Skill-intelligence and self-model toolset: the ActionResult implementations
beneath the `skill_*` / `self_model_*` MCP tools.

These seven capabilities were born in `tools/vnext_control.py` returning
plain dicts, which made them the one thing a durable objective cannot call:
`capability_runtime` binds a capability to a `run`-taking toolset function,
and a dict from an adapter has no verification beneath it. Reach fell from
"all but the adapter-only ones" to 165/200 and `test_core01_red` said so.

Each function here does the work through the same module the adapter used
(`skill_ladder`, `skill_fingerprint`, `skill_permissions`, `skill_behavior`,
`self_model`) and states how the result was checked - a row read back from
the ladder, a switch file read back, a verdict that names its scenarios.
The MCP adapter is a thin wrapper over these; nothing is implemented twice.
"""
from __future__ import annotations

import json

from friday import contracts as c
from friday import skill_ladder as sl
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "agent_runtime"


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return run.record(c.started(run.run_id, tool_id).finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


def _fingerprints():
    from friday import skill_fingerprint as sf
    from friday.config import PROJECT_ROOT
    return sf, sf.SkillFingerprints(sl.SkillLadder(), root=PROJECT_ROOT)


# ---------------------------------------------------------------------------
# fingerprints (FR-012)
# ---------------------------------------------------------------------------

def skill_declare_dependencies(run: c.Run, name: str, dependencies: str, *,
                               engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Record what a VALIDATED skill's procedure rests on (`kind:target`
    pairs, comma-separated). Verified by reading the fingerprint rows back
    from the ladder; a dependency that does not exist is reported, not
    hidden."""
    tool_id = "skill_declare_dependencies"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    try:
        sf, fp = _fingerprints()
        deps = []
        for item in (x.strip() for x in dependencies.split(",") if x.strip()):
            kind, _, target = item.partition(":")
            deps.append(sf.Dependency(kind.strip(), target.strip()))
        if not deps:
            return run.record(c.failed(started, "no dependencies given (kind:target, comma-separated)"))
        out = fp.record(name, deps)
        readback = fp.dependencies(name)
    except KeyError as exc:
        return run.record(c.failed(started, str(exc)))
    except Exception as exc:  # noqa: BLE001
        return run.record(c.failed(started, f"{type(exc).__name__}: {exc}"))
    if len(readback) != len(deps):
        return run.record(c.partial(
            started, f"recorded {len(deps)} dependencies but read back {len(readback)}",
            output=_scoped(out)))
    return run.record(c.succeeded(
        started, output=_scoped({**out, "recorded": [d.get("target") for d in readback]}),
        verification=c.Verification(
            method="ladder_readback",
            evidence=f"{len(readback)} fingerprint rows for {name} v{out['version']}"
                     + (f"; missing: {out['missing']}" if out.get("missing") else ""))))


def skill_revalidation_sweep(run: c.Run, changed_paths: str = "", *,
                             engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Mark the VALIDATED skills whose declared dependencies moved as
    NEEDS_REVALIDATION and report which were left alone. The `untouched`
    list is the proof that unrelated skills were not disturbed."""
    tool_id = "skill_revalidation_sweep"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    try:
        _, fp = _fingerprints()
        paths = [p.strip() for p in changed_paths.split(",") if p.strip()] or None
        out = fp.sweep(changed_paths=paths)
        # read back: every skill reported stale is now NEEDS_REVALIDATION
        ladder = sl.SkillLadder()
        not_marked = [s["skill"] for s in out["stale"]
                      if (ladder.current(s["skill"]) or {}).get("state") != "NEEDS_REVALIDATION"]
    except Exception as exc:  # noqa: BLE001
        return run.record(c.failed(started, f"{type(exc).__name__}: {exc}"))
    if not_marked:
        return run.record(c.partial(
            started, f"reported stale but not marked: {not_marked}", output=_scoped(out)))
    return run.record(c.succeeded(
        started, output=_scoped(out),
        verification=c.Verification(
            method="ladder_readback",
            evidence=f"{len(out['stale'])} stale (read back as NEEDS_REVALIDATION), "
                     f"{len(out['clean'])} clean, {len(out['untouched'])} untouched")))


# ---------------------------------------------------------------------------
# permission manifests (GB-13)
# ---------------------------------------------------------------------------

def skill_declare_permissions(run: c.Run, name: str, manifest_yaml: str, *,
                              engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Record the scope a skill REQUESTS (never grants). A lint FAIL is a
    refusal with every finding; the skill stays read-only. Verified by
    reading the manifest back from the ladder."""
    tool_id = "skill_declare_permissions"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    try:
        from friday import skill_permissions as sp
        perms = sp.SkillPermissions(sl.SkillLadder())
        out = perms.record(name, manifest_yaml)
    except KeyError as exc:
        return run.record(c.failed(started, str(exc)))
    except Exception as exc:  # noqa: BLE001
        return run.record(c.failed(started, f"{type(exc).__name__}: {exc}"))
    if out.get("status") != "recorded":
        return run.record(started.finish(
            status=c.NOT_PERMITTED,
            error=f"manifest refused ({out.get('lint')}): " + "; ".join(out.get("findings") or [])[:800],
            output=_scoped(out)))
    manifest = perms.manifest_for(name)
    if manifest is None:
        return run.record(c.partial(started, "recorded but could not be read back", output=_scoped(out)))
    return run.record(c.succeeded(
        started, output=_scoped(out),
        verification=c.Verification(
            method="ladder_readback",
            evidence=f"manifest for {name} v{out.get('version')} read back: risk {manifest.risk}, "
                     f"lint {out.get('lint')}")))


# ---------------------------------------------------------------------------
# behaviour evaluation (GB-16)
# ---------------------------------------------------------------------------

def skill_behavior_scenarios(run: c.Run, skill: str, task: str, competing_pressure: str = "", *,
                             engine: PolicyEngine = default_engine) -> c.ActionResult:
    """The three prompts a skill is judged under (supportive / neutral /
    competing). Pure derivation; the evidence is the three strictness levels
    being present."""
    tool_id = "skill_behavior_scenarios"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    if not skill.strip() or not task.strip():
        return run.record(c.failed(started, "skill and task are both required"))
    from friday import skill_behavior as sb
    items = sb.scenarios(skill, task, competing_pressure=competing_pressure)
    levels = sorted({s.strictness for s in items})
    return run.record(c.succeeded(
        started,
        output=_scoped({"skill": skill, "scenarios": [
            {"id": s.id, "strictness": s.strictness, "prompt": s.prompt} for s in items]}),
        verification=c.Verification(method="scenario_set",
                                    evidence=f"{len(items)} scenarios at strictness {levels}")))


def skill_behavior_grade(run: c.Run, spec_json: str, trace_json: str, report_dir: str = "", *,
                         engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Grade whether a worker FOLLOWED a skill, deterministically over the
    trace. The verdict names the scenarios it rests on; INCOMPLETE is a
    verdict too, never a success."""
    tool_id = "skill_behavior_grade"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    try:
        from friday import skill_behavior as sb
        spec = sb.Spec.from_dict(json.loads(spec_json))
        raw = json.loads(trace_json)
        traces: dict[str, list] = {}
        for key, val in (raw or {}).items():
            if isinstance(val, str):
                traces[key] = sb.events_from_stream_json(val)
            else:
                traces[key] = [sb.Event(order=i, tool=str(e.get("tool", "")),
                                        arguments=sb._redact(sb._ser(e.get("arguments"))),
                                        status=str(e.get("status", "")),
                                        output=sb._redact(sb._ser(e.get("output"))))
                               for i, e in enumerate(val or [])]
        ev = sb.evaluate(spec, traces)
        out = ev.to_dict()
        if report_dir:
            out["report"] = str(sb.write_report(ev, report_dir))
    except (ValueError, KeyError, TypeError) as exc:
        return run.record(c.failed(started, f"bad spec or trace: {exc}"))
    except Exception as exc:  # noqa: BLE001
        return run.record(c.failed(started, f"{type(exc).__name__}: {exc}"))
    return run.record(c.succeeded(
        started, output=_scoped(out),
        verification=c.Verification(
            method="deterministic_trace_grade",
            evidence=f"verdict {ev.verdict} over scenarios {sorted(traces)}"
                     + (f"; weakest {ev.weakest}" if ev.weakest else ""))))


# ---------------------------------------------------------------------------
# runtime self-model (ML-05)
# ---------------------------------------------------------------------------

def self_model_snapshot(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """What Friday actually has right now, from runtime state. A read; the
    evidence is the snapshot's own timestamp and counts."""
    tool_id = "self_model_snapshot"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    try:
        from friday import self_model
        snap = self_model.snapshot()
        data = snap.to_dict()
    except Exception as exc:  # noqa: BLE001
        return run.record(c.failed(started, f"{type(exc).__name__}: {exc}"))
    return run.record(c.succeeded(
        started, output=_scoped({"description": snap.describe(), **data}),
        verification=c.Verification(
            method="runtime_snapshot",
            evidence=f"snapshot at {data.get('taken_at')}: {len(data.get('modalities') or [])} modalities, "
                     f"{len(data.get('families') or data.get('capability_families') or [])} families, "
                     f"{len(data.get('switched_off') or {})} switched off")))


def self_model_switch(run: c.Run, name: str, enabled: bool, reason: str = "", *,
                      engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Switch a capability off or back on by name. Verified by reading the
    durable switch file back and checking the entry is (or is not) there."""
    tool_id = "self_model_switch"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    try:
        from friday import self_model
        if enabled:
            self_model.enable(name)
        else:
            self_model.disable(name, reason or "switched off by the operator")
        current = self_model.switches()          # read back from disk
    except ValueError as exc:
        return run.record(c.failed(started, str(exc)))
    except Exception as exc:  # noqa: BLE001
        return run.record(c.failed(started, f"{type(exc).__name__}: {exc}"))
    is_off = name in current
    if is_off == bool(enabled):
        return run.record(c.partial(
            started, f"switch file read back does not show {name} {'on' if enabled else 'off'}",
            output=_scoped({"name": name, "switched_off": current})))
    return run.record(c.succeeded(
        started,
        output=_scoped({"name": name, "enabled": bool(enabled), "switched_off": current,
                        "description": self_model.snapshot().describe()}),
        side_effects=(f"{name} switched {'on' if enabled else 'off'} in the self-model",),
        verification=c.Verification(
            method="switch_file_readback",
            evidence=f"{name} {'absent from' if enabled else 'present in'} the switch file after the write")))
