"""Skill permission manifests, and the authority intersection that enforces them.

GB-13. The weakness this closes is a known one upstream (Gawkbot #1068): a
free-text skill ran with the whole tool surface of whoever invoked it, so
the prose of a skill was, in effect, a grant. Here a skill never grants.
It REQUESTS a scope, in a manifest, and the runtime executes under

    WORKER AUTHORITY  ∩  SKILL MANIFEST  ∩  OBJECTIVE AUTHORITY  ∩  USER POLICY

Each layer can only narrow. The user policy layer is `friday.policy`
(PolicyEngine), unchanged and still last; the other three are `Authority`
values combined with `intersect`, checked by `CapabilityRuntime` before a
capability is resolved or loaded, and folded into the Claude CLI allowlist
(`narrow_profile`) which the CLI enforces outside the model.

Rules a manifest cannot talk its way around:

- no manifest means READ-ONLY (R0). Undeclared is not unrestricted.
- a bare `*` is refused. "Everything" is the defect, spelled differently.
- a skill may not request a capability above its declared risk class
  (LOW -> R1, MEDIUM -> R2, HIGH -> R3). R4 is never requestable.
- unknown manifest keys are refused, including anything spelled like a
  grant (`grant`, `allow_all`, `override`, `bypass`).
- `prohibited` only ever adds to the deny set; intersection is a union there.
"""
from __future__ import annotations

import fnmatch
import json
import re
import time
from dataclasses import dataclass, field, replace

from friday import trust as T
from friday.skill_ladder import SkillLadder

# --------------------------------------------------------------------------
# risk classes and tiers
# --------------------------------------------------------------------------

LOW, MEDIUM, HIGH = "LOW", "MEDIUM", "HIGH"
RISKS = (LOW, MEDIUM, HIGH)

#: The most consequential tier a skill of each risk class may request.
RISK_CEILING = {LOW: T.R1, MEDIUM: T.R2, HIGH: T.R3}

_TIER_RANK = {tier: i for i, tier in enumerate(T.TIERS)}


def _tier_le(a: str, b: str) -> bool:
    return _TIER_RANK[a] <= _TIER_RANK[b]


def _min_tier(a: str, b: str) -> str:
    return a if _tier_le(a, b) else b


PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

#: Keys that read as a grant. A manifest carrying one is refused outright
#: rather than ignored - ignoring it would let the author believe it worked.
_GRANT_WORDS = ("grant", "allow_all", "allow-all", "override", "bypass", "unrestricted")

_TOP_KEYS = {"risk", "permissions"}
_PERMISSION_KEYS = {"capabilities", "prohibited", "filesystem", "network", "commands"}
_FS_KEYS = {"read", "write"}
_NET_KEYS = {"domains"}

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.S)


# --------------------------------------------------------------------------
# the manifest
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Lint:
    status: str
    findings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status != FAIL


@dataclass(frozen=True)
class SkillManifest:
    """What a skill asks for. Never what it gets."""

    risk: str
    capabilities: tuple[str, ...] = ()      # ids or fnmatch globs; empty = none requested
    prohibited: tuple[str, ...] = ()        # ids or globs the skill forswears
    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()       # empty = no writes requested
    network_domains: tuple[str, ...] = ()
    network: bool = False
    commands: bool = False

    @property
    def ceiling(self) -> str:
        return RISK_CEILING[self.risk]

    def authority(self, name: str = "") -> "Authority":
        """The scope this manifest requests, as an Authority layer."""
        return Authority(
            source=f"skill:{name}" if name else "skill",
            capabilities=frozenset(self.capabilities),
            prohibited=frozenset(self.prohibited),
            max_tier=self.ceiling,
            write_paths=tuple(self.write_paths),
            network=self.network,
            commands=self.commands,
        )

    def to_dict(self) -> dict:
        return {
            "risk": self.risk,
            "permissions": {
                "capabilities": list(self.capabilities),
                "prohibited": list(self.prohibited),
                "filesystem": {"read": list(self.read_paths), "write": list(self.write_paths)},
                "network": {"domains": list(self.network_domains)} if self.network_domains
                else self.network,
                "commands": self.commands,
            },
        }


def _strings(value, where: str, findings: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        findings.append(f"FAIL {where}: expected a list, got {type(value).__name__}")
        return ()
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            findings.append(f"FAIL {where}: entries must be non-empty strings")
            continue
        out.append(item.strip())
    return tuple(out)


def _bad_path(p: str) -> str | None:
    if p.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", p):
        return f"absolute path {p!r} - a manifest is portable, a machine path is not"
    if ".." in p.replace("\\", "/").split("/"):
        return f"path {p!r} escapes its root"
    return None


def parse(source: str | dict) -> tuple[SkillManifest | None, Lint]:
    """Parse and lint a manifest from YAML text, SKILL.md text, or a dict.

    Deterministic, no model. Returns (manifest, lint); the manifest is None
    when the lint is FAIL. WARN findings are for the author (an unregistered
    capability id, most often a Hermes-side tool name) and do not block.
    """
    findings: list[str] = []
    data = source
    if isinstance(source, str):
        text = source
        m = _FRONTMATTER.match(text.lstrip())
        if m:
            text = m.group(1)
        try:
            import yaml
            data = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001 - a parse error is a lint finding
            return None, Lint(FAIL, (f"FAIL yaml: {exc}",))
    if not isinstance(data, dict):
        return None, Lint(FAIL, ("FAIL manifest must be a mapping",))

    # -- words that must not appear ------------------------------------------
    # The per-level key allow-lists already refuse an unknown KEY. This scan
    # is for what they cannot see: a grant word in a VALUE (a capability
    # entry spelled `allow_all`, a path called `bypass/`), which is not a
    # scope request either.
    for token in _walk_tokens(data):
        low = str(token).lower()
        if any(word in low for word in _GRANT_WORDS):
            findings.append(f"FAIL {token!r}: a skill requests scope, it does not grant it")
    unknown = set(map(str, data)) - _TOP_KEYS
    if unknown:
        findings.append(f"FAIL unknown top-level keys {sorted(unknown)}; allowed {sorted(_TOP_KEYS)}")

    # -- risk ---------------------------------------------------------------
    risk = str(data.get("risk", "") or "").strip().upper()
    if risk not in RISKS:
        findings.append(f"FAIL risk must be one of {RISKS}, got {data.get('risk')!r}")
        risk = LOW  # placeholder so the rest can still be linted

    perms = data.get("permissions") or {}
    if not isinstance(perms, dict):
        findings.append("FAIL permissions must be a mapping")
        perms = {}
    unknown = set(map(str, perms)) - _PERMISSION_KEYS
    if unknown:
        findings.append(f"FAIL unknown permission keys {sorted(unknown)}; allowed {sorted(_PERMISSION_KEYS)}")

    capabilities = _strings(perms.get("capabilities"), "permissions.capabilities", findings)
    prohibited = _strings(perms.get("prohibited"), "permissions.prohibited", findings)
    if "*" in capabilities:
        findings.append("FAIL permissions.capabilities: a bare '*' requests everything, which is "
                        "the failure this manifest exists to prevent")

    fs = perms.get("filesystem") or {}
    if not isinstance(fs, dict):
        findings.append("FAIL permissions.filesystem must be a mapping")
        fs = {}
    unknown = set(map(str, fs)) - _FS_KEYS
    if unknown:
        findings.append(f"FAIL unknown filesystem keys {sorted(unknown)}")
    read_paths = _strings(fs.get("read"), "permissions.filesystem.read", findings)
    write_paths = _strings(fs.get("write"), "permissions.filesystem.write", findings)
    for p in read_paths + write_paths:
        bad = _bad_path(p)
        if bad:
            findings.append(f"FAIL filesystem: {bad}")

    net = perms.get("network", False)
    network, domains = False, ()
    if isinstance(net, bool):
        network = net
    elif isinstance(net, dict):
        unknown = set(map(str, net)) - _NET_KEYS
        if unknown:
            findings.append(f"FAIL unknown network keys {sorted(unknown)}")
        domains = _strings(net.get("domains"), "permissions.network.domains", findings)
        network = bool(domains)
    else:
        findings.append("FAIL permissions.network must be a bool or {domains: [...]}")

    commands = perms.get("commands", False)
    if not isinstance(commands, bool):
        findings.append("FAIL permissions.commands must be a bool")
        commands = False

    # -- the ceiling: a skill cannot ask above its risk class ----------------
    ceiling = RISK_CEILING[risk]
    from friday import capabilities as C
    for cid in capabilities:
        if any(ch in cid for ch in "*?["):
            continue                     # a glob is resolved per call, at permits()
        if C.by_id(cid) is None and cid not in _tool_categories():
            findings.append(f"WARN capability {cid!r} is not a registered Friday capability "
                            "(a Hermes/CLI tool name is fine; a typo is not)")
            continue
        tier = T.tier_of_tool(cid)
        if not _tier_le(tier, ceiling):
            findings.append(f"FAIL capability {cid!r} is {tier}; risk {risk} allows up to {ceiling}")
    if commands and not _tier_le(T.R2, ceiling):
        findings.append(f"FAIL commands: shell execution is {T.R2}; risk {risk} allows up to {ceiling}")

    status = FAIL if any(f.startswith("FAIL") for f in findings) else (WARN if findings else PASS)
    lint = Lint(status, tuple(findings))
    if status == FAIL:
        return None, lint
    return SkillManifest(risk=risk, capabilities=capabilities, prohibited=prohibited,
                         read_paths=read_paths, write_paths=write_paths,
                         network_domains=domains, network=network, commands=commands), lint


def _walk_tokens(node):
    """Every key and every string value, at any depth."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _walk_tokens(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _walk_tokens(v)
    elif isinstance(node, str):
        yield node


def _tool_categories() -> dict:
    from friday import policy as P
    return P.TOOL_CATEGORIES


# --------------------------------------------------------------------------
# authority: one layer, and the algebra
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Authority:
    """What one layer permits. `None` fields are 'no opinion' (the universe).

    Intersection can only narrow: id sets meet, deny sets join, tiers take
    the minimum, booleans AND. There is no operation that widens.
    """

    source: str
    capabilities: frozenset[str] | None = None   # None = no id restriction
    prohibited: frozenset[str] = frozenset()
    max_tier: str = T.R3                          # R4 is never permitted by anyone
    write_paths: tuple[str, ...] | None = None    # None = no path restriction
    network: bool = True
    commands: bool = True

    @classmethod
    def read_only(cls, source: str) -> "Authority":
        return cls(source=source, max_tier=T.R0, write_paths=(), commands=False)

    @classmethod
    def unrestricted(cls, source: str) -> "Authority":
        """Every layer but policy stands aside. R4 still never passes."""
        return cls(source=source)

    def intersect(self, other: "Authority") -> "Authority":
        if self.capabilities is None:
            caps = other.capabilities
        elif other.capabilities is None:
            caps = self.capabilities
        else:
            caps = _meet_globs(self.capabilities, other.capabilities)
        if self.write_paths is None:
            paths = other.write_paths
        elif other.write_paths is None:
            paths = self.write_paths
        else:
            paths = _meet_paths(self.write_paths, other.write_paths)
        return Authority(
            source=f"{self.source} ∩ {other.source}",
            capabilities=caps,
            prohibited=self.prohibited | other.prohibited,
            max_tier=_min_tier(self.max_tier, other.max_tier),
            write_paths=paths,
            network=self.network and other.network,
            commands=self.commands and other.commands,
        )

    def permits(self, capability_id: str) -> str | None:
        """None when permitted, else the reason - naming the layer stack."""
        if _matches(capability_id, self.prohibited):
            return f"{capability_id} is prohibited under {self.source}"
        if self.capabilities is not None and not _matches(capability_id, self.capabilities):
            return f"{capability_id} is outside the capabilities requested under {self.source}"
        tier = T.tier_of_tool(capability_id)
        if tier == T.R4 or not _tier_le(tier, self.max_tier):
            return f"{capability_id} is {tier}; {self.source} permits up to {self.max_tier}"
        from friday import capabilities as C
        from friday import policy as P
        cap = C.by_id(capability_id)
        if cap is not None:
            if cap.execution_scope == "network" and not self.network:
                return f"{capability_id} leaves the machine; {self.source} permits no network"
            if self.write_paths == () and cap.side_effect == "write" \
                    and cap.execution_scope != "network":
                return f"{capability_id} writes; {self.source} permits no writes"
        category = P.TOOL_CATEGORIES.get(capability_id) or \
            P.TOOL_CATEGORIES.get(capability_id.replace("_", ".", 1))
        if category == P.COMMAND_EXECUTION and not self.commands:
            return f"{capability_id} runs commands; {self.source} permits none"
        return None

    def to_dict(self) -> dict:
        return {"source": self.source,
                "capabilities": sorted(self.capabilities) if self.capabilities is not None else None,
                "prohibited": sorted(self.prohibited), "max_tier": self.max_tier,
                "write_paths": list(self.write_paths) if self.write_paths is not None else None,
                "network": self.network, "commands": self.commands}


def _matches(capability_id: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(capability_id, p) for p in patterns)


def _meet_globs(a: frozenset[str], b: frozenset[str]) -> frozenset[str]:
    """Entries of either side that the other side also admits.

    A concrete id survives when the other set matches it (by id or glob);
    a glob survives only when the other side carries the identical glob -
    two different globs have no computable meet without the registry, and
    the safe answer is the narrower one.
    """
    out = set()
    for x in a:
        if x in b or (not _is_glob(x) and _matches(x, b)):
            out.add(x)
    for y in b:
        if not _is_glob(y) and _matches(y, a):
            out.add(y)
    return frozenset(out)


def _is_glob(s: str) -> bool:
    return any(ch in s for ch in "*?[")


def _norm(p: str) -> str:
    return p.replace("\\", "/").rstrip("/")


def _meet_paths(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
    """Paths of each side that lie under some path of the other."""
    def under(p, roots):
        p = _norm(p)
        return any(p == _norm(r) or p.startswith(_norm(r) + "/") for r in roots)
    out = [p for p in a if under(p, b)] + [p for p in b if under(p, a)]
    return tuple(dict.fromkeys(out))


# --------------------------------------------------------------------------
# the Claude CLI boundary
# --------------------------------------------------------------------------

def narrow_profile(profile, authority: Authority):
    """A `brokers.Profile` with only what the authority still permits.

    The claude CLI enforces --allowedTools outside the model, so a tool that
    is not in the list cannot be called however the prompt reads. What the
    manifest withholds is withheld here: no write scope -> no Write/Edit;
    no commands -> no Bash/PowerShell entries; no network -> no web tools.
    """
    from friday.executors import brokers as B
    allowed = list(profile.allowed)
    if authority.write_paths == () or not _tier_le(T.R1, authority.max_tier):
        allowed = [t for t in allowed if t not in B.WRITE_TOOLS]
    if not authority.commands or not _tier_le(T.R2, authority.max_tier):
        allowed = [t for t in allowed if not t.startswith(("Bash(", "PowerShell("))]
    if not authority.network:
        allowed = [t for t in allowed if t not in ("WebFetch", "WebSearch")]
    return replace(profile, name=f"{profile.name}|{authority.source}", allowed=tuple(allowed))


# --------------------------------------------------------------------------
# the store: manifests live beside the skill they scope
# --------------------------------------------------------------------------

_TABLE = """
CREATE TABLE IF NOT EXISTS skill_manifests (
    skill        TEXT NOT NULL,
    version      INTEGER NOT NULL,
    manifest     TEXT NOT NULL,        -- canonical JSON of the parsed manifest
    risk         TEXT NOT NULL,
    lint         TEXT NOT NULL,        -- PASS | WARN, with findings
    recorded_at  REAL NOT NULL,
    PRIMARY KEY (skill, version)
)
"""


class SkillPermissions:
    """Manifests for skills on the ladder. One table, same database."""

    def __init__(self, ladder: SkillLadder) -> None:
        self.ladder = ladder
        with self.ladder._connect() as db:
            db.execute(_TABLE)

    def record(self, skill: str, source: str | dict) -> dict:
        """Lint and store the manifest for the skill's current version.

        A FAIL is refused and reported with every finding; nothing is stored,
        so the skill keeps the read-only default rather than a half-manifest.
        """
        cur = self.ladder.current(skill)
        if cur is None:
            raise KeyError(f"no such skill {skill!r}")
        manifest, lint = parse(source)
        if manifest is None:
            return {"status": "refused", "skill": skill, "lint": lint.status,
                    "findings": list(lint.findings)}
        with self.ladder._connect() as db:
            db.execute("INSERT OR REPLACE INTO skill_manifests"
                       " (skill, version, manifest, risk, lint, recorded_at)"
                       " VALUES (?,?,?,?,?,?)",
                       (skill, int(cur["version"]), json.dumps(manifest.to_dict(), sort_keys=True),
                        manifest.risk, json.dumps({"status": lint.status,
                                                   "findings": list(lint.findings)}),
                        time.time()))
        return {"status": "recorded", "skill": skill, "version": int(cur["version"]),
                "risk": manifest.risk, "ceiling": manifest.ceiling,
                "lint": lint.status, "findings": list(lint.findings)}

    def manifest_for(self, skill: str) -> SkillManifest | None:
        cur = self.ladder.current(skill)
        if cur is None:
            return None
        with self.ladder._connect() as db:
            row = db.execute("SELECT manifest FROM skill_manifests WHERE skill = ? AND version = ?",
                             (skill, int(cur["version"]))).fetchone()
        if not row:
            return None
        manifest, _ = parse(json.loads(row["manifest"]))
        return manifest

    def authority_for(self, skill: str) -> Authority:
        """The skill's requested scope - READ-ONLY when it declared nothing."""
        manifest = self.manifest_for(skill)
        if manifest is None:
            return Authority.read_only(f"skill:{skill} (no manifest: read-only)")
        return manifest.authority(skill)

    def prohibited_by(self, skills) -> tuple[str, ...]:
        """The union of what the named skills forswear - for a worker that
        may follow any of them, the deny set is the one thing that composes
        safely across skills."""
        out: list[str] = []
        for name in skills:
            m = self.manifest_for(name)
            if m:
                out.extend(p for p in m.prohibited if p not in out)
        return tuple(out)
