"""Long-run context-health benchmark (CTX-004; also the context evidence for EVAL-002).

Replays a few hundred synthetic turns into a session context and shows that
`friday.context_health` does what PRD v7 19.5 asks of it:

  baseline    the same turns with nobody acting: the context rots, and the
              states appear in order, HEALTHY, NOISY, DEGRADED, ROTTING.
  controlled  the same turns with `context_health.remediate` run each turn:
              duplicates, stale turns, open contradictions, large raw outputs, a
              bulky-section burst and a runaway duplicate burst arrive, and every
              action that fires leaves the context in a better state, and the
              rules, preferences and open contradictions are never lost.
  bounded     the real `memory_stack.aggregate` over a throwaway store, one
              unique turn at a time: a context that is bounded by construction
              stays HEALTHY and nothing acts on it.

It then runs the Context Lab (`friday.context_lab`, PRD v7 19.11, EVAL-002):
every listed scenario against the full-history baseline, with the ones that have
nothing to run against reported as unexercised. Their summary is the same dict
the golden release report carries as `context`.

It never reads or writes data/ada.sqlite3: the bounded run points ADA_DB at a
temp file. Exit status is 0 only when every check passes. Evidence is written to
`--out` (default data/golden_context.json).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from friday import compaction  # noqa: E402
from friday import compression  # noqa: E402
from friday import context_health as H  # noqa: E402
from friday import context_lab  # noqa: E402

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
FRESH = (NOW - timedelta(days=1)).isoformat()
OLD = (NOW - timedelta(days=30)).isoformat()
BUDGET = 900
TURNS = 400
BOUNDED_TURNS = 150
TOPICS = ("the cutover", "the backup job", "invoice sync", "the demo deck", "flight to pune",
          "the staging box", "mum's birthday", "the Hermes retry", "calendar clean-up")


def tokens(text):
    return max(1, len(text) // 4)


def entry(text, tier, ts=None, kind="", score=None):
    out = {"tier": tier, "text": text, "ts": ts, "kind": kind}
    if score is not None:
        out["score"] = score
    return out


def head(text):
    return {"tier": None, "text": text}


def severity(state):
    return H.STATES.index(state)


# --- the turns --------------------------------------------------------------

def build_script(turns=TURNS, seed=7):
    """What arrives each turn. Independent of the context, so the baseline and
    the controlled run see exactly the same input."""
    rng = random.Random(seed)
    said, script = [], []
    for t in range(turns):
        add, resolve = [], []
        text = "- he: turn %d, %s, %s" % (t, rng.choice(TOPICS), rng.choice(TOPICS))
        add.append(entry(text, "episodes", FRESH))
        if 60 <= t < 200 and t % 2 == 0 and said:               # growing repeats
            add.append(entry(rng.choice(said), "episodes", FRESH))
        if 100 <= t < 220 and t % 3 == 0:                       # talk from a month ago
            add.append(entry("- he: old thread %d about %s" % (t, rng.choice(TOPICS)),
                             "episodes", OLD))
        if t in (230, 260, 290):                                # a clash is spotted ...
            add.append(entry("- OPEN CONTRADICTION on setting-%d: 'a' vs 'b'" % t, "relations",
                             kind=H.CONTRADICTION))
        if t - 40 in (230, 260, 290):                           # ... and later answered
            resolve.append("- OPEN CONTRADICTION on setting-%d: 'a' vs 'b'" % (t - 40))
        if t >= 320 and t % 10 == 0:                            # a tool dumps its output
            add.append(entry("OUTPUT %d: %s" % (t, "line of build log " * 90), "outcomes"))
        if t % 7 == 3:                                          # work is attempted; a run of tries fails
            failed = 300 <= t < 345 or t % 4 == 0
            add.append(entry("- attempt %d: %s -> %s" % (t, rng.choice(TOPICS),
                                                         "failed" if failed else "done"),
                             "outcomes", kind=H.FAILED_ATTEMPT if failed else H.ATTEMPT))
        if t == 150:                                            # one section balloons
            add += [entry("- [spec%d] %s" % (i, "requirement detail " * 22), "specs",
                          score=(i + 1) / 10)                  # each a retrieval hit with a score
                    for i in range(10)]
        if t == 250:                                            # a stutter
            add += [entry(text, "episodes", FRESH) for _ in range(45)]
        if t == 350:                                            # a tool loop repeats itself
            add += [entry(text, "episodes", FRESH) for _ in range(80)]
        said.append(text)
        script.append({"add": add, "resolve": resolve})
    return script


class Session:
    """A session context: rules and preferences from the start, then whatever arrives."""

    def __init__(self):
        prefs = [entry("- preference %d: short answers, no filler, never read a URL aloud" % i,
                       "preferences") for i in range(4)]
        rules = [entry("- rule %d: never touch data/ada.sqlite3 and never push without being asked"
                       % i, "rules") for i in range(6)]
        self.ledger = {e["text"]: e for e in prefs + rules}   # what must never be lost
        self.layout = ([head("YOUR PREFERENCES AND RULES:")] + prefs
                       + [head("RULES OF ENGAGEMENT (git-tracked):")] + rules
                       + [head("WHAT WAS SAID BEFORE (most recent last):")])

    def arrive(self, step):
        for text in step["resolve"]:
            self.ledger.pop(text, None)
            self.layout = [e for e in self.layout if e["text"] != text]
        for e in step["add"]:
            self.layout.append(dict(e))
            if H._critical(e):
                self.ledger[e["text"]] = e

    def agg(self):
        used = sum(tokens(e["text"]) for e in self.layout if e.get("tier"))
        return {"task": "", "budget_tokens": BUDGET, "tokens_used": used, "layout": self.layout,
                "prompt": "\n".join(e["text"] for e in self.layout)}

    def facts(self):
        rules = tuple(t for t, e in self.ledger.items() if e["tier"] in H.CRITICAL_TIERS)
        open_ = tuple(t for t, e in self.ledger.items() if e.get("kind") == H.CONTRADICTION)
        return compaction.CriticalFactSet(constraints=rules, questions=open_)


def point(turn, verdict):
    m = verdict["metrics"]
    return {"turn": turn, "state": verdict["state"], "tokens": m["tokens"],
            "utilization": m["utilization"], "duplicate_ratio": m["duplicate_ratio"],
            "stale_ratio": m["stale_ratio"], "contradiction_score": m["contradiction_score"],
            "raw_output_ratio": m["raw_output_ratio"],
            "failed_attempt_density": m["failed_attempt_density"],
            "struggle_score": m["struggle_score"],
            "retrieval_confidence": m["retrieval_confidence"], "drivers": verdict["drivers"]}


def restart_recovers(session_before, recovered):
    """True when the text recovered from the vault is exactly the distinct
    non-critical lines the session held before the reset, in order."""
    held = list(dict.fromkeys(e["text"] for e in session_before
                              if e.get("tier") and not H._critical(e)))
    return bool(held) and recovered == "\n".join(held)


def replay(script, act):
    session, series = Session(), []
    for turn, step in enumerate(script):
        session.arrive(step)
        agg = session.agg()
        verdict = H.assess(agg, NOW)
        row = point(turn, verdict)
        row.update(action=verdict["action"], applied=H.NONE, after=None, after_action=None,
                   fresh=None, fresh_action=None, restart_recovers=None, critical_lost=[],
                   refused=[])
        if act and verdict["action"] != H.NONE:
            session_before = [e for e in session.layout if e.get("tier")]
            agg2, outcome = H.remediate(agg, NOW, reset=True)
            row.update(applied=outcome["applied"], after=outcome.get("after"),
                       after_action=outcome.get("after_action"),
                       refused=outcome.get("refused", []))
            session.layout = agg2["layout"]
            if outcome["applied"] == H.HANDOFF:
                # The session restarts on the returned context; what it let go of
                # must come back from the vault, every line of it.
                old = compression.recover(outcome["restart"]["recovery"]).decode()
                row.update(fresh=outcome["after"], fresh_action=outcome["after_action"],
                           restart_recovers=restart_recovers(session_before, old))
        end = H.assess(session.agg(), NOW)
        row.update(end_state=end["state"], end_action=end["action"],
                   end_awaiting_person=end["awaiting_person"])
        row["critical_lost"] = compaction.missing(session.agg()["prompt"], session.facts())
        series.append(row)
    return series


# --- the bounded run, on the real aggregate ---------------------------------

def bounded_run(turns=BOUNDED_TURNS):
    """Real `memory_stack.aggregate` over a temp store, one unique turn at a time."""
    tmp = tempfile.mkdtemp(prefix="golden_context_")
    previous = os.environ.get("ADA_DB")
    os.environ["ADA_DB"] = os.path.join(tmp, "bounded.sqlite3")
    from friday import memory_stack
    from friday.toolsets import memory as M
    M.reset_store(None)
    series = []
    try:
        store = M.store()
        rng = random.Random(11)
        for t in range(turns):
            store.add_message("bench", "user", "turn %d: %s and %s, ref %06d" % (
                t, rng.choice(TOPICS), rng.choice(TOPICS), rng.randrange(10 ** 6)))
            agg = memory_stack.aggregate("what did we say about %s" % rng.choice(TOPICS),
                                         budget_tokens=BUDGET)
            _, verdict = H.remediate(agg)
            series.append({"turn": t, "state": agg["health"]["state"], "applied": verdict["applied"],
                           "tokens": agg["tokens_used"], "entries": agg["health"]["metrics"]["entries"],
                           "utilization": agg["health"]["metrics"]["utilization"]})
    finally:
        M.reset_store(None)
        if previous is None:
            os.environ.pop("ADA_DB", None)
        else:
            os.environ["ADA_DB"] = previous
        shutil.rmtree(tmp, ignore_errors=True)
    return series


# --- the checks -------------------------------------------------------------

def check(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": detail}


def controlled_checks(controlled, baseline=None):
    """The checks that need only the controlled run, so a short replay can prove
    them alone (tests/test_context_health.py). The comparison with the baseline
    is made only when one is given."""
    out = []
    acted = [r for r in controlled if r["action"] != H.NONE]
    seen_states = sorted({r["state"] for r in controlled}, key=severity)
    out.append(check("controlled_run_meets_every_state",
                     seen_states == list(H.STATES), {"states_seen": seen_states}))
    kinds = sorted({r["action"] for r in acted})
    out.append(check("every_response_fires",
                     set(kinds) == {H.PRUNE, H.SUMMARIZE, H.FOLD, H.HANDOFF},
                     {"actions": kinds, "count": len(acted)}))
    # Better: nothing an action can clear is left (the after-context calls for
    # no further action) and the state is no worse. A hand-off is judged on the
    # fresh session it starts, which must be out of ROTTING.
    not_better = [r["turn"] for r in acted if r["refused"] or (
        r["fresh"] is not None and (r["fresh_action"] != H.NONE
                                    or severity(r["fresh"]) >= severity(H.ROTTING))) or (
        r["fresh"] is None and (r["after_action"] != H.NONE
                                or severity(r["after"]) > severity(r["state"])))]
    out.append(check("every_action_leaves_a_better_state", not not_better,
                     {"actions": len(acted), "not_better_at_turns": not_better}))
    reset = [r for r in controlled if r["action"] == H.HANDOFF]
    out.append(check("reset_restarts_from_a_recoverable_branch",
                     bool(reset) and all(r["applied"] == H.HANDOFF and r["restart_recovers"]
                                         for r in reset),
                     {"resets": [{"turn": r["turn"], "applied": r["applied"],
                                  "fresh": r["fresh"], "old_context_recovers": r["restart_recovers"]}
                                 for r in reset]}))
    measured = {}
    for name in H.NEEDS_SOURCE:
        values = [r[name] for r in controlled if r[name] is not None]
        measured[name] = {"turns_measured": len(values), "peak": max(values) if values else None}
    out.append(check("every_section_19_5_metric_is_measured",
                     all(m["turns_measured"] and m["peak"] > 0 for m in measured.values()),
                     measured))
    lost = [(r["turn"], r["critical_lost"]) for r in controlled if r["critical_lost"]]
    out.append(check("critical_facts_survive_every_turn", not lost,
                     {"turns_checked": len(controlled), "turns_that_lost_a_fact": lost[:5]}))
    # A turn may end NOISY only for a reason the person has to resolve (an open
    # contradiction); it never ends DEGRADED or ROTTING, and the baseline does.
    over = [r["turn"] for r in controlled if severity(r["end_state"]) > severity(H.NOISY)]
    unexplained = [r["turn"] for r in controlled if r["end_state"] != H.HEALTHY
                   and not r["end_awaiting_person"]]
    healthy = sum(r["end_state"] == H.HEALTHY for r in controlled) / len(controlled)
    detail = {"controlled_turns_above_noisy": len(over),
              "controlled_noisy_without_an_open_contradiction": unexplained[:5],
              "controlled_healthy_share": round(healthy, 4),
              "controlled_peak_tokens": max(r["tokens"] for r in controlled)}
    worse = True
    if baseline:
        base_over = sum(severity(r["end_state"]) > severity(H.NOISY) for r in baseline) / len(baseline)
        worse = base_over > 0.5
        detail.update(baseline_share_of_turns_above_noisy=round(base_over, 4),
                      baseline_peak_tokens=max(r["tokens"] for r in baseline))
    out.append(check("acting_keeps_the_run_out_of_trouble",
                     not over and not unexplained and worse, detail))
    return out


def checks(baseline, controlled, bounded):
    base = [r["state"] for r in baseline]
    ranks = [severity(s) for s in base]
    first = []
    for s in base:
        if s not in first:
            first.append(s)
    out = [check("baseline_states_appear_in_order",
                 first == list(H.STATES) and ranks == sorted(ranks),
                 {"first_seen": first, "turn_of_first": {
                     s: next(r["turn"] for r in baseline if r["state"] == s) for s in first}})]
    out += controlled_checks(controlled, baseline)
    states = sorted({r["state"] for r in bounded}, key=severity)
    out.append(check("bounded_run_stays_healthy",
                     states == [H.HEALTHY] and all(r["applied"] == H.NONE for r in bounded),
                     {"turns": len(bounded), "states": states,
                      "peak_utilization": max(r["utilization"] for r in bounded)}))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "data" / "golden_context.json"))
    parser.add_argument("--turns", type=int, default=TURNS)
    args = parser.parse_args(argv)
    script = build_script(args.turns)
    baseline, controlled, bounded = replay(script, False), replay(script, True), bounded_run()
    results = checks(baseline, controlled, bounded)
    lab = context_lab.run()
    lab_summary = context_lab.summarize(lab)
    evidence = {"requirement": "CTX-004", "also": "EVAL-002", "turns": args.turns, "budget_tokens": BUDGET,
                "thresholds": H.thresholds(), "checks": results,
                "baseline": baseline, "controlled": controlled, "bounded": bounded,
                "context_lab": {"summary": lab_summary, "scenarios": lab}}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=1), encoding="utf-8")
    for r in results:
        sys.stdout.write("%s  %s  %s\n" % ("PASS" if r["ok"] else "FAIL", r["name"],
                                           json.dumps(r["detail"])))
    for r in lab:
        verdict = "SKIP" if not r["exercised"] else "PASS" if r["ok"] else "FAIL"
        sys.stdout.write("%s  lab: %s  %s\n" % (verdict, r["scenario"], r.get("reason", "")))
    sys.stdout.write("context lab: %d of %d scenarios exercised, %d failed\n" % (
        lab_summary["exercised"], lab_summary["scenarios"], lab_summary["failed"]))
    sys.stdout.write("evidence: %s\n" % out)
    return 0 if all(r["ok"] for r in results) and not lab_summary["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
