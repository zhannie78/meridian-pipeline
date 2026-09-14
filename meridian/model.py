"""The shape of an answer, and number formatting that never invents a zero."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional

from .flags import Assumption, Flag


def strong(text: str) -> str:
    """Mark text for emphasis. The browser renders it bold; the CLI drops the marks."""
    return "**%s**" % text


def plain(text: str) -> str:
    return str(text).replace("**", "")


def cell(value) -> str:
    """Display text for a free-text field. Blank shows as (blank), never "nan"."""
    if value is None or (isinstance(value, float) and value != value):
        return "(blank)"
    try:
        import pandas as pd
        if pd.isna(value):
            return "(blank)"
    except (TypeError, ValueError):
        pass
    return str(value)


def money(value, lower_bound: bool = False) -> str:
    if value is None:
        return "unknown"
    value = int(round(value))
    text = ("-$" if value < 0 else "$") + "{:,}".format(abs(value))
    return "at least " + text if lower_bound else text


def _half_up(value: float, places: int) -> str:
    """Round halves away from zero, as people do by hand (0.625 -> 0.63), not to even."""
    q = Decimal(1).scaleb(-places)
    return str(Decimal(repr(float(value))).quantize(q, rounding=ROUND_HALF_UP))


def pct(fraction, places: int = 1, lower_bound: bool = False) -> str:
    if fraction is None:
        return "undefined"
    text = _half_up(100.0 * fraction, places) + "%"
    return "at least " + text if lower_bound else text


def times(ratio, places: int = 2, lower_bound: bool = False) -> str:
    if ratio is None:
        return "undefined"
    text = _half_up(ratio, places) + "x"
    return "at least " + text if lower_bound else text


class Metric:
    def __init__(self, label: str, value: str, note: str = ""):
        self.label = label
        self.value = value
        self.note = note


class Table:
    def __init__(self, title: str, columns: List[str], rows: List[List[str]],
                 numeric: Optional[List[str]] = None):
        self.title = title
        self.columns = columns
        self.rows = rows
        self.numeric = set(numeric or [])


class Term:
    """One side of a calculation, with how it was obtained."""

    def __init__(self, label: str, value, explanation: str,
                 lower_bound: bool = False, undefined_reason: Optional[str] = None,
                 fmt: str = "money"):
        self.fmt = fmt
        self.label = label
        self.value = value
        self.explanation = explanation
        self.lower_bound = lower_bound
        self.undefined_reason = undefined_reason


class Calculation:
    def __init__(self, name: str, formula: str, numerator: Term,
                 denominator: Optional[Term], result, kind: str,
                 undefined_reason: Optional[str] = None):
        # "/" for ratios, "-" for differences, "sum" for a single total.
        self.op = ("/" if kind in ("pct", "x") else "-" if denominator is not None else "sum")
        self.name = name
        self.formula = formula
        self.numerator = numerator
        self.denominator = denominator
        self.result = result
        self.kind = kind  # "pct", "x" or "money"
        self.undefined_reason = undefined_reason

    @property
    def lower_bound(self) -> bool:
        return self.numerator.lower_bound

    def display(self, places: int = 2) -> str:
        if self.result is None:
            return "undefined (%s)" % self.undefined_reason
        if self.kind == "pct":
            return pct(self.result, places, self.lower_bound)
        if self.kind == "x":
            return times(self.result, places, self.lower_bound)
        return money(self.result, self.lower_bound)


def divide(name: str, formula: str, numerator: Term, denominator: Term,
           kind: str = "pct") -> Calculation:
    """A ratio that reports undefined, never 0 or infinity, when it can't be computed."""
    reason = None
    if numerator.value is None:
        reason = numerator.undefined_reason or "%s is unknown" % numerator.label
    elif denominator.value is None:
        reason = denominator.undefined_reason or "%s is unknown" % denominator.label
    elif denominator.value == 0:
        reason = "%s is $0" % denominator.label
    result = None if reason else float(numerator.value) / float(denominator.value)
    return Calculation(name, formula, numerator, denominator, result, kind, reason)


class Answer:
    def __init__(self, query, headline: str = ""):
        self.query = query
        self.intent = query.intent
        self.headline = headline
        self.lines: List[str] = []
        self.metrics: List[Metric] = []
        self.tables: List[Table] = []
        self.datasets: list = []
        self.calculations: List[Calculation] = []
        self.assumptions: List[Assumption] = list(query.assumptions)
        self.flags: List[Flag] = list(query.flags)
        self.values: dict = {}
        self.audit_snaps = None
        self.snaps = None

    def flag(self, flag: Optional[Flag]):
        if flag is not None:
            self.flags.append(flag)

    def assume(self, assumption: Assumption):
        if all(a.code != assumption.code for a in self.assumptions):
            self.assumptions.append(assumption)

    def dataset(self, selection):
        if selection is not None and all(d.key != selection.key for d in self.datasets):
            self.datasets.append(selection)
        return selection

    @property
    def flag_codes(self) -> List[str]:
        return [f.code for f in self.flags]

    @property
    def assumption_codes(self) -> List[str]:
        return [a.code for a in self.assumptions]
