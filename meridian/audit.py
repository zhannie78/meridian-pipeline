"""Snapshot forensics.

This module exists because Meridian's two extracts disagree about a quarter
they consider closed. It diffs them, classifies every change by cause, and
builds a reconciliation bridge that must close to the dollar. If it does not
close, `unexplained` is non-zero and the answer layer says so rather than
quietly absorbing the difference.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from .data import QUARTERS, Snapshot, SnapshotSet

COMPARED_FIELDS = [
    "account_name", "segment", "region", "rep_id", "stage",
    "deal_value", "close_date", "created_date", "product_line", "loss_reason",
]


class Bookings:
    def __init__(self, total: int, deal_count: int, rows: pd.DataFrame, snapshot: str):
        self.total = total
        self.deal_count = deal_count
        self.rows = rows
        self.snapshot = snapshot


class BridgeLine:
    def __init__(self, cause: str, label: str, amount: int, deal_ids: List[str]):
        self.cause = cause
        self.label = label
        self.amount = amount
        self.deal_ids = sorted(deal_ids)

    @property
    def deal_count(self) -> int:
        return len(self.deal_ids)


class Bridge:
    def __init__(self, opening: int, closing: int, lines: List[BridgeLine]):
        self.opening = opening
        self.closing = closing
        self.lines = lines

    @property
    def unexplained(self) -> int:
        return self.closing - (self.opening + sum(l.amount for l in self.lines))


def q1_bookings(snapshot: Snapshot) -> Bookings:
    """What this snapshot claims Meridian booked in Q1 2026."""
    rows = snapshot.won(quarter="Q1")
    return Bookings(int(rows.deal_value.sum()), len(rows), rows, snapshot.name)


def diff(before: Snapshot, after: Snapshot) -> pd.DataFrame:
    """Field-level changes on every deal_id present in both extracts."""
    a = before.clean.set_index("deal_id")
    b = after.clean.set_index("deal_id")
    shared = sorted(set(a.index) & set(b.index))
    records = []
    for did in shared:
        for field in COMPARED_FIELDS:
            x, y = a.loc[did, field], b.loc[did, field]
            if _differs(x, y):
                records.append({
                    "deal_id": did, "field": field, "before": x, "after": y,
                    "account_before": a.loc[did, "account_name"],
                })
    return pd.DataFrame(records, columns=[
        "deal_id", "field", "before", "after", "account_before"])


def _differs(x, y) -> bool:
    if pd.isna(x) and pd.isna(y):
        return False
    return str(x) != str(y)


def recycled_ids(before: Snapshot, after: Snapshot) -> List[str]:
    """Deal IDs reused for a different deal entirely.

    Detected by rule, never by a hard-coded list: an ID is recycled if the
    account was renamed OR the creation date moved forward. A real deal does
    not change the date it was created.
    """
    a = before.clean.set_index("deal_id")
    b = after.clean.set_index("deal_id")
    out = []
    for did in sorted(set(a.index) & set(b.index)):
        renamed = a.loc[did, "account_name"] != b.loc[did, "account_name"]
        recreated = b.loc[did, "created_date"] > a.loc[did, "created_date"]
        if renamed or recreated:
            out.append(did)
    return out


def reconcile_q1(before: Snapshot, after: Snapshot) -> Bridge:
    """Explain every dollar of the gap between the two Q1 answers."""
    opening = q1_bookings(before)
    closing = q1_bookings(after)
    recycled = set(recycled_ids(before, after))

    a = before.clean.set_index("deal_id")
    b = after.clean.set_index("deal_id")
    q1_start, q1_end = QUARTERS["Q1"]

    buckets = {
        "recycled_id": [],
        "slipped_to_q2": [],
        "reverted_to_open": [],
        "flipped_to_lost": [],
        "value_revised": [],
    }

    for did in opening.rows.deal_id:
        if did not in b.index:
            continue
        was, now = a.loc[did], b.loc[did]
        if did in recycled:
            buckets["recycled_id"].append((did, -int(was.deal_value)))
        elif now.stage == "Closed Lost":
            buckets["flipped_to_lost"].append((did, -int(was.deal_value)))
        elif now.stage != "Closed Won":
            buckets["reverted_to_open"].append((did, -int(was.deal_value)))
        elif not (q1_start <= now.close_date <= q1_end):
            buckets["slipped_to_q2"].append((did, -int(was.deal_value)))
        elif now.deal_value != was.deal_value:
            buckets["value_revised"].append(
                (did, int(now.deal_value) - int(was.deal_value)))

    labels = {
        "recycled_id": "Deal IDs recycled onto new Q2 deals",
        "slipped_to_q2": "Close date slipped into Q2",
        "reverted_to_open": "Reverted to open pipeline",
        "flipped_to_lost": "Flipped to Closed Lost",
        "value_revised": "Deal value revised",
    }
    order = ["recycled_id", "slipped_to_q2", "reverted_to_open",
             "flipped_to_lost", "value_revised"]

    lines = []
    for cause in order:
        items = buckets[cause]
        if items:
            lines.append(BridgeLine(
                cause, labels[cause],
                sum(amt for _, amt in items),
                [did for did, _ in items]))

    return Bridge(opening.total, closing.total, lines)


def slipped_into(snaps: SnapshotSet, quarter: str = "Q2") -> List[str]:
    """Deals counted as `quarter` wins now, but won in a prior quarter before."""
    bridge = reconcile_q1(snaps.at_q1_close, snaps.live)
    for line in bridge.lines:
        if line.cause == "slipped_to_q2" and quarter == "Q2":
            return line.deal_ids
    return []


def dimension_conflicts(snapshot: Snapshot) -> Dict[str, int]:
    """Where a deal's own dimension disagrees with its rep's."""
    merged = snapshot.clean.merge(
        snapshot.reps[["rep_id", "segment", "region"]],
        on="rep_id", suffixes=("_deal", "_rep"))
    return {
        "region": int((merged.region_deal != merged.region_rep).sum()),
        "segment": int((merged.segment_deal != merged.segment_rep).sum()),
    }


def stale_deals(snapshot: Snapshot) -> pd.DataFrame:
    """Open deals whose close date has already passed as of the snapshot."""
    df = snapshot.open_deals()
    return df[df.close_date < snapshot.as_of]


def integrity_report(snaps: SnapshotSet) -> Dict[str, object]:
    """Everything the audit knows, for the `audit` command and the monitor."""
    bridge = reconcile_q1(snaps.at_q1_close, snaps.live)
    changes = diff(snaps.at_q1_close, snaps.live)
    return {
        "bridge": bridge,
        "changes": changes,
        "changed_deals": int(changes.deal_id.nunique()),
        "recycled": recycled_ids(snaps.at_q1_close, snaps.live),
        "conflicts": dimension_conflicts(snaps.live),
        "stale": stale_deals(snaps.live),
        "new_ids": sorted(set(snaps.live.clean.deal_id)
                          - set(snaps.at_q1_close.clean.deal_id)),
    }


def reopened_losses(before: Snapshot, after: Snapshot) -> List[tuple]:
    """Q1 losses that came back to life, or lost their reason, in the later extract.

    Recycled IDs are excluded: a different deal on the same ID says nothing
    about the original loss.
    """
    a = before.clean.set_index("deal_id")
    b = after.clean.set_index("deal_id")
    recycled = set(recycled_ids(before, after))
    out = []
    for did in sorted(set(a.index) & set(b.index) - recycled):
        was, now = a.loc[did], b.loc[did]
        if was.stage != "Closed Lost":
            continue
        if now.stage != "Closed Lost":
            out.append((did, "reopened"))
        elif pd.notna(was.loss_reason) and pd.isna(now.loss_reason):
            out.append((did, "reason erased"))
    return out


def region_conflicts(snapshot: Snapshot) -> pd.DataFrame:
    """Deals whose region differs from their rep's territory."""
    merged = snapshot.clean.merge(
        snapshot.reps[["rep_id", "rep_name", "region"]],
        on="rep_id", suffixes=("_deal", "_rep"))
    return merged[merged.region_deal != merged.region_rep].sort_values("deal_id")
