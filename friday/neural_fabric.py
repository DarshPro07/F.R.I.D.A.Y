"""
The Neural Fabric training lifecycle (NEURAL-001, PRD section 24.11).

    verified trajectory -> sanitized feature/example -> offline dataset
      -> candidate model -> holdout eval -> promotion or rejection

PRD 24.11: "A single interaction never directly mutates production weights."

This is the skeleton of the back half of that line; the front half is
`friday.lab_dataset`, whose export directory (train.jsonl, the frozen
eval.jsonl, manifest.json) is the only dataset a network may be registered
against. No second dataset path exists here: a network records the manifest's
sha256 for both files at registration and every later step re-checks them, so
the holdout it is judged on is the one frozen when the dataset was built.

A network is born CANDIDATE and may be used by nothing. It is trained offline
by an injected `trainer(train_rows) -> artifact` (any JSON value; this module
trains nothing itself), scored on the holdout by an injected
`score(artifact, eval_rows)` and compared with an injected `baseline(eval_rows)`
(PRD 28: a learned router is deployed only after beating the simple baseline).
Higher scores are better. `promote` moves it to PRODUCTION only when
candidate >= baseline + margin; otherwise it stays CANDIDATE and the measured
numbers and the decision are recorded. `use` is the one door out of the
module and refuses anything not PRODUCTION.

State is one JSON file in the directory the caller names; there is no store
table. Not built here: the adversarial/regression eval stage and any of the
ten candidate networks of section 24.

Assumption (revisit if wrong): the margin is stated by the caller at promotion
time (DEFAULT_MARGIN when omitted) rather than fixed per network kind.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Callable

from friday import lab_dataset as LD

CANDIDATE = "CANDIDATE"
PRODUCTION = "PRODUCTION"

#: Absolute score margin a candidate must clear over the baseline.
DEFAULT_MARGIN = 0.02

STATE_FILE = "neural_fabric.json"


class AlreadyRegistered(Exception):
    """A network of that name exists."""


class UnknownNetwork(Exception):
    """No network of that name is registered."""


class DatasetChanged(Exception):
    """The training file no longer matches what the network registered against."""


class NotTrained(Exception):
    """The network has not been trained yet."""


class NotEvaluated(Exception):
    """The network has no holdout evaluation to promote on."""


class NotProduction(Exception):
    """Only a PRODUCTION network may be used."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines()]


class NeuralFabric:
    def __init__(self, state_dir: str | Path):
        self._path = Path(state_dir) / STATE_FILE

    # -- state ---------------------------------------------------------------

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _save(self, state: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self._path)

    @staticmethod
    def _get(state: dict, name: str) -> dict:
        if name not in state:
            raise UnknownNetwork(name)
        return state[name]

    # -- the dataset ---------------------------------------------------------

    @staticmethod
    def _recorded(out: Path) -> dict:
        try:
            files = json.loads((out / LD.MANIFEST).read_text(encoding="utf-8"))["files"]
            return {"train_sha256": files[LD.TRAIN]["sha256"],
                    "eval_sha256": files[LD.EVAL]["sha256"]}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise LD.NotAnExport(f"{out} has no readable {LD.MANIFEST}") from exc

    @staticmethod
    def _read(rec: dict) -> tuple[list[dict], list[dict]]:
        """The train and holdout rows, each checked against the sha256 the
        network registered with."""
        ref = rec["dataset"]
        out = Path(ref["dir"])
        eval_bytes = (out / LD.EVAL).read_bytes()
        if _sha(eval_bytes) != ref["eval_sha256"]:
            raise LD.EvalSetChanged(
                f"{out / LD.EVAL} no longer matches the sha256 {rec['name']!r} registered with")
        train_bytes = (out / LD.TRAIN).read_bytes()
        if _sha(train_bytes) != ref["train_sha256"]:
            raise DatasetChanged(
                f"{out / LD.TRAIN} no longer matches the sha256 {rec['name']!r} registered with")
        return _rows(train_bytes), _rows(eval_bytes)

    # -- lifecycle -----------------------------------------------------------

    def register(self, name: str, kind: str, dataset_dir: str | Path) -> dict:
        """Register a CANDIDATE against a lab_dataset export."""
        state = self._load()
        if name in state:
            raise AlreadyRegistered(name)
        out = Path(dataset_dir).resolve()
        rec = {"name": name, "kind": kind, "status": CANDIDATE, "trained": False,
               "artifact": None, "holdout": None, "decision": None,
               "dataset": {"dir": str(out), **self._recorded(out)}}
        self._read(rec)  # the files on disk must already match their manifest
        state[name] = rec
        self._save(state)
        return rec

    def train(self, name: str, trainer: Callable[[list[dict]], object]) -> dict:
        """Train offline: the trainer sees the train rows only."""
        state = self._load()
        rec = self._get(state, name)
        train_rows, _ = self._read(rec)
        rec["artifact"] = trainer(train_rows)
        rec["trained"] = True
        rec["holdout"] = rec["decision"] = None
        self._save(state)
        return rec

    def evaluate(self, name: str, score: Callable[[object, list[dict]], float],
                 baseline: Callable[[list[dict]], float]) -> dict:
        """Score the candidate and the baseline on the frozen holdout."""
        state = self._load()
        rec = self._get(state, name)
        if not rec["trained"]:
            raise NotTrained(name)
        _, holdout = self._read(rec)
        rec["holdout"] = {"candidate": score(rec["artifact"], holdout),
                          "baseline": baseline(holdout), "rows": len(holdout),
                          "eval_sha256": rec["dataset"]["eval_sha256"]}
        self._save(state)
        return rec

    def promote(self, name: str, margin: float = DEFAULT_MARGIN, *,
                strict: bool = False) -> dict:
        """PRODUCTION only if the recorded holdout win clears MARGIN; else the
        network stays CANDIDATE with the numbers and the decision recorded.
        `strict` (LAB-004) requires the candidate to exceed baseline + margin,
        so a tie at margin 0 is not a win."""
        state = self._load()
        rec = self._get(state, name)
        if not rec["holdout"]:
            raise NotEvaluated(name)
        self._read(rec)  # the holdout it was judged on must still be the frozen one
        h = rec["holdout"]
        bar = h["baseline"] + margin
        won = h["candidate"] > bar if strict else h["candidate"] >= bar
        rec["decision"] = {"promoted": won, "margin": margin,
                           "delta": h["candidate"] - h["baseline"], "at": time.time()}
        if won:
            rec["status"] = PRODUCTION
        self._save(state)
        return rec

    def demote(self, name: str, reason: str) -> dict:
        """Back to CANDIDATE (LAB-004 rollback): `use` refuses it again."""
        state = self._load()
        rec = self._get(state, name)
        rec["status"] = CANDIDATE
        rec["decision"] = {"promoted": False, "demoted": reason, "at": time.time()}
        self._save(state)
        return rec

    def use(self, name: str) -> object:
        """The artifact of a PRODUCTION network; anything else is refused."""
        rec = self._load().get(name)
        if rec is None or rec["status"] != PRODUCTION:
            raise NotProduction(f"{name!r} is not a PRODUCTION network")
        return rec["artifact"]
