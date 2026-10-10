"""ULTRA-001: an ultracode workflow as a validated JSON spec.

The spec is frozen dataclasses loaded from JSON by a stdlib validator. The
same file is the input to FRIDAY and to the Claude Code / Hermes harness
(HARN-001); `compile_plan` reduces either entry to one canonical plan.

Any violation raises `SpecError` naming the field, in the form
`stages[1].tier` or `budget_tokens`, so a person editing the file is sent to
the line that is wrong.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from friday.execution_economics import TIER_DEEP, TIER_ECONOMY, TIER_STANDARD

TIERS = (TIER_ECONOMY, TIER_STANDARD, TIER_DEEP)
EFFORTS = ("low", "medium", "high")
#: The only stage templates a deep-tier model is worth its price on.
DEEP_TEMPLATES = ("judge", "synthesis", "architecture")
SCHEMA_TYPES = ("object", "array", "string", "number", "integer", "boolean", "null")
_SCHEMA_KEYS = ("type", "properties", "required", "enum", "items")
_SPEC_KEYS = ("id", "budget_tokens", "stages")
_STAGE_KEYS = ("id", "template", "barrier", "schema", "tier", "effort", "cap",
               "dry_rounds")


class SpecError(ValueError):
    """A workflow spec that cannot be run. `field` names where."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field


@dataclass(frozen=True)
class Stage:
    id: str
    template: str
    schema: dict
    tier: str
    cap: int
    barrier: bool = False
    effort: str = "medium"
    dry_rounds: int = 0

    def to_dict(self) -> dict:
        return {"id": self.id, "template": self.template, "barrier": self.barrier,
                "schema": json.loads(json.dumps(self.schema)), "tier": self.tier,
                "effort": self.effort, "cap": self.cap, "dry_rounds": self.dry_rounds}


@dataclass(frozen=True)
class Spec:
    id: str
    budget_tokens: int
    stages: tuple[Stage, ...]

    def to_dict(self) -> dict:
        return {"id": self.id, "budget_tokens": self.budget_tokens,
                "stages": [s.to_dict() for s in self.stages]}


def _int(value, field: str, minimum: int) -> int:
    # bool is an int in Python; `true` is not a token count.
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SpecError(field, f"must be an integer >= {minimum}")
    return value


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(field, "must be a non-empty string")
    return value


def _check_schema(node, field: str) -> None:
    """The JSON-Schema subset a stage output may be held to."""
    if not isinstance(node, dict):
        raise SpecError(field, "must be an object")
    for key in node:
        if key not in _SCHEMA_KEYS:
            raise SpecError(f"{field}.{key}", f"not in the supported subset {_SCHEMA_KEYS}")
    if node.get("type") not in SCHEMA_TYPES:
        raise SpecError(f"{field}.type", f"must be one of {SCHEMA_TYPES}")
    props = node.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            raise SpecError(f"{field}.properties", "must be an object")
        for name, sub in props.items():
            _check_schema(sub, f"{field}.properties.{name}")
    required = node.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
            raise SpecError(f"{field}.required", "must be a list of strings")
        missing = [r for r in required if r not in (props or {})]
        if missing:
            raise SpecError(f"{field}.required", f"names undeclared properties {missing}")
    enum = node.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise SpecError(f"{field}.enum", "must be a non-empty list")
    if "items" in node:
        _check_schema(node["items"], f"{field}.items")


def _stage(raw, index: int) -> Stage:
    at = f"stages[{index}]"
    if not isinstance(raw, dict):
        raise SpecError(at, "must be an object")
    for key in raw:
        if key not in _STAGE_KEYS:
            raise SpecError(f"{at}.{key}", "unknown field")
    for key in ("id", "template", "schema", "tier", "cap"):
        if key not in raw:
            raise SpecError(f"{at}.{key}", "is required")
    barrier = raw.get("barrier", False)
    if not isinstance(barrier, bool):
        raise SpecError(f"{at}.barrier", "must be true or false")
    tier = raw["tier"]
    if tier not in TIERS:
        raise SpecError(f"{at}.tier", f"must be one of {TIERS}")
    effort = raw.get("effort", "medium")
    if effort not in EFFORTS:
        raise SpecError(f"{at}.effort", f"must be one of {EFFORTS}")
    _check_schema(raw["schema"], f"{at}.schema")
    return Stage(id=_text(raw["id"], f"{at}.id"),
                 template=_text(raw["template"], f"{at}.template"),
                 schema=json.loads(json.dumps(raw["schema"])), tier=tier,
                 cap=_int(raw["cap"], f"{at}.cap", 1), barrier=barrier, effort=effort,
                 dry_rounds=_int(raw.get("dry_rounds", 0), f"{at}.dry_rounds", 0))


def parse_spec(source) -> Spec:
    """Validate a spec given as JSON text or an already-decoded dict."""
    if isinstance(source, (str, bytes)):
        try:
            source = json.loads(source)
        except ValueError as exc:
            raise SpecError("$", f"not valid JSON ({exc})") from exc
    if not isinstance(source, dict):
        raise SpecError("$", "a spec is a JSON object")
    for key in source:
        if key not in _SPEC_KEYS:
            raise SpecError(key, "unknown field")
    if "budget_tokens" not in source:
        raise SpecError("budget_tokens", "is required: a run without a token budget is refused")
    budget = _int(source["budget_tokens"], "budget_tokens", 1)
    spec_id = _text(source.get("id"), "id")
    raw_stages = source.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise SpecError("stages", "must be a non-empty list")
    stages: list[Stage] = []
    seen: set[str] = set()
    deep_seen = False
    for index, raw in enumerate(raw_stages):
        stage = _stage(raw, index)
        if stage.id in seen:
            raise SpecError(f"stages[{index}].id", f"duplicate stage id {stage.id!r}")
        seen.add(stage.id)
        if stage.tier == TIER_DEEP:
            if stage.template not in DEEP_TEMPLATES:
                raise SpecError(f"stages[{index}].tier",
                                f"deep is only for {DEEP_TEMPLATES}, not {stage.template!r}")
            if deep_seen:
                raise SpecError(f"stages[{index}].tier", "at most one deep stage per spec")
            deep_seen = True
        stages.append(stage)
    return Spec(id=spec_id, budget_tokens=budget, stages=tuple(stages))


def load_spec(path: str | Path) -> Spec:
    """Validate a spec file."""
    return parse_spec(Path(path).read_text(encoding="utf-8"))


def compile_plan(spec: Spec) -> dict:
    """The canonical plan both entry points run: the spec, stages in order.

    A plain dict of JSON types, so two entry points that loaded the same
    file can be compared with `==`.
    """
    from friday.ultracode.tiers import resolve_tiers
    resolve_tiers(spec)
    return spec.to_dict()
