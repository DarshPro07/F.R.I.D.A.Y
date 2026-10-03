"""
Adjudicate ambiguous benchmark labels by a written rule (CTX-002).

Run:  python scripts/label_adjudication.py          writes the overlay
      python scripts/label_adjudication.py --check  fails if the overlay is stale

The rule is in docs/plans/ctx-002/PLAN.md. In short: two tools are substitutes
for a request when each one's registered description fully carries out what the
request literally asks and the text has no cue only one of them satisfies; a
bare imperative whose only object is a pronoun, with no turns or context to
resolve it, is a CLARIFY request. Nothing here is read from a retriever or a
model, and the same rule runs over development and holdout unchanged.

The corpus files under data/ are not edited and every original label stays.
The overlay, docs/plans/ctx-002/label_overlay.json, adds `also_correct` tools
and `clarify` markers, each with a one-line reason; the benchmark reads it on
top of the corpus.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "corpus"
OVERLAY = ROOT / "docs" / "plans" / "ctx-002" / "label_overlay.json"


@dataclass(frozen=True)
class Substitution:
    key: str
    members: frozenset[str]
    reason: str
    blockers: str = ""        # a cue only one member satisfies: the set does not apply
    requires: str = ""        # a cue the request must carry for the set to apply

    def applies(self, text: str) -> bool:
        if self.blockers and re.search(self.blockers, text):
            return False
        return not self.requires or bool(re.search(self.requires, text))


SUBSTITUTIONS = (
    Substitution(
        "master-volume", frozenset({"volume_set", "audio_master_volume"}),
        "both set the machine's master volume from 0 to 100",
        blockers=r"\b(spotify|chrome|browser|firefox|edge|vlc|discord|zoom|teams|app|application|youtube|tab)\b"),
    Substitution(
        "bring-to-front", frozenset({"windows_focus", "apps_focus"}),
        "both bring an already open window to the foreground"),
    Substitution(
        "close-program", frozenset({"apps_close", "process_close"}),
        "both ask a running program to close the polite way",
        blockers=r"\b(kill|force|terminate|end task|not responding|frozen|freezes|hung|stuck|unresponsive|"
                 r"won'?t close|will not close)\b"),
    Substitution(
        "delete-file", frozenset({"files_delete", "files_recycle"}),
        "files_recycle is documented as what 'delete that file' means, and files_delete recycles by default",
        blockers=r"\b(permanent\w*|forever|shred\w*|irrecoverab\w*|unrecoverab\w*|bypass\w*|empty the recycle)\b"),
    Substitution(
        "forget-fact", frozenset({"brain_forget", "memory_forget"}),
        "both stop treating a remembered fact as current and keep the history"),
    Substitution(
        "fill-the-screen", frozenset({"windows_maximize", "windows_arrange"}),
        "windows_arrange has a 'full' placement that fills the screen as maximize does",
        requires=r"\b(maxim\w+|full ?screen|fill the (whole |entire )?screen|entire screen|whole screen)\b",
        blockers=r"\b(half|left|right|top|bottom|corner|snap\w*|side)\b"),
    Substitution(
        "write-a-file", frozenset({"files_write", "files_create"}),
        "the request does not say whether the file exists; each succeeds when it does not",
        blockers=r"\b(overwrit\w*|replac\w*|already|existing|update\w*|append\w*|edit\w*|over the old|rewrite)\b"),
    Substitution(
        "am-i-connected", frozenset({"system_wifi_status", "system_network"}),
        "a connectivity question with no wifi-specific or adapter-specific cue is answered by either",
        requires=r"\b(online|internet|connected|connection|network)\b",
        blockers=r"\b(wi-?fi|ssid|signal|wireless|adapter\w*|ip|ethernet|address\w*)\b"),
)

_ACTION = (r"close|open|delete|stop|pause|resume|restart|cancel|minimi[sz]e|maxim[iz]e|move|copy|rename|"
           r"read|run|play|skip|mute|focus|kill|remove|show|launch|start")
BARE_REFERENT = re.compile(
    rf"^\s*(please\s+|just\s+|now\s+)?({_ACTION})\s+(it|that|this|them|those|these)\s*[.!?]*\s*$")


def adjudicate(row: dict) -> dict | None:
    """The overlay entry for one corpus row, or None when the rule does not touch it."""
    expect = row.get("expect")
    text = " ".join(str(row.get("text", "")).lower().split())
    if row.get("turns") or row.get("context"):
        return None
    if expect not in ("ABSTAIN", "CLARIFY") and BARE_REFERENT.match(text):
        return {"clarify": True, "reason": "C: a bare imperative whose object is a pronoun with nothing to resolve it"}
    for sub in SUBSTITUTIONS:
        if expect in sub.members and sub.applies(text):
            return {"also_correct": sorted(sub.members - {expect}), "reason": f"A:{sub.key}: {sub.reason}"}
    return None


def build() -> dict:
    rows: dict[str, dict] = {}
    per_split: dict[str, dict[str, int]] = {}
    for split in ("development", "holdout"):
        counts: dict[str, int] = {}
        for line in (CORPUS / f"{split}.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            entry = adjudicate(row)
            if entry:
                rows[row["id"]] = entry
                key = entry["reason"].split(":")[1] if entry["reason"].startswith("A:") else "clarify"
                counts[key] = counts.get(key, 0) + 1
        per_split[split] = counts
    return {"rule": "docs/plans/ctx-002/PLAN.md#adjudication-rule", "counts": per_split,
            "rows": dict(sorted(rows.items()))}


def load_overlay() -> dict[str, dict]:
    """id -> overlay entry, or {} when no overlay has been written."""
    if not OVERLAY.is_file():
        return {}
    return json.loads(OVERLAY.read_text(encoding="utf-8")).get("rows", {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = json.dumps(build(), indent=1, ensure_ascii=False) + "\n"
    if args.check:
        if not OVERLAY.is_file() or OVERLAY.read_text(encoding="utf-8") != text:
            print("label overlay is stale: run scripts/label_adjudication.py", file=sys.stderr)
            return 1
        return 0
    OVERLAY.write_text(text, encoding="utf-8")
    print(json.dumps(json.loads(text)["counts"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
