"""
friday skills - the owner's console for the skill layer.

    python -m friday.skills.cli doctor                     health of every tier, exit 0/1/2
    python -m friday.skills.cli sync                       register skills found in the tiers
    python -m friday.skills.cli list [--state ACTIVE]
    python -m friday.skills.cli lint NAME [--publication]
    python -m friday.skills.cli activate NAME [--no-run]   evaluate, then activate on evidence
    python -m friday.skills.cli revalidate [--since REV]   code change -> affected skills -> re-test
    python -m friday.skills.cli rollback NAME [--to VER]
    python -m friday.skills.cli publish NAME --target LOCAL|PRIVATE_GIT|PUBLIC_GIT
    python -m friday.skills.cli select "QUERY"             what the router would load, and its cost
    python -m friday.skills.cli hermes-link [--apply]      point Hermes at FRIDAY's skill dirs

From the packaged build: FRIDAY.exe -m friday.skills.cli ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from friday.skills import contracts as K
from friday.skills import files as F
from friday.skills import gc as G
from friday.skills import hermes_link as HL
from friday.skills import lint as L
from friday.skills import publisher as P
from friday.skills.dependencies import SkillDependencyGraph
from friday.skills.evaluator import SkillEvaluator
from friday.skills.lifecycle import SkillLifecycle
from friday.skills.registry import SkillRegistry
from friday.skills.router import SkillRouter

TRUST_BY_TIER = {K.BUILT_IN: K.TRUST_BUILT_IN, K.PROJECT_TIER: K.TRUST_OWNER, K.LOCAL_USER: K.TRUST_AGENT,
                 K.SHARED_PRIVATE: K.TRUST_OWNER, K.PUBLISHED_PUBLIC: K.TRUST_COMMUNITY}


class Services:
    """Everything wired once, the way the roles wire it."""

    def __init__(self, db_path: str | None = None, roots: dict[str, Path] | None = None) -> None:
        self.registry = SkillRegistry(db_path)
        self.roots = roots if roots is not None else F.tier_roots()
        self.graph = SkillDependencyGraph(self.registry)
        self.evaluator = SkillEvaluator(self.registry)
        self.lifecycle = SkillLifecycle(self.registry, evaluator=self.evaluator, graph=self.graph)
        self.router = SkillRouter(self.registry, self.roots)
        self.publisher = P.SkillPublisher(self.registry, roots=self.roots)

    def discover(self) -> list[tuple[str, F.SkillFiles]]:
        found = []
        for tier, root in self.roots.items():
            for skill in F.discover(root):
                found.append((tier, skill))
        return found


def _register(services: Services, tier: str, skill: F.SkillFiles, known, actor: str):
    return services.lifecycle.register(
        skill, tier=known.tier if known else tier,
        trust=known.trust if known else TRUST_BY_TIER.get(tier, K.TRUST_AGENT),
        source=known.source if known else f"sync:{tier}", actor=actor)


def register_unregistered(services: Services, actor: str = "startup") -> list[tuple[str, str, str]]:
    """Register every on-disk skill the registry does not know yet, as
    (name, tier, state). A skill already registered is left alone: re-reading
    one can drop an ACTIVE skill to DRAFT, and that is `sync`'s call to make,
    not something a launch should do to the owner's skills."""
    registered = []
    for tier, skill in services.discover():
        if services.registry.get(skill.name) is None:
            record = _register(services, tier, skill, None, actor)
            registered.append((skill.name, tier, record.state))
    return registered


def cmd_sync(services: Services, args) -> int:
    """Register what is on disk; re-read what is already registered (an
    ACTIVE skill whose file changed drops to DRAFT until re-evaluated)."""
    added = refreshed = 0
    for tier, skill in services.discover():
        known = services.registry.get(skill.name)
        record = _register(services, tier, skill, known, "cli")
        if known is None:
            print(f"  registered {skill.name:<40} {tier:<16} {record.state}")
            added += 1
        else:
            refreshed += 1
            if record.state != known.state:
                print(f"  {skill.name:<40} {known.state} -> {record.state} (file changed)")
    print(f"{added} skill(s) registered, {refreshed} refreshed")
    return 0


def cmd_list(services: Services, args) -> int:
    rows = services.registry.list(state=args.state)
    if not rows:
        print("no skills registered (run `sync`)")
        return 0
    width = max(len(r.name) for r in rows)
    for r in rows:
        print(f"  {r.name:<{width}}  {r.version:<8} {r.state:<19} {r.scope:<12} {r.tier:<16} "
              f"q={r.quality_score:<5} risk={r.risk:<18} verified={r.last_verified or '-'}")
    return 0


def cmd_lint(services: Services, args) -> int:
    record = services.registry.get(args.name)
    skill = F.SkillFiles(Path(record.path)) if record else F.SkillFiles(Path(args.name))
    report = L.lint(skill, for_publication=args.publication)
    for finding in report.findings:
        print(f"  {finding.level:<5} {finding.code:<22} {finding.detail}  [{finding.file}]")
    print(f"lint: {report.verdict}")
    return {L.PASS: 0, L.WARN: 0, L.FAIL: 2}[report.verdict]


def cmd_activate(services: Services, args) -> int:
    record = services.registry.get(args.name)
    if record is None:
        print(f"no skill named {args.name!r}; run `sync` first", file=sys.stderr)
        return 2
    if record.state in (K.DRAFT, K.QUARANTINED):
        try:
            services.lifecycle.stage(args.name, actor="cli")
        except K.TransitionRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    report, record = services.lifecycle.evaluate_and_activate(args.name, run_tests=not args.no_run, actor="cli")
    for name, state, detail in report.checks:
        print(f"  {state:<5} {name:<12} {detail}")
    print(f"{args.name}: {record.state} (version {record.version}, quality {record.quality_score})")
    return 0 if report.passed else 1


def cmd_revalidate(services: Services, args) -> int:
    if args.name:
        outcome, report = services.lifecycle.revalidate(args.name, run_tests=not args.no_run, actor="cli")
        print(f"{args.name}: {outcome}")
        return 0 if outcome in ("FRESH", K.ACTIVE) else 1
    changed = services.graph.changed_paths_since(args.since)
    marked = services.graph.revalidate_after_change(changed, actor="cli")
    print(f"{len(changed)} changed path(s) since {args.since}; {len(marked)} skill(s) affected")
    worst = 0
    for entry in marked:
        outcome, _ = services.lifecycle.revalidate(entry["skill"], run_tests=not args.no_run, actor="cli")
        print(f"  {entry['skill']}: {outcome}  ({', '.join(entry['changed'][:3])})")
        worst = max(worst, 0 if outcome == "FRESH" else 1)
    return worst


def cmd_rollback(services: Services, args) -> int:
    record = services.lifecycle.rollback(args.name, actor="cli", reason=args.reason or "owner request", to_version=args.to)
    print(f"{args.name}: {record.state} at {record.version}")
    return 0


def cmd_publish(services: Services, args) -> int:
    result = services.publisher.publish(args.name, args.target, actor="cli")
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.ok else 1


def cmd_select(services: Services, args) -> int:
    selection = services.router.select(args.query, limit=args.limit)
    print(json.dumps(selection.to_dict(), indent=2))
    return 0


def cmd_hermes_link(services: Services, args) -> int:
    home = HL.hermes_home()
    if home is None:
        print("no Hermes home found (HERMES_HOME, %LOCALAPPDATA%\\hermes, ~/.hermes)")
        return 1
    config = home / "config.yaml"
    dirs = HL.desired_dirs(services.roots)
    result = HL.apply(config, dirs) if args.apply else HL.plan(config, dirs)
    print(json.dumps(result, indent=2))
    return 0


def cmd_propose(services: Services, args) -> int:
    """From remembered procedure statements to a curated DRAFT skill in the
    user tier: bridge -> candidate detector -> curator -> register."""
    from friday.skills import bridge as B
    from friday.skills import curator as CU

    statements = list(args.statement or [])
    if args.file:
        statements += [line.strip() for line in Path(args.file).read_text(encoding="utf-8").splitlines() if line.strip()]
    proposal = B.proposal_from_memories(statements, source=args.source or "memory")
    if proposal is None:
        print("no skill: the statements do not form a repeated procedure (need 3+ steps on one subject)")
        return 1
    curated = CU.SkillCurator(services.registry).curate(proposal)
    print(json.dumps({"proposal": proposal.name, "criteria": proposal.criteria, "curation": curated.to_dict()}, indent=2))
    if curated.action == "reject":
        return 2
    if curated.action == "merge":
        print(f"merge into {curated.target} instead of a new skill (use `lint`/`activate` after patching it)")
        return 1
    root = services.roots.get(K.LOCAL_USER)
    if root is None:
        print("no LOCAL_USER tier configured", file=sys.stderr)
        return 2
    skill = F.SkillFiles(root / proposal.name)
    skill.write_text(curated.content)
    for relative, content in curated.extra_files.items():
        skill.write_file(relative, content)
    record = services.lifecycle.register(skill, tier=K.LOCAL_USER, trust=K.TRUST_AGENT,
                                         source=proposal.source, actor="cli")
    print(f"wrote {skill.skill_md} ({record.state}); review it, then `activate {proposal.name}`")
    return 0


def cmd_rescore(services: Services, args) -> int:
    """Recompute quality from recorded outcomes; DEGRADE and roll back an
    ACTIVE skill that fell below the floor."""
    names = [args.name] if args.name else [r.name for r in services.registry.list(state=K.ACTIVE)]
    for name in names:
        record = services.lifecycle.degrade_if_needed(name, auto_rollback=not args.no_rollback, actor="cli")
        print(f"  {name:<40} quality={record.quality_score:<5} {record.state} v{record.version}")
    return 0


def cmd_gc(services: Services, args) -> int:
    """Archive stale duplicate skills (DEPRECATED, kept for history)."""
    report = G.collect(services.registry, apply=not args.dry_run, actor="cli")
    verb = "would archive" if args.dry_run else "archived"
    for item in report["archived"]:
        print(f"  {verb} {item['name']:<40} duplicate of {item['duplicate_of']} (idle {item['idle_days']} days)")
    if not report["archived"]:
        print("  nothing to archive")
    return 0


def cmd_hints(services: Services, args) -> int:
    """The names Hermes should load for a request (TaskBundle.skill_hints)."""
    print(json.dumps(services.router.hints_for(args.query, limit=args.limit)))
    return 0


def cmd_facts(services: Services, args) -> int:
    """Hand a skill's `## Facts` bullets to memory promotion."""
    from friday.skills import bridge as B

    record = services.registry.get(args.name)
    if record is None:
        print(f"no skill named {args.name!r}", file=sys.stderr)
        return 2
    decisions = B.promote_facts(F.SkillFiles(Path(record.path)))
    for decision in decisions:
        print(f"  {'accepted' if decision.accepted else 'refused':<8} {decision.target:<8} {decision.reason}")
    print(f"{sum(1 for d in decisions if d.accepted)}/{len(decisions)} facts accepted into memory")
    return 0


def cmd_outcome(services: Services, args) -> int:
    """Record how a use of a skill went; quality follows from these rows."""
    services.registry.record_usage(args.name, args.outcome, latency_ms=args.latency_ms, cost=args.cost,
                                   objective=args.objective, version=(services.registry.get(args.name) or K.SkillRecord(args.name)).version)
    score = services.evaluator.rescore(args.name)
    print(f"{args.name}: {args.outcome} recorded; quality now {score}")
    return 0


def cmd_usage(services: Services, args) -> int:
    record = services.registry.get(args.name)
    if record is None:
        print(f"no skill named {args.name!r}", file=sys.stderr)
        return 2
    print(json.dumps({"stats": services.registry.stats(args.name),
                      "recent": services.registry.usage(args.name, limit=10)}, indent=2))
    return 0


def cmd_diff(services: Services, args) -> int:
    """What changed on disk since the last verified version - read before
    activating a sensitive upgrade."""
    import difflib

    record = services.registry.get(args.name)
    if record is None:
        print(f"no skill named {args.name!r}", file=sys.stderr)
        return 2
    verified = services.registry.last_verified_version(args.name)
    if verified is None or not verified.get("snapshot_path"):
        print("no verified version to compare with")
        return 1
    old = (Path(verified["snapshot_path"]) / F.SKILL_FILE).read_text(encoding="utf-8", errors="replace").splitlines()
    new = F.SkillFiles(Path(record.path)).text().splitlines()
    lines = list(difflib.unified_diff(old, new, fromfile=f"{args.name}@{verified['version']} (verified)",
                                      tofile=f"{args.name} (on disk)", lineterm=""))
    print("\n".join(lines) if lines else "identical to the verified version")
    return 0


def cmd_builtins(services: Services, args) -> int:
    root = services.roots.get(K.BUILT_IN)
    shipped = F.discover(root) if root else []
    adopted = {u["skill"]: u for u in services.lifecycle.builtin_updates(roots=services.roots)}
    for skill in shipped:
        status = adopted.get(skill.name)
        if status is None:
            print(f"  {skill.name:<32} shipped, not adopted (adopt {skill.name})")
        else:
            print(f"  {skill.name:<32} adopted: {status['state']} v{status['version']}"
                  f"{'; shipped version changed' if status['upstream_changed'] else ''}"
                  f"{'; edited locally' if status['locally_modified'] else ''}")
    print(f"{len(shipped)} built-in skill(s)")
    return 0


def cmd_adopt(services: Services, args) -> int:
    record = services.lifecycle.adopt_builtin(args.name, roots=services.roots, actor="cli")
    print(f"{args.name}: copied into the user tier ({record.state}); `activate {args.name}` when ready")
    return 0


def cmd_upgrade_builtin(services: Services, args) -> int:
    result = services.lifecycle.upgrade_builtin(args.name, roots=services.roots, run_tests=not args.no_run, actor="cli")
    print(json.dumps(result, indent=2))
    return 0 if result.get("outcome") in ("activated", "current") else 1


def cmd_doctor(services: Services, args) -> int:
    findings: list[tuple[str, str]] = []
    discovered = services.discover()
    registered = {r.name: r for r in services.registry.list()}
    for tier, skill in discovered:
        report = L.lint(skill)
        if report.verdict == L.FAIL:
            findings.append(("FAIL", f"{skill.name} ({tier}): lint " + ", ".join(sorted({f.code for f in report.failures()}))))
        if skill.name not in registered:
            findings.append(("WARN", f"{skill.name} ({tier}): on disk but not registered (run `sync`)"))
    for name, record in registered.items():
        if not Path(record.path, F.SKILL_FILE).is_file():
            findings.append(("FAIL", f"{name}: registered at {record.path} but the file is gone"))
            continue
        if record.state == K.ACTIVE:
            check = services.graph.check(name)
            if not check["fresh"]:
                findings.append(("WARN", f"{name}: dependencies changed ({', '.join((check['changed'] + check['missing'])[:3])}); run `revalidate`"))
        if record.state in (K.QUARANTINED, K.BROKEN):
            findings.append(("WARN", f"{name}: {record.state}"))
        if record.state == K.ACTIVE and not record.tests:
            findings.append(("WARN", f"{name}: active without tests"))
    selectable = services.router.candidates()
    tasks: dict[str, list[str]] = {}
    for record in selectable:
        if record.task:
            tasks.setdefault(record.task, []).append(record.name)
    for task, names in tasks.items():
        if len(names) > 1:
            findings.append(("WARN", f"task {task!r} is claimed by {', '.join(names)}"))
    for level, text in findings:
        print(f"  {level:<5} {text}")
    counts = {}
    for record in registered.values():
        counts[record.state] = counts.get(record.state, 0) + 1
    print(f"{len(discovered)} on disk, {len(registered)} registered: "
          + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    verdict = "FAIL" if any(l == "FAIL" for l, _ in findings) else "DEGRADED" if findings else "PASS"
    print(f"skills doctor: {verdict}")
    return {"PASS": 0, "DEGRADED": 1, "FAIL": 2}[verdict]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="friday skills", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None, help="registry database (default: Friday's store)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    sub.add_parser("sync").set_defaults(func=cmd_sync)
    p = sub.add_parser("list"); p.add_argument("--state", default=None); p.set_defaults(func=cmd_list)
    p = sub.add_parser("lint"); p.add_argument("name"); p.add_argument("--publication", action="store_true"); p.set_defaults(func=cmd_lint)
    p = sub.add_parser("activate"); p.add_argument("name"); p.add_argument("--no-run", action="store_true"); p.set_defaults(func=cmd_activate)
    p = sub.add_parser("revalidate"); p.add_argument("name", nargs="?", default=""); p.add_argument("--since", default="HEAD"); p.add_argument("--no-run", action="store_true"); p.set_defaults(func=cmd_revalidate)
    p = sub.add_parser("rollback"); p.add_argument("name"); p.add_argument("--to", default=None); p.add_argument("--reason", default=""); p.set_defaults(func=cmd_rollback)
    p = sub.add_parser("publish"); p.add_argument("name"); p.add_argument("--target", choices=P.TARGETS, required=True); p.set_defaults(func=cmd_publish)
    p = sub.add_parser("select"); p.add_argument("query"); p.add_argument("--limit", type=int, default=3); p.set_defaults(func=cmd_select)
    p = sub.add_parser("hermes-link"); p.add_argument("--apply", action="store_true"); p.set_defaults(func=cmd_hermes_link)
    p = sub.add_parser("propose", help="a DRAFT skill from remembered procedure statements")
    p.add_argument("statement", nargs="*"); p.add_argument("--file", default=""); p.add_argument("--source", default="")
    p.set_defaults(func=cmd_propose)
    p = sub.add_parser("rescore", help="recompute quality; degrade and roll back below the floor")
    p.add_argument("name", nargs="?", default=""); p.add_argument("--no-rollback", action="store_true")
    p.set_defaults(func=cmd_rescore)
    p = sub.add_parser("gc", help="archive stale duplicate skills")
    p.add_argument("--dry-run", action="store_true"); p.set_defaults(func=cmd_gc)
    p = sub.add_parser("hints", help="skill names Hermes should load for a request")
    p.add_argument("query"); p.add_argument("--limit", type=int, default=3); p.set_defaults(func=cmd_hints)
    p = sub.add_parser("facts", help="promote a skill's ## Facts bullets into memory")
    p.add_argument("name"); p.set_defaults(func=cmd_facts)
    p = sub.add_parser("outcome", help="record how a use of a skill went")
    p.add_argument("name"); p.add_argument("outcome", choices=("success", "failure", "corrected"))
    p.add_argument("--latency-ms", type=int, default=0); p.add_argument("--cost", type=float, default=0.0)
    p.add_argument("--objective", default=""); p.set_defaults(func=cmd_outcome)
    p = sub.add_parser("usage", help="rates, averages and recent uses of a skill")
    p.add_argument("name"); p.set_defaults(func=cmd_usage)
    p = sub.add_parser("diff", help="what changed on disk since the last verified version")
    p.add_argument("name"); p.set_defaults(func=cmd_diff)
    sub.add_parser("builtins", help="shipped skills and their adopted copies").set_defaults(func=cmd_builtins)
    p = sub.add_parser("adopt", help="copy a shipped skill into the user tier")
    p.add_argument("name"); p.set_defaults(func=cmd_adopt)
    p = sub.add_parser("upgrade-builtin", help="bring an adopted copy up to the shipped version, merging local edits")
    p.add_argument("name"); p.add_argument("--no-run", action="store_true"); p.set_defaults(func=cmd_upgrade_builtin)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    services = Services(args.db)
    try:
        return int(args.func(services, args) or 0)
    except K.SkillError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
