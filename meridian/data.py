"""Loading, row checks, and snapshot precedence.

Meridian gave us two CRM extracts, not two quarters. The Q2 file is a full
re-extract taken 2026-05-02 that restates every Q1 record. Treating it as
"the Q2 data" is the mistake that makes their dashboards disagree.

Precedence rule, applied everywhere and printed on every answer:

    A closed quarter is reported from the snapshot taken at its close.
    The live quarter is reported from the latest snapshot.

When each extract was taken is declared in snapshots.json, not hardcoded. An
extract with no as-of date can't be used: "quarter to date" means nothing
without one.

Every row is checked on load. A row with a missing or invalid value is never
counted as zero. It is set aside, and any answer whose filters it could have
matched says so (see selection.py).
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_FILE = "snapshots.json"

YEAR = 2026
QUARTERS = {
    "Q1": (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-03-31")),
    "Q2": (pd.Timestamp("2026-04-01"), pd.Timestamp("2026-06-30")),
}
QUOTA_COLUMN = {"Q1": "quota_q1_2026", "Q2": "quota_q2_2026"}

OPEN_STAGES = ("Prospecting", "Discovery", "Proposal", "Negotiation")
WON = "Closed Won"
LOST = "Closed Lost"
KNOWN_STAGES = OPEN_STAGES + (WON, LOST)
SEGMENTS = ("Enterprise", "Mid-Market", "SMB")

DEAL_COLUMNS = ("deal_id", "account_name", "segment", "region", "rep_id", "stage",
                "deal_value", "close_date", "created_date", "product_line",
                "loss_reason")
REP_COLUMNS = ("rep_id", "rep_name", "segment", "region", "manager", "hire_date")


class DataIntegrityError(Exception):
    """A file, column or manifest entry is missing, so nothing can be answered."""


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------

def quarter_of(ts) -> Optional[str]:
    if ts is None or pd.isna(ts):
        return None
    for q, (start, end) in QUARTERS.items():
        if start <= ts <= end:
            return q
    return None


def quarter_length(quarter: str) -> int:
    start, end = QUARTERS[quarter]
    return int((end - start).days) + 1


def day_of_quarter(quarter: str, ts: pd.Timestamp) -> int:
    return int((ts - QUARTERS[quarter][0]).days) + 1


def same_elapsed_day(day: int, quarter: str) -> pd.Timestamp:
    """The date that is `day` days into `quarter` (day 1 is the first day)."""
    return QUARTERS[quarter][0] + pd.Timedelta(days=day - 1)


# ---------------------------------------------------------------------------
# manifest and roster: the only things read before a question is validated
# ---------------------------------------------------------------------------

class Manifest:
    """snapshots.json: which file is which extract, and when each was taken."""

    def __init__(self, entries: Dict[str, dict], path: str):
        self.entries = entries
        self.path = path

    @classmethod
    def load(cls, root: str = ROOT) -> "Manifest":
        path = os.path.join(root, MANIFEST_FILE)
        if not os.path.exists(path):
            raise DataIntegrityError(
                "%s is missing. It records which extract is which and when each "
                "was taken, and nothing can be dated without it." % MANIFEST_FILE)
        with open(path) as fh:
            doc = json.load(fh)
        entries = doc.get("snapshots") or {}
        for name in ("at_q1_close", "live"):
            if name not in entries:
                raise DataIntegrityError("%s has no %r entry." % (MANIFEST_FILE, name))
            for key in ("deals", "reps"):
                if not entries[name].get(key):
                    raise DataIntegrityError(
                        "%s entry %r has no %r file." % (MANIFEST_FILE, name, key))
        return cls(entries, path)

    def as_of(self, name: str) -> Optional[pd.Timestamp]:
        raw = self.entries[name].get("as_of")
        if raw in (None, ""):
            return None
        ts = pd.to_datetime(raw, errors="coerce")
        return None if pd.isna(ts) else ts

    def as_of_basis(self, name: str) -> str:
        return self.entries[name].get("as_of_basis") or "basis not recorded"

    def file(self, name: str, kind: str) -> str:
        return self.entries[name][kind]


class Roster:
    """The rep roster from the live extract.

    Reference data only. The validator reads this and the manifest, and
    nothing else: no deal row is read until a question has been validated.
    """

    def __init__(self, reps: pd.DataFrame, source: str):
        self.reps = reps
        self.source = source

    @classmethod
    def load(cls, root: str = ROOT, manifest: Optional[Manifest] = None) -> "Roster":
        manifest = manifest or Manifest.load(root)
        rel = manifest.file("live", "reps")
        return cls(_read_reps(os.path.join(root, rel), rel), rel)

    def valid(self) -> pd.DataFrame:
        return self.reps[self.reps.rep_id.notna() & self.reps.rep_name.notna()]

    def name_of(self, rep_id: str) -> Optional[str]:
        hit = self.reps[self.reps.rep_id == rep_id]
        return None if hit.empty else hit.rep_name.iloc[0]

    def segment_of(self, rep_id: str) -> Optional[str]:
        hit = self.reps[self.reps.rep_id == rep_id]
        return None if hit.empty else hit.segment.iloc[0]

    def manager_segment(self, manager: str) -> Optional[str]:
        segs = self.reps[self.reps.manager == manager].segment.dropna().unique()
        return segs[0] if len(segs) == 1 else None

    def name_phrases(self):
        """Every full, first and last name on the roster, reps and managers."""
        out = set()
        for col in ("rep_name", "manager"):
            for full in self.reps[col].dropna().unique():
                out.add(full.lower())
                out.update(part.lower() for part in full.split())
        return out


# ---------------------------------------------------------------------------
# reading and row checks
# ---------------------------------------------------------------------------

def _read(path: str, rel: str, required) -> pd.DataFrame:
    if not os.path.exists(path):
        raise DataIntegrityError("%s is missing." % rel)
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise DataIntegrityError("%s is missing column(s): %s" % (rel, ", ".join(missing)))
    raw = raw.reset_index(drop=True)
    raw["_line"] = np.arange(2, len(raw) + 2)
    return raw


def _blank(series: pd.Series) -> pd.Series:
    return series.str.strip() == ""


def _read_deals(path: str, rel: str, roster_ids=None) -> pd.DataFrame:
    raw = _read(path, rel, DEAL_COLUMNS)
    df = raw.copy()
    for col in DEAL_COLUMNS:
        df[col] = raw[col].where(~_blank(raw[col]))
    df["deal_value"] = pd.to_numeric(df["deal_value"], errors="coerce")
    df["close_date"] = pd.to_datetime(df["close_date"], errors="coerce")
    df["created_date"] = pd.to_datetime(df["created_date"], errors="coerce")

    problems = [[] for _ in range(len(df))]

    def mark(mask, describe):
        for i in np.flatnonzero(np.asarray(mask, dtype=bool)):
            problems[i].append(describe(i))

    counts = df.deal_id.value_counts()
    mark(df.deal_id.isna(), lambda i: "deal_id missing")
    mark(df.deal_id.notna() & df.deal_id.duplicated(keep=False),
         lambda i: "deal_id %s appears %d times in this file"
         % (df.deal_id.iat[i], counts[df.deal_id.iat[i]]))
    mark(_blank(raw.deal_value), lambda i: "deal_value missing")
    mark(~_blank(raw.deal_value) & df.deal_value.isna(),
         lambda i: "deal_value %r is not a number" % raw.deal_value.iat[i])
    mark(df.deal_value < 0, lambda i: "deal_value is negative")
    mark(_blank(raw.close_date), lambda i: "close_date missing")
    mark(~_blank(raw.close_date) & df.close_date.isna(),
         lambda i: "close_date %r is not a date" % raw.close_date.iat[i])
    mark(df.stage.isna(), lambda i: "stage missing")
    mark(df.stage.notna() & ~df.stage.isin(KNOWN_STAGES),
         lambda i: "stage %r is not a known stage" % df.stage.iat[i])
    mark(df.segment.isna(), lambda i: "segment missing")
    mark(df.segment.notna() & ~df.segment.isin(SEGMENTS),
         lambda i: "segment %r is not a known segment" % df.segment.iat[i])
    mark(df.rep_id.isna(), lambda i: "rep_id missing")
    if roster_ids is not None:
        mark(df.rep_id.notna() & ~df.rep_id.isin(roster_ids),
             lambda i: "rep_id %s is not on the roster" % df.rep_id.iat[i])

    df["_problem"] = [("; ".join(p) if p else None) for p in problems]
    return df


def _read_reps(path: str, rel: str) -> pd.DataFrame:
    raw = _read(path, rel, REP_COLUMNS)
    df = raw.copy()
    for col in raw.columns:
        if col == "_line":
            continue
        df[col] = raw[col].where(~_blank(raw[col]))
        if col.startswith("quota_"):
            df["_raw_" + col] = raw[col]
            df[col] = pd.to_numeric(df[col], errors="coerce")

    problems = []
    dup = df.rep_id.notna() & df.rep_id.duplicated(keep=False)
    for i in range(len(df)):
        p = []
        if pd.isna(df.rep_id.iat[i]):
            p.append("rep_id missing")
        elif dup.iat[i]:
            p.append("rep_id %s appears more than once" % df.rep_id.iat[i])
        if pd.isna(df.segment.iat[i]):
            p.append("segment missing")
        problems.append("; ".join(p) if p else None)
    df["_problem"] = problems
    return df


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------

class Snapshot:
    """One CRM extract, with the date it was taken."""

    def __init__(self, name: str, as_of: Optional[pd.Timestamp], as_of_basis: str,
                 deals_file: str, reps_file: str,
                 deals: pd.DataFrame, reps: pd.DataFrame):
        self.name = name
        self.as_of = as_of
        self.as_of_basis = as_of_basis
        self.deals_file = deals_file
        self.reps_file = reps_file
        self.deals = deals
        self.reps = reps

    @property
    def clean(self) -> pd.DataFrame:
        """Rows with no problems. Only these are ever counted."""
        return self.deals[self.deals._problem.isna()]

    def describe(self) -> str:
        label = "extract taken at Q1 close" if self.name == "at_q1_close" else "live extract"
        date = self.as_of.date() if self.as_of is not None else "no as-of date"
        return "%s, as of %s (%s)" % (label, date, self.as_of_basis)

    def deal(self, deal_id: str) -> pd.Series:
        hit = self.deals[self.deals.deal_id == deal_id]
        if hit.empty:
            raise KeyError("%s not present in snapshot %r" % (deal_id, self.name))
        return hit.iloc[0]

    def won(self, quarter: Optional[str] = None,
            through: Optional[pd.Timestamp] = None) -> pd.DataFrame:
        df = self.clean[self.clean.stage == WON]
        if quarter:
            start, end = QUARTERS[quarter]
            df = df[(df.close_date >= start) & (df.close_date <= end)]
        if through is not None:
            df = df[df.close_date <= through]
        return df

    def open_deals(self, quarter: Optional[str] = None) -> pd.DataFrame:
        df = self.clean[self.clean.stage.isin(OPEN_STAGES)]
        if quarter:
            start, end = QUARTERS[quarter]
            df = df[(df.close_date >= start) & (df.close_date <= end)]
        return df

    def __repr__(self):
        return "<Snapshot %s as_of %s: %d deals>" % (self.name, self.as_of, len(self.deals))


class SnapshotSet:
    """Both extracts plus the precedence rule that chooses between them."""

    def __init__(self, at_q1_close: Snapshot, live: Snapshot):
        self.at_q1_close = at_q1_close
        self.live = live
        self._recycled = None
        self._slipped = None

    @property
    def as_of(self) -> Optional[pd.Timestamp]:
        return self.live.as_of

    def for_quarter(self, quarter: str) -> Snapshot:
        """Q1 closed on 2026-03-31, so it is read from the extract taken then.
        Q2 is in progress, so it is read from the latest extract."""
        return self.at_q1_close if quarter == "Q1" else self.live

    def recycled_ids(self) -> set:
        """Deal IDs reused for a different deal between the two extracts."""
        if self._recycled is None:
            from . import audit
            self._recycled = set(audit.recycled_ids(self.at_q1_close, self.live))
        return self._recycled

    def slipped_ids(self) -> set:
        """Q1 wins at close that now carry a Q2 close date in the live extract."""
        if self._slipped is None:
            from . import audit
            bridge = audit.reconcile_q1(self.at_q1_close, self.live)
            self._slipped = set()
            for line in bridge.lines:
                if line.cause == "slipped_to_q2":
                    self._slipped.update(line.deal_ids)
        return self._slipped


def load(root: str = ROOT, manifest: Optional[Manifest] = None) -> SnapshotSet:
    manifest = manifest or Manifest.load(root)
    built = {}
    for name in ("at_q1_close", "live"):
        reps_rel = manifest.file(name, "reps")
        deals_rel = manifest.file(name, "deals")
        reps = _read_reps(os.path.join(root, reps_rel), reps_rel)
        deals = _read_deals(os.path.join(root, deals_rel), deals_rel,
                            roster_ids=set(reps.rep_id.dropna()))
        built[name] = Snapshot(name, manifest.as_of(name), manifest.as_of_basis(name),
                               deals_rel, reps_rel, deals, reps)
    return SnapshotSet(built["at_q1_close"], built["live"])
