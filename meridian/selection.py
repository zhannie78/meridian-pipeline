"""Row selection with visible filters.

Every figure starts as a selection: a named set of filters applied to one file.
The filters are kept as readable text, so the answer can print exactly what was
applied and an executive can reapply them in a spreadsheet.

A row whose value for a filtered field is missing or invalid is not treated as
failing the filter. Nobody can tell whether it belongs, so it is set aside as
"unplaced" and reported. Excluding it silently would be the same as counting it
as zero.
"""
from __future__ import annotations

from typing import Callable, List, Optional

import pandas as pd

from .data import (KNOWN_STAGES, QUOTA_COLUMN, SEGMENTS, DataIntegrityError,
                   Snapshot)


class Filter:
    def __init__(self, field: str, text: str,
                 test: Callable[[pd.Series], pd.Series]):
        self.field = field
        self.text = text
        self._test = test

    @classmethod
    def equals(cls, field: str, value) -> "Filter":
        return cls(field, '%s = "%s"' % (field, value), lambda s: s == value)

    @classmethod
    def one_of(cls, field: str, values) -> "Filter":
        values = tuple(values)
        return cls(field, "%s is one of %s" % (field, ", ".join('"%s"' % v for v in values)),
                   lambda s: s.isin(values))

    @classmethod
    def date_between(cls, field: str, start: pd.Timestamp, end: pd.Timestamp) -> "Filter":
        return cls(field, "%s from %s to %s, inclusive" % (field, start.date(), end.date()),
                   lambda s: (s >= start) & (s <= end))

    @classmethod
    def date_outside(cls, field: str, start: pd.Timestamp, end: pd.Timestamp) -> "Filter":
        return cls(field, "%s before %s or after %s" % (field, start.date(), end.date()),
                   lambda s: (s < start) | (s > end))

    def evaluate(self, df: pd.DataFrame):
        """Return (matches, unknown). Unknown rows can't be judged either way."""
        col = df[self.field]
        unknown = col.isna()
        if self.field == "stage":
            unknown = unknown | ~col.isin(KNOWN_STAGES)
        elif self.field == "segment":
            unknown = unknown | ~col.isin(SEGMENTS)
        matches = pd.Series(False, index=df.index, dtype=bool)
        known = ~unknown.astype(bool)
        if known.any():
            tested = self._test(col[known]).astype(bool)
            matches = tested.reindex(df.index, fill_value=False)
        return matches.astype(bool), unknown.astype(bool)


class DealSelection:
    kind = "deals"

    def __init__(self, key: str, title: str, snapshot: Snapshot,
                 filters: List[Filter], rows: pd.DataFrame, unplaced: pd.DataFrame):
        self.key = key
        self.title = title
        self.snapshot = snapshot
        self.filters = filters
        self.rows = rows
        self.unplaced = unplaced

    @property
    def file(self) -> str:
        return self.snapshot.deals_file

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def total(self) -> int:
        # Rows here passed every check on load, so deal_value is never missing.
        return int(self.rows.deal_value.sum()) if len(self.rows) else 0

    @property
    def complete(self) -> bool:
        return self.unplaced.empty

    @property
    def ids(self) -> List[str]:
        return list(self.rows.deal_id)

    @property
    def rows_in_file(self) -> int:
        return len(self.snapshot.deals)

    def total_for(self, column: str, value) -> int:
        part = self.rows[self.rows[column] == value]
        return int(part.deal_value.sum()) if len(part) else 0

    def count_for(self, column: str, value) -> int:
        return int((self.rows[column] == value).sum())


def select_deals(snapshot: Snapshot, filters: List[Filter],
                 key: str, title: str) -> DealSelection:
    df = snapshot.deals
    passes = pd.Series(True, index=df.index)
    unknown = pd.Series(False, index=df.index)
    for f in filters:
        matches, unk = f.evaluate(df)
        passes &= (matches | unk)
        unknown |= unk
    clean = df._problem.isna()
    rows = df[passes & ~unknown & clean]
    unplaced = df[passes & (unknown | ~clean)].copy()
    unplaced["_reason"] = [
        p if isinstance(p, str) else "a filtered field is missing"
        for p in unplaced._problem
    ]
    return DealSelection(key, title, snapshot, filters, rows, unplaced)


class QuotaSelection:
    kind = "quota"

    def __init__(self, key: str, title: str, snapshot: Snapshot, quarter: str,
                 filters: List[Filter], rows: pd.DataFrame, missing: pd.DataFrame):
        self.key = key
        self.title = title
        self.snapshot = snapshot
        self.quarter = quarter
        self.filters = filters
        self.rows = rows
        self.missing = missing
        self.column = QUOTA_COLUMN[quarter]

    @property
    def file(self) -> str:
        return self.snapshot.reps_file

    @property
    def rows_in_file(self) -> int:
        return len(self.snapshot.reps)

    @property
    def total(self) -> Optional[int]:
        """None when any matching rep's quota is missing: an unknown quota is
        never summed as zero."""
        if self.rows.empty or not self.missing.empty:
            return None
        return int(self.rows[self.column].sum())

    @property
    def undefined_reason(self) -> Optional[str]:
        if self.rows.empty and self.missing.empty:
            return "no reps matched the filters"
        if not self.missing.empty:
            return "quota is missing or invalid for %s" % ", ".join(
                "%s (line %d)" % (r.rep_id if isinstance(r.rep_id, str) else "unnamed rep",
                                  r._line)
                for _, r in self.missing.iterrows())
        if self.total == 0:
            return "quota totals $0"
        return None

    def quota_for(self, rep_id: str) -> Optional[int]:
        if not self.missing[self.missing.rep_id == rep_id].empty:
            return None
        hit = self.rows[self.rows.rep_id == rep_id]
        return None if hit.empty else int(hit[self.column].iloc[0])


def select_quota(snapshot: Snapshot, quarter: str, key: str,
                 segment: Optional[str] = None,
                 rep_id: Optional[str] = None) -> QuotaSelection:
    column = QUOTA_COLUMN[quarter]
    if column not in snapshot.reps.columns:
        raise DataIntegrityError("%s has no %s column." % (snapshot.reps_file, column))
    df = snapshot.reps
    filters = []
    if segment:
        filters.append(Filter.equals("segment", segment))
    if rep_id:
        filters.append(Filter.equals("rep_id", rep_id))
    passes = pd.Series(True, index=df.index)
    unknown = pd.Series(False, index=df.index)
    for f in filters:
        matches, unk = f.evaluate(df)
        passes &= (matches | unk)
        unknown |= unk
    bad = unknown | df._problem.notna() | df[column].isna()
    rows = df[passes & ~bad]
    missing = df[passes & bad]
    return QuotaSelection(key, "%s quota" % quarter, snapshot, quarter,
                          filters, rows, missing)
