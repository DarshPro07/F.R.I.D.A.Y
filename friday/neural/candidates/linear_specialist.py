"""
The offline trainer of the task-category linear specialist (LAB-004).

This is the one place a specialist's weights are fitted, so it is the one
module the NEURAL-002 boundary test exempts. It trains from rows, returns a
plain-JSON artifact and touches nothing else: a candidate leaves here only
through `friday.lab_specialist`, which judges it and promotes it on a strict
win (PRD 24.11).
"""
from __future__ import annotations

from typing import Mapping, Sequence

from friday import experimental_local as EL
from friday import lab_benchmark as LB

#: Fewer labelled train cases than this is not a dataset (assumed, LAB-003's
#: default minimum); in particular no single interaction trains a specialist.
MIN_TRAIN_CASES = 10


class TrainingRefused(Exception):
    """The input is not a dataset a specialist may be trained on."""


def require_enough(count: int) -> None:
    if count < MIN_TRAIN_CASES:
        raise TrainingRefused(
            f"{count} labelled training case(s); a specialist needs at least "
            f"{MIN_TRAIN_CASES}, and no single interaction trains one")


def fit_baseline(cases: Sequence[LB.Case]) -> dict:
    """The LAB-003 benchmark's linear baseline, fitted here so this module stays
    the one place weights are fitted (NEURAL-002). A measurement baseline, not
    a specialist: it is never promoted, so no dataset minimum applies."""
    return LB.LinearClassifier().fit(list(cases)).to_dict()


def train_linear(rows: Sequence[Mapping]) -> dict:
    """Train offline on the approved train rows; the artifact is plain JSON.
    Refuses anything that is not a list of rows with enough labelled cases."""
    if isinstance(rows, (Mapping, str, bytes)):
        raise TrainingRefused("a trainer takes the approved dataset's rows, not one interaction")
    cases = LB.cases_for(EL.TASK_CATEGORY, rows)
    require_enough(len(cases))
    return LB.LinearClassifier().fit(cases).to_dict()
