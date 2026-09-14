"""The metric layer. Deterministic, hand-written, reviewable.

No language model is anywhere near this code. It runs only on a query that has
already been validated, and every answer it builds carries:

    datasets      the file, snapshot date, filters and rows behind each figure
    calculations  formula, numerator and denominator, so the figure can be redone
    assumptions   the definitions the answer depends on
    flags         what a reader must know about this particular answer

Nothing missing is ever counted as zero. A row that can't be placed is
reported, and the figure it would have fed is marked "at least". A quota that
is missing or $0 makes a ratio undefined, not 0%.
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from . import audit
from .data import (LOST, OPEN_STAGES, QUARTERS, WON, YEAR, Snapshot, SnapshotSet,
                   day_of_quarter, quarter_length)
from .flags import Assumption, Flag
from .model import (Answer, Calculation, Metric, Table, Term, cell, divide, money, strong,
                    pct, times)
from .selection import (DealSelection, Filter, QuotaSelection, select_deals,
                        select_quota)


def execute(vq, snaps: SnapshotSet) -> Answer:
    ans = {
        "attainment": attainment,
        "coverage": coverage,
        "rep_risk": rep_risk,
        "quarter_comparison": quarter_comparison,
        "deal_list": deal_list,
        "audit": audit_answer,
    }[vq.intent](vq, snaps)
    ans.snaps = snaps
    return ans


# ---------------------------------------------------------------------------
# selections
# ---------------------------------------------------------------------------

def _filters(stages, start, end, segment=None, rep_id=None) -> List[Filter]:
    fs = []
    if stages:
        fs.append(Filter.equals("stage", stages[0]) if len(stages) == 1
                  else Filter.one_of("stage", stages))
    fs.append(Filter.date_between("close_date", start, end))
    if segment:
        fs.append(Filter.equals("segment", segment))
    if rep_id:
        fs.append(Filter.equals("rep_id", rep_id))
    return fs


def _won(snap: Snapshot, quarter: str, through: pd.Timestamp, key: str,
         segment=None, rep_id=None) -> DealSelection:
    start, _ = QUARTERS[quarter]
    return select_deals(snap, _filters([WON], start, through, segment, rep_id), key,
                        "Closed-won deals, %s %d through %s" % (quarter, YEAR, through.date()))


def _open(snap: Snapshot, quarter: str, key: str, segment=None, rep_id=None) -> DealSelection:
    start, end = QUARTERS[quarter]
    return select_deals(snap, _filters(OPEN_STAGES, start, end, segment, rep_id), key,
                        "Open pipeline, close date in %s %d" % (quarter, YEAR))


def _quota(snap: Snapshot, quarter: str, key: str, segment=None, rep_id=None) -> QuotaSelection:
    sel = select_quota(snap, quarter, key, segment, rep_id)
    sel.title = "%s %d quota" % (quarter, YEAR)
    return sel


def _future_wins(snap: Snapshot, quarter: str, key: str, segment=None,
                 rep_id=None) -> Optional[DealSelection]:
    _, end = QUARTERS[quarter]
    if snap.as_of is None or snap.as_of >= end:
        return None
    sel = select_deals(
        snap, _filters([WON], snap.as_of + pd.Timedelta(days=1), end, segment, rep_id), key,
        "Closed Won deals dated after the snapshot (left out of bookings)")
    return sel if sel.count or not sel.complete else None


def _open_outside(snap: Snapshot, quarter: str, key: str, segment=None,
                  rep_id=None) -> Optional[DealSelection]:
    start, end = QUARTERS[quarter]
    fs = [Filter.one_of("stage", OPEN_STAGES), Filter.date_outside("close_date", start, end)]
    if segment:
        fs.append(Filter.equals("segment", segment))
    if rep_id:
        fs.append(Filter.equals("rep_id", rep_id))
    sel = select_deals(snap, fs, key, "Open deals with close dates outside %s (not counted)" % quarter)
    return sel if sel.count else None


def _term_deals(sel: DealSelection, label: str) -> Term:
    return Term(label, sel.total,
                "sum of deal_value over %d row%s in [%s]" % (sel.count, _s(sel.count), sel.key),
                lower_bound=not sel.complete)


def _term_quota(sel: QuotaSelection) -> Term:
    total = sel.total
    reason = sel.undefined_reason
    return Term("[%s] quota" % sel.key, total,
                "sum of %s over %d rep%s in [%s]" % (sel.column, len(sel.rows), _s(len(sel.rows)), sel.key),
                undefined_reason=reason)


# ---------------------------------------------------------------------------
# flags
# ---------------------------------------------------------------------------

def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _id_list(rows: pd.DataFrame, extra: Optional[str] = None) -> str:
    parts = []
    for _, r in rows.iterrows():
        label = r.deal_id if isinstance(r.deal_id, str) else "unnamed row"
        detail = "line %d" % r._line
        if extra == "close_date" and not pd.isna(r.close_date):
            detail = "%s, closes %s" % (detail, r.close_date.date())
        if extra == "value":
            detail = "%s, %s" % (detail, money(r.deal_value))
        parts.append("%s (%s)" % (label, detail))
    return ", ".join(parts)


def unplaced_flag(sel) -> Optional[Flag]:
    if sel is None or sel.kind != "deals" or sel.complete:
        return None
    parts = []
    for _, r in sel.unplaced.iterrows():
        label = r.deal_id if isinstance(r.deal_id, str) else "unnamed row"
        parts.append("%s (line %d: %s)" % (label, r._line, r._reason))
    return Flag(
        "UNPLACED_ROWS", "critical",
        "[%s] %d row%s could match these filters but can't be counted: %s. They "
        "are left out rather than counted as $0, so [%s] is incomplete and every "
        "figure built on it is a lower bound." % (
            sel.key, len(sel.unplaced), _s(len(sel.unplaced)), "; ".join(parts), sel.key),
        deal_ids=[d for d in sel.unplaced.deal_id if isinstance(d, str)])


def quota_flag(sel: QuotaSelection) -> Optional[Flag]:
    total = sel.total
    if total is None:
        return Flag(
            "MISSING_QUOTA", "critical",
            "[%s] %s, so the quota total is unknown. Ratios that use it are reported "
            "as undefined, not as 0%%." % (sel.key, _cap(sel.undefined_reason)))
    if total == 0:
        return Flag(
            "ZERO_QUOTA", "critical",
            "[%s] Quota totals $0 across %s, so attainment and coverage are "
            "undefined, not 0%% or infinite." % (sel.key, ", ".join(sel.rows.rep_id)))
    return None


def no_rows_flag(sel: DealSelection, what: str) -> Optional[Flag]:
    if sel.count or not sel.complete:
        return None
    return Flag(
        "NO_MATCHING_DEALS", "context",
        "[%s] No %s matched these filters. The $0 is a count of zero deals in a "
        "complete file, not missing data." % (sel.key, what))


def slipped_flag(snaps: SnapshotSet, sel: DealSelection) -> Optional[Flag]:
    if sel.snapshot.name != "live":
        return None
    ids = sorted(set(sel.ids) & snaps.slipped_ids())
    if not ids:
        return None
    rows = sel.rows[sel.rows.deal_id.isin(ids)]
    return Flag(
        "SLIPPED_ACROSS_BOUNDARY", "critical",
        "%s of [%s] is %d deal%s that %s Closed Won in Q1 in the extract taken at "
        "Q1 close, and now carr%s a Q2 close date: %s. %s counted in Q2 because "
        "that's what the live extract shows, but %s not new Q2 business." % (
            money(rows.deal_value.sum()), sel.key, len(ids), _s(len(ids)),
            "was" if len(ids) == 1 else "were", "ies" if len(ids) == 1 else "y",
            _id_list(rows, "close_date"), "It is" if len(ids) == 1 else "They are",
            "it is" if len(ids) == 1 else "they are"),
        deal_ids=ids)


def recycled_flag(snaps: SnapshotSet, sel: DealSelection) -> Optional[Flag]:
    if sel.snapshot.name != "live" or not sel.count:
        return None
    ids = sorted(set(sel.ids) & snaps.recycled_ids())
    if not ids:
        return None
    amount = int(sel.rows[sel.rows.deal_id.isin(ids)].deal_value.sum())
    share = amount / sel.total if sel.total else None
    return Flag(
        "RECYCLED_DEAL_ID", "warning",
        "%s in [%s] (%s of it) sits on %d deal ID%s that belonged to a different "
        "deal in the extract taken at Q1 close: %s. Values are taken from the live "
        "extract as they stand, but the history behind these IDs can't be verified." % (
            money(amount), sel.key, pct(share, 0), len(ids), _s(len(ids)), ", ".join(ids)),
        deal_ids=ids)


def stale_flag(sel: DealSelection) -> Optional[Flag]:
    as_of = sel.snapshot.as_of
    rows = sel.rows[sel.rows.stage.isin(OPEN_STAGES) & (sel.rows.close_date < as_of)]
    if rows.empty:
        return None
    return Flag(
        "STALE_CLOSE_DATE", "warning",
        "%d open deal%s in [%s] worth %s %s close date%s that had already passed "
        "by the snapshot date (%s): %s. %s still counted as open pipeline, but the "
        "close date%s %s out of date." % (
            len(rows), _s(len(rows)), sel.key, money(rows.deal_value.sum()),
            "has a" if len(rows) == 1 else "have", "" if len(rows) == 1 else "s",
            as_of.date(), _id_list(rows, "close_date"),
            "It is" if len(rows) == 1 else "They are", "" if len(rows) == 1 else "s",
            "is" if len(rows) == 1 else "are"),
        deal_ids=list(rows.deal_id))


def missing_loss_flag(sel: DealSelection) -> Optional[Flag]:
    rows = sel.rows[(sel.rows.stage == LOST) & sel.rows.loss_reason.isna()]
    if rows.empty:
        return None
    return Flag(
        "MISSING_LOSS_REASON", "warning",
        "%d Closed Lost deal%s in [%s] %s no loss reason: %s. %s shown as MISSING "
        "rather than left blank." % (
            len(rows), _s(len(rows)), sel.key, "has" if len(rows) == 1 else "have",
            _id_list(rows), "It is" if len(rows) == 1 else "They are"),
        deal_ids=list(rows.deal_id))


def future_win_flag(sel: Optional[DealSelection], bookings: Term, quota: Term) -> Optional[Flag]:
    if sel is None:
        return None
    extra = ""
    if quota.value:
        extra = " Counting them anyway would make bookings %s (%s of quota)." % (
            money(bookings.value + sel.total), pct((bookings.value + sel.total) / quota.value))
    return Flag(
        "FUTURE_DATED_WIN", "critical",
        "[%s] %d deal%s worth %s %s marked Closed Won with a close date after the "
        "snapshot date (%s): %s. A deal can't have closed on a date that hadn't "
        "happened when the data was extracted, so %s left out of bookings. Either "
        "the stage or the close date is wrong.%s" % (
            sel.key, sel.count, _s(sel.count), money(sel.total),
            "is" if sel.count == 1 else "are", sel.snapshot.as_of.date(),
            _id_list(sel.rows, "close_date"), "it is" if sel.count == 1 else "they are",
            extra),
        deal_ids=sel.ids)


def outside_quarter_flag(sel: Optional[DealSelection], quarter: str) -> Optional[Flag]:
    if sel is None:
        return None
    return Flag(
        "OPEN_OUTSIDE_QUARTER", "warning",
        "[%s] %d open deal%s worth %s %s close date%s outside %s %d: %s. %s not "
        "counted toward %s pipeline." % (
            sel.key, sel.count, _s(sel.count), money(sel.total),
            "has a" if sel.count == 1 else "have", "" if sel.count == 1 else "s",
            quarter, YEAR, _id_list(sel.rows, "close_date"),
            "It is" if sel.count == 1 else "They are", quarter),
        deal_ids=sel.ids)


def snapshot_conflict_flag(snaps: SnapshotSet, sel: DealSelection, level: str,
                           note: str = "") -> Optional[Flag]:
    """Only raised when the other extract gives a different figure for the same
    filters, so it never fires on numbers the conflict doesn't touch."""
    other = snaps.live if sel.snapshot.name == "at_q1_close" else snaps.at_q1_close
    alt = select_deals(other, sel.filters, sel.key + "'", sel.title)
    if alt.total == sel.total and alt.complete == sel.complete:
        return None
    return Flag(
        "SNAPSHOT_CONFLICT", level,
        "[%s] totals %s in the %s but %s in the %s, for exactly the same filters. "
        "This answer uses the %s.%s Run `python ask.py audit` for the reconciliation." % (
            sel.key, money(sel.total), _extract(sel.snapshot), money(alt.total),
            _extract(other), _extract(sel.snapshot), note))


def _extract(snap: Snapshot) -> str:
    return "extract taken at Q1 close" if snap.name == "at_q1_close" else "live extract"


# ---------------------------------------------------------------------------
# assumptions
# ---------------------------------------------------------------------------

def _bookings_def(quarter: str, through: pd.Timestamp) -> Assumption:
    start, _ = QUARTERS[quarter]
    return Assumption(
        "BOOKINGS",
        "Bookings are the deal_value of Closed Won deals with a close date from %s "
        "to %s. No other stage counts toward bookings." % (start.date(), through.date()))


def _precedence(snap: Snapshot, quarter: str) -> Assumption:
    if snap.name == "live":
        text = ("%s %d is in progress, so it is read from the latest extract, %s (as of "
                "%s, %s)." % (quarter, YEAR, snap.deals_file, snap.as_of.date(), snap.as_of_basis))
    else:
        text = ("%s %d is closed, so it is read from the extract taken at quarter close, "
                "%s (as of %s, %s)." % (quarter, YEAR, snap.deals_file, snap.as_of.date(),
                                        snap.as_of_basis))
    return Assumption("SNAPSHOT_" + quarter, text)


def _at_risk_def() -> Assumption:
    return Assumption("AT_RISK", "\"At risk\" means closed-won plus open pipeline is below quota.")


def _coverage_only() -> Assumption:
    return Assumption(
        "AT_RISK_COVERAGE_ONLY",
        "The at-risk verdict uses coverage alone. Other risk factors are shown, not scored: "
        "bookings moved from Q1, pipeline on recycled deal IDs, open deals past their close "
        "date, and (under \"Why is <rep> at risk?\") how much pipeline sits in early stages "
        "or in one deal. Blending them into one score would need weights this data can't "
        "calibrate: the extracts hold each deal's current stage but not its stage history, "
        "so there is no stage-by-stage win rate, and loss reasons were erased on reopened "
        "deals. A score built on guessed weights would be harder to check than the "
        "factors themselves.")


def _not_weighted() -> Assumption:
    return Assumption(
        "PIPELINE_NOT_WEIGHTED",
        strong("Open pipeline is not probability-weighted: every open deal counts at full "
               "value, whatever its stage."))


def _attainment_def(in_progress: bool) -> Assumption:
    text = ("Attainment means closed-won bookings divided by quota. It counts only deals "
            "already won; open pipeline is not part of it.")
    if in_progress:
        text += (" The quarter is still in progress, so attainment is paired with coverage: "
                 "closed-won plus open pipeline, divided by quota.")
    return Assumption("ATTAINMENT", text)


def _open_def(quarter: str) -> Assumption:
    start, end = QUARTERS[quarter]
    return Assumption(
        "OPEN_PIPELINE",
        "Open pipeline is deals in Prospecting, Discovery, Proposal or Negotiation "
        "with a close date from %s to %s. Deals whose close date has already "
        "passed still count, and are flagged." % (start.date(), end.date()))


def _segment_source(snap: Snapshot) -> Optional[Assumption]:
    conflicts = audit.dimension_conflicts(snap)["segment"]
    if conflicts:
        return None
    return Assumption(
        "SEGMENT_SOURCE",
        "Segment comes from each deal's own segment field. It matches the owning "
        "rep's segment on all %d countable deals in %s." % (len(snap.clean), snap.deals_file))


def _elapsed(vq) -> str:
    day = day_of_quarter(vq.quarter, vq.through)
    length = quarter_length(vq.quarter)
    return "day %d of %d (%s)" % (day, length, pct(day / length))


# ---------------------------------------------------------------------------
# attainment
# ---------------------------------------------------------------------------

def attainment(vq, snaps: SnapshotSet) -> Answer:
    if vq.through < QUARTERS[vq.quarter][1]:
        return _tracking(vq, snaps)
    ans = Answer(vq)
    snap = snaps.for_quarter(vq.quarter)
    closed = vq.through >= QUARTERS[vq.quarter][1]

    A = ans.dataset(_won(snap, vq.quarter, vq.through, "A", vq.segment, vq.rep_id))
    B = ans.dataset(_quota(snap, vq.quarter, "B", vq.segment, vq.rep_id))
    X = ans.dataset(_future_wins(snap, vq.quarter, "X", vq.segment, vq.rep_id))

    booked = _term_deals(A, "[A] closed-won bookings")
    quota = _term_quota(B)
    calc = divide("attainment", "[A] closed-won bookings / [B] quota", booked, quota, "pct")
    ans.calculations.append(calc)

    label = vq.rep_name or vq.segment or "Meridian (all segments)"
    if calc.result is None:
        ans.headline = "%s has booked %s in %s, but attainment is undefined: %s." % (
            label, money(booked.value, booked.lower_bound), vq.period, calc.undefined_reason)
    elif closed:
        ans.headline = "%s finished %s with %s booked against a %s quota: %s attainment." % (
            label, vq.period, money(booked.value, booked.lower_bound), money(quota.value),
            calc.display(1))
    else:
        ans.headline = ("%s has booked %s against a %s %s quota: %s attainment through "
                        "%s, %s." % (label, money(booked.value, booked.lower_bound),
                                     money(quota.value), vq.quarter, calc.display(1),
                                     vq.through.date(), _elapsed(vq)))

    if A.count == 1:
        r = A.rows.iloc[0]
        ans.lines.append("That is a single deal: %s, %s (%s)." % (r.deal_id, cell(r.account_name), r.rep_id))
    elif A.count:
        ans.lines.append("That is %d closed-won deals." % A.count)

    slipped = slipped_flag(snaps, A)
    slipped_amount = 0
    if slipped:
        slipped_amount = int(A.rows[A.rows.deal_id.isin(slipped.deal_ids)].deal_value.sum())
        net = booked.value - slipped_amount
        net_pct = pct(net / quota.value) if quota.value else "undefined"
        ans.lines.append(
            "%s of it was won in Q1 and moved into Q2. Excluding moved deals, %s "
            "bookings are %s (%s)." % (
                "All" if net == 0 else money(slipped_amount), vq.quarter, money(net), net_pct))
        ans.calculations.append(Calculation(
            "bookings excluding deals moved from Q1",
            "[A] closed-won bookings - moved deals (%s)" % ", ".join(slipped.deal_ids),
            booked, Term("moved deals", slipped_amount, "sum of deal_value over %s in [A]"
                         % ", ".join(slipped.deal_ids)),
            net, "money"))

    ans.metrics += [
        Metric("Closed-won bookings", money(booked.value, booked.lower_bound),
               "%d deal%s" % (A.count, _s(A.count))),
        Metric("Quota", money(quota.value), quota.undefined_reason or ""),
        Metric("Attainment", calc.display(1)),
    ]
    if slipped:
        ans.metrics.append(Metric("Bookings excluding deals moved from Q1",
                                  money(booked.value - slipped_amount)))
    if not closed:
        ans.metrics.append(Metric("Quarter elapsed", _elapsed(vq)))

    if not vq.segment and not vq.rep_id:
        rows = []
        for seg in ("Enterprise", "Mid-Market", "SMB"):
            seg_quota = select_quota(snap, vq.quarter, "B", seg)
            seg_total = seg_quota.total
            seg_booked = A.total_for("segment", seg)
            rows.append([seg, money(seg_booked), str(A.count_for("segment", seg)),
                         money(seg_total),
                         pct(seg_booked / seg_total) if seg_total else "undefined"])
        rows.append(["Total", money(booked.value, booked.lower_bound), str(A.count),
                     money(quota.value), calc.display(1)])
        ans.tables.append(Table("Attainment by segment",
                                ["Segment", "Bookings [A]", "Deals", "Quota [B]", "Attainment"],
                                rows, numeric=["Bookings [A]", "Deals", "Quota [B]", "Attainment"]))

    ans.flag(unplaced_flag(A))
    ans.flag(quota_flag(B))
    ans.flag(future_win_flag(X, booked, quota))
    ans.flag(slipped)
    ans.flag(no_rows_flag(A, "closed-won deals"))
    if vq.quarter == "Q1":
        ans.flag(snapshot_conflict_flag(snaps, A, "critical"))

    ans.assume(_attainment_def(False))
    ans.assume(_bookings_def(vq.quarter, vq.through))
    ans.assume(_precedence(snap, vq.quarter))
    if vq.segment:
        a = _segment_source(snap)
        if a:
            ans.assume(a)

    ans.values.update(bookings=booked.value, quota=quota.value, attainment=calc.result,
                      deal_ids=A.ids, slipped=slipped_amount)
    return ans


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------

def _rep_split(B: QuotaSelection, A: DealSelection, C: DealSelection) -> List[dict]:
    """Per-rep closed-won, pipeline and coverage inside a segment or the org."""
    out = []
    for _, rep in pd.concat([B.rows, B.missing]).iterrows():
        rid = rep.rep_id
        won, opn, q = A.total_for("rep_id", rid), C.total_for("rep_id", rid), B.quota_for(rid)
        cov = (won + opn) / q if q else None
        out.append(dict(rep_id=rid, name=cell(rep.rep_name), won=won, open=opn, quota=q,
                        coverage=cov, shortfall=(q - won - opn) if q is not None else None,
                        verdict=("unknown" if not q else "at risk" if won + opn < q else "not at risk")))
    out.sort(key=lambda r: (r["coverage"] is None, r["coverage"] or 0))
    return out


def _join(parts: List[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    if len(parts) == 2:
        return parts[0] + " and " + parts[1]
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _tracking(vq, snaps: SnapshotSet) -> Answer:
    """How a segment, rep or the org is tracking in a quarter still in progress.

    Bookings alone say little mid-quarter, so this answer always pairs them with
    open pipeline, the at-risk test, and (for a segment or the org) the split by
    rep, because a covered total can hide reps who are short.
    """
    ans = Answer(vq)
    snap = snaps.for_quarter(vq.quarter)
    A, B, C, booked, pipeline, quota, combined, cov, shortfall, verdict = _coverage_core(
        ans, vq, snaps, vq.segment, vq.rep_id)
    att = divide("attainment", "[A] closed-won bookings / [B] quota", booked, quota, "pct")
    ans.calculations.insert(0, att)
    ans.assume(_attainment_def(True))
    label = vq.rep_name or vq.segment or "Meridian"
    whose = "its" if not vq.rep_name else "the"

    slipped = next((f for f in ans.flags if f.code == "SLIPPED_ACROSS_BOUNDARY"), None)
    moved = int(A.rows[A.rows.deal_id.isin(slipped.deal_ids)].deal_value.sum()) if slipped else 0
    recycled = next((f for f in ans.flags if f.code == "RECYCLED_DEAL_ID"), None)
    recycled_amount = int(C.rows[C.rows.deal_id.isin(recycled.deal_ids)].deal_value.sum()) if recycled else 0
    split = _rep_split(B, A, C) if not vq.rep_id else []
    short = [r for r in split if r["verdict"] == "at risk"]
    over = [r for r in split if r["verdict"] == "not at risk"]

    # ---- headline
    if att.result is None or cov.result is None:
        ans.headline = "%s has booked %s in %s, but attainment and coverage are undefined: %s." % (
            label, money(booked.value, booked.lower_bound), vq.period, att.undefined_reason)
    elif verdict == "unknown":
        ans.headline = ("%s has booked %s of %s %s quota. With the open pipeline that can be counted it "
                        "reaches %s, but some rows couldn't be counted, so whether it is covered "
                        "can't be determined." % (label, att.display(1), whose, vq.quarter,
                                                  cov.display(2)))
    else:
        cushion = -shortfall
        clauses = []
        if verdict == "not at risk":
            bigger = int((C.rows.deal_value > cushion).sum())
            clauses.append("technically covered, with no room for slippage" if bigger
                           else "covered, with room for any single deal to slip")
            lead = "%s has booked %s of %s %s quota, but with open pipeline it's at %s: " % (
                label, att.display(1), whose, vq.quarter, cov.display(2))
        else:
            bigger = 0
            clauses.append("%s short even if every open deal closes" % money(shortfall))
            lead = "%s has booked %s of %s %s quota, and even with open pipeline it's only at %s: " % (
                label, att.display(1), whose, vq.quarter, cov.display(2))
        ans.headline = lead + _join(clauses) + "."

        # ---- supporting lines
        booked_line = "Booked: %s against a %s quota through %s, %s." % (
            money(booked.value, booked.lower_bound), money(quota.value), vq.through.date(), _elapsed(vq))
        if A.count == 1:
            r = A.rows.iloc[0]
            booked_line += " That is a single deal: %s, %s (%s)." % (r.deal_id, cell(r.account_name), r.rep_id)
        ans.lines.append(booked_line)
        if moved:
            ans.lines.append(strong("%s of it was won in Q1 and moved into Q2; excluding moved deals, %s bookings are %s." % (
                "All" if moved == booked.value else money(moved), vq.quarter, money(booked.value - moved))))
        if verdict == "not at risk":
            ans.lines.append(
                "Open pipeline: %s across %d deal%s. Booked plus pipeline is %s, a %s cushion over quota%s." % (
                    money(pipeline.value), C.count, _s(C.count), money(combined.value), money(cushion),
                    ("; losing any one of the %d open deal%s larger than that would put %s below quota"
                     % (bigger, _s(bigger), label)) if bigger else ""))
        else:
            ans.lines.append(
                "Open pipeline: %s across %d deal%s. Booked plus pipeline is %s, %s short of quota even if "
                "every open deal closes at full value." % (
                    money(pipeline.value), C.count, _s(C.count), money(combined.value), money(shortfall)))
        if recycled_amount:
            ans.lines.append("%s of that pipeline (%s) sits on deal IDs that belonged to different Q1 deals." % (
                money(recycled_amount), pct(recycled_amount / pipeline.value, 0)))
        if short:
            ans.lines.append("Reps short: %s." % ", ".join(
                "%s (%s, %s short)" % (r["name"], times(r["coverage"]), money(r["shortfall"])) for r in short))

    # ---- rep masking flag
    if verdict == "not at risk" and short and over:
        ans.flag(Flag(
            "REPS_SHORT_WITHIN_TOTAL", "warning",
            "%s is covered in total, but %d of %s %d reps are short: %s. The total hides this "
            "because %s %s more pipeline than %s quota%s need%s." % (
                label, len(short), whose, len(split),
                ", ".join("%s (%s)" % (r["name"], times(r["coverage"])) for r in short),
                _join(["%s (%s)" % (r["name"], times(r["coverage"])) for r in over]) if len(over) > 1
                else "%s (%s)" % (over[0]["name"], times(over[0]["coverage"])),
                "have" if len(over) > 1 else "has", "their" if len(over) > 1 else "their",
                "s" if len(over) > 1 else "", "" if len(over) > 1 else "s")))
    ans.flag(no_rows_flag(A, "closed-won deals"))

    # ---- metrics and tables
    ans.metrics += [
        Metric("Closed-won bookings [A]", money(booked.value, booked.lower_bound),
               "%d deal%s" % (A.count, _s(A.count))),
        Metric("Quota [B]", money(quota.value), quota.undefined_reason or ""),
        Metric("Attainment", att.display(1)),
        Metric("Open pipeline [C]", money(pipeline.value, pipeline.lower_bound),
               "%d deal%s" % (C.count, _s(C.count))),
        Metric("Booked + open pipeline", money(combined.value, combined.lower_bound)),
        Metric("Coverage", cov.display(2)),
        Metric("Cushion" if (shortfall or 0) < 0 else "Shortfall",
               money(abs(shortfall)) if shortfall is not None else "unknown"),
        Metric("Quarter elapsed", _elapsed(vq)),
    ]
    if moved:
        ans.metrics.append(Metric("Bookings excluding deals moved from Q1", money(booked.value - moved)))
    if split:
        ans.tables.append(Table(
            "%s reps [A] [B] [C]" % (vq.segment or "All"),
            ["Rep", "ID", "Won [A]", "Open [C]", "Quota [B]", "Coverage", "Shortfall (-: cushion)", "Verdict"],
            [[r["name"], r["rep_id"], money(r["won"]), money(r["open"]), money(r["quota"]),
              times(r["coverage"]), money(r["shortfall"]) if r["shortfall"] is not None else "unknown",
              r["verdict"]] for r in split],
            numeric=["Won [A]", "Open [C]", "Quota [B]", "Coverage", "Shortfall (-: cushion)"]))
    ans.tables.append(_stage_table(C, "Open pipeline by stage [C]"))
    if vq.segment:
        a = _segment_source(snap)
        if a:
            ans.assume(a)

    ans.values.update(bookings=booked.value, quota=quota.value, attainment=att.result,
                      deal_ids=A.ids, slipped=moved, pipeline=pipeline.value, coverage=cov.result,
                      shortfall=shortfall, verdict=verdict, reps_short=[r["rep_id"] for r in short],
                      recycled_pipeline=recycled_amount)
    return ans


def _coverage_core(ans: Answer, vq, snaps: SnapshotSet, segment=None, rep_id=None):
    snap = snaps.for_quarter(vq.quarter)
    A = ans.dataset(_won(snap, vq.quarter, vq.through, "A", segment, rep_id))
    C = ans.dataset(_open(snap, vq.quarter, "C", segment, rep_id))
    B = ans.dataset(_quota(snap, vq.quarter, "B", segment, rep_id))
    X = ans.dataset(_future_wins(snap, vq.quarter, "X", segment, rep_id))
    Y = ans.dataset(_open_outside(snap, vq.quarter, "Y", segment, rep_id))

    booked = _term_deals(A, "[A] closed-won bookings")
    pipeline = _term_deals(C, "[C] open pipeline")
    quota = _term_quota(B)
    combined = Term("[A] closed-won + [C] open pipeline", booked.value + pipeline.value,
                    "%s + %s" % (money(booked.value), money(pipeline.value)),
                    lower_bound=booked.lower_bound or pipeline.lower_bound)
    cov = divide("coverage", "([A] closed-won + [C] open pipeline) / [B] quota",
                 combined, quota, "x")
    ans.calculations.append(cov)

    shortfall = None
    if quota.value is not None:
        shortfall = quota.value - combined.value
        ans.calculations.append(Calculation(
            "shortfall", "[B] quota - ([A] closed-won + [C] open pipeline)",
            quota, combined, shortfall, "money"))

    verdict = None
    if cov.result is not None:
        verdict = "at risk" if combined.value < quota.value else "not at risk"
        if combined.lower_bound and verdict == "at risk":
            verdict = "unknown"

    for f in (unplaced_flag(A), unplaced_flag(C), quota_flag(B),
              future_win_flag(X, booked, quota), outside_quarter_flag(Y, vq.quarter),
              slipped_flag(snaps, A), stale_flag(C), recycled_flag(snaps, C)):
        ans.flag(f)
    ans.assume(_at_risk_def())
    ans.assume(_open_def(vq.quarter))
    ans.assume(_not_weighted())
    ans.assume(_bookings_def(vq.quarter, vq.through))
    ans.assume(_precedence(snap, vq.quarter))
    return A, B, C, booked, pipeline, quota, combined, cov, shortfall, verdict


def _verdict_words(vq, verdict: str) -> str:
    if "on track" not in vq.raw.phrases:
        return verdict
    return {"at risk": "not on track", "not at risk": "on track"}.get(verdict, verdict)


def _stage_table(C: DealSelection, title: str) -> Table:
    rows = []
    for stage in OPEN_STAGES:
        n = C.count_for("stage", stage)
        if n:
            value = C.total_for("stage", stage)
            rows.append([stage, str(n), money(value), pct(value / C.total if C.total else None, 0)])
    rows.append(["Total", str(C.count), money(C.total, not C.complete), "100%" if C.total else "-"])
    return Table(title, ["Stage", "Deals", "Value", "Share"], rows,
                 numeric=["Deals", "Value", "Share"])


def coverage(vq, snaps: SnapshotSet) -> Answer:
    ans = Answer(vq)
    A, B, C, booked, pipeline, quota, combined, cov, shortfall, verdict = _coverage_core(
        ans, vq, snaps, vq.segment, vq.rep_id)
    label = vq.rep_name or vq.segment or "Meridian (all segments)"

    if verdict is None:
        ans.headline = "%s's coverage is undefined: %s." % (label, cov.undefined_reason)
    elif verdict == "unknown":
        ans.headline = ("%s can't be assessed: some rows couldn't be counted, and what "
                        "can be counted is %s against a %s quota." % (
                            label, money(combined.value, True), money(quota.value)))
    else:
        words = _verdict_words(vq, verdict)
        gap = ("%s short" % money(shortfall) if shortfall > 0
               else "a %s cushion" % money(-shortfall) if shortfall < 0 else "exactly at quota")
        ans.headline = ("%s is %s: closed-won plus open pipeline is %s against a %s %s "
                        "quota (%s coverage), %s." % (
                            label, words, money(combined.value), money(quota.value),
                            vq.quarter, cov.display(2), gap))
        ans.lines.append("That assumes every open deal closes at full value by %s." %
                         QUARTERS[vq.quarter][1].date())

    remaining = None
    if quota.value is not None:
        remaining = quota.value - booked.value
        if remaining > 0:
            rem = divide("open pipeline against remaining quota",
                         "[C] open pipeline / ([B] quota - [A] closed-won)", pipeline,
                         Term("remaining quota", remaining, "%s - %s" % (
                             money(quota.value), money(booked.value))), "x")
            ans.calculations.append(rem)

    days_left = int((QUARTERS[vq.quarter][1] - vq.as_of).days)
    ans.metrics += [
        Metric("Closed-won to date [A]", money(booked.value, booked.lower_bound),
               "%d deal%s" % (A.count, _s(A.count))),
        Metric("Open pipeline [C]", money(pipeline.value, pipeline.lower_bound),
               "%d deal%s" % (C.count, _s(C.count))),
        Metric("Closed-won + open", money(combined.value, combined.lower_bound)),
        Metric("Quota [B]", money(quota.value), quota.undefined_reason or ""),
        Metric("Coverage", cov.display(2)),
        Metric("Shortfall" if (shortfall or 0) >= 0 else "Cushion",
               money(abs(shortfall)) if shortfall is not None else "unknown"),
        Metric("Quota still to book", money(remaining) if remaining is not None else "unknown"),
        Metric("Days left in %s" % vq.quarter, str(days_left)),
    ]
    ans.tables.append(_stage_table(C, "Open pipeline by stage [C]"))
    ans.values.update(bookings=booked.value, pipeline=pipeline.value, quota=quota.value,
                      coverage=cov.result, shortfall=shortfall, verdict=verdict)
    return ans


# ---------------------------------------------------------------------------
# rep risk
# ---------------------------------------------------------------------------

def rep_risk(vq, snaps: SnapshotSet) -> Answer:
    if vq.rep_id:
        return _rep_why(vq, snaps)
    return _rep_ranking(vq, snaps)


def _rep_ranking(vq, snaps: SnapshotSet) -> Answer:
    ans = Answer(vq)
    snap = snaps.for_quarter(vq.quarter)
    A = ans.dataset(_won(snap, vq.quarter, vq.through, "A", vq.segment))
    C = ans.dataset(_open(snap, vq.quarter, "C", vq.segment))
    B = ans.dataset(_quota(snap, vq.quarter, "B", vq.segment))
    X = ans.dataset(_future_wins(snap, vq.quarter, "X", vq.segment))
    Y = ans.dataset(_open_outside(snap, vq.quarter, "Y", vq.segment))

    roster = pd.concat([B.rows, B.missing])
    unplaced_reps = set(A.unplaced.rep_id.dropna()) | set(C.unplaced.rep_id.dropna())
    slipped_ids = snaps.slipped_ids()

    records = []
    for _, rep in roster.iterrows():
        rid = rep.rep_id
        won = A.total_for("rep_id", rid)
        opn = C.total_for("rep_id", rid)
        q = B.quota_for(rid)
        cov = (won + opn) / q if q else None
        if q is None:
            verdict = "unknown: quota missing"
        elif q == 0:
            verdict = "unknown: quota $0"
        elif rid in unplaced_reps and won + opn < q:
            verdict = "unknown: rows uncounted"
        else:
            verdict = "at risk" if won + opn < q else "not at risk"
        moved = A.rows[(A.rows.rep_id == rid) & A.rows.deal_id.isin(slipped_ids)]
        records.append(dict(
            rep_id=rid, name=rep.rep_name, segment=rep.segment, won=won,
            won_n=A.count_for("rep_id", rid), open=opn, open_n=C.count_for("rep_id", rid),
            quota=q, coverage=cov, shortfall=(q - won - opn) if q is not None else None,
            verdict=verdict, moved=int(moved.deal_value.sum()) if len(moved) else 0))
    records.sort(key=lambda r: (r["coverage"] is None, r["coverage"] if r["coverage"] is not None else 0))

    at_risk = [r for r in records if r["verdict"] == "at risk"]
    unknown = [r for r in records if r["verdict"].startswith("unknown")]
    scope = "%s reps" % vq.segment if vq.segment else "reps"
    status = ("not on track for" if "on track" in vq.raw.phrases else "at risk of missing")
    ans.headline = "%d of %d %s %s %s %s: for each, closed-won plus open pipeline is below quota." % (
        len(at_risk), len(records), scope, "is" if len(at_risk) == 1 else "are", status, vq.period)
    if unknown:
        ans.headline = ans.headline[:-1] + "; %d can't be assessed." % len(unknown)

    if at_risk:
        worst = max(at_risk, key=lambda r: r["shortfall"])
        closest = min(at_risk, key=lambda r: r["shortfall"])
        ans.lines.append("Largest shortfall: %s, %s of quota with no pipeline behind it (%s)." % (
            worst["name"], money(worst["shortfall"]), times(worst["coverage"])))
        if closest is not worst:
            ans.lines.append("Closest to the line: %s, %s short (%s)." % (
                closest["name"], money(closest["shortfall"]), times(closest["coverage"])))
    safe = [r for r in records if r["verdict"] == "not at risk"]
    if safe:
        ans.lines.append("Not at risk: %s." % ", ".join(
            "%s (%s)" % (r["name"], times(r["coverage"])) for r in safe))

    rows = []
    for r in records:
        rows.append([r["name"], r["rep_id"], r["segment"],
                     "%s (%d)" % (money(r["won"]), r["won_n"]),
                     "%s (%d)" % (money(r["open"]), r["open_n"]),
                     money(r["quota"]), times(r["coverage"]),
                     money(r["shortfall"]) if r["shortfall"] is not None else "unknown",
                     r["verdict"]])
    ans.tables.append(Table(
        "Reps ranked by coverage (deal counts in brackets)",
        ["Rep", "ID", "Segment", "Won [A]", "Open [C]", "Quota [B]", "Coverage",
         "Shortfall (-: cushion)", "Verdict"], rows,
        numeric=["Won [A]", "Open [C]", "Quota [B]", "Coverage", "Shortfall (-: cushion)"]))

    if at_risk:
        w = max(at_risk, key=lambda r: r["shortfall"])
        ans.calculations.append(divide(
            "coverage for %s (worked example; every row in the table uses the same formula)" % w["name"],
            "([A] won + [C] open for %s) / [B] quota for %s" % (w["rep_id"], w["rep_id"]),
            Term("[A] + [C] for %s" % w["rep_id"], w["won"] + w["open"],
                 "%s + %s" % (money(w["won"]), money(w["open"]))),
            Term("[B] quota for %s" % w["rep_id"], w["quota"], "%s for %s" % (B.column, w["rep_id"])),
            "x"))

    for f in (unplaced_flag(A), unplaced_flag(C), future_win_flag(
                  X, _term_deals(A, "bookings"), _term_quota(B)),
              outside_quarter_flag(Y, vq.quarter), stale_flag(C), recycled_flag(snaps, C)):
        ans.flag(f)
    if not B.missing.empty:
        ans.flag(Flag("MISSING_QUOTA", "critical",
                      "[B] %s. Those reps are shown as \"unknown\", not as at risk "
                      "or safe." % _cap(B.undefined_reason)))
    zero = [r for r in records if r["quota"] == 0]
    if zero:
        ans.flag(Flag("ZERO_QUOTA", "critical",
                      "[B] Quota is $0 for %s, so coverage is undefined for %s." % (
                          ", ".join(r["rep_id"] for r in zero),
                          "that rep" if len(zero) == 1 else "those reps")))
    moved = [r for r in records if r["moved"]]
    if moved:
        detail = []
        for r in moved:
            without = (r["won"] - r["moved"] + r["open"]) / r["quota"] if r["quota"] else None
            detail.append("%s's %s closed-won includes %s moved from Q1 (%s without it)" % (
                r["name"], money(r["won"]), money(r["moved"]), times(without)))
        ids = sorted(set(A.ids) & slipped_ids)
        ans.flag(Flag("SLIPPED_ACROSS_BOUNDARY", "critical",
                      "%s. Those deals were Closed Won in Q1 in the extract taken at Q1 "
                      "close and now carry Q2 close dates: %s." % ("; ".join(detail), ", ".join(ids)),
                      deal_ids=ids))

    ans.assume(_at_risk_def())
    ans.assume(_coverage_only())
    ans.assume(_open_def(vq.quarter))
    ans.assume(_not_weighted())
    ans.assume(_bookings_def(vq.quarter, vq.through))
    ans.assume(_precedence(snap, vq.quarter))
    ans.assume(Assumption("ROSTER", "Reps are everyone on the roster in %s%s." % (
        snap.reps_file, " with segment %s" % vq.segment if vq.segment else "")))

    ans.values.update(records=records, at_risk=[r["rep_id"] for r in at_risk])
    return ans


def _rep_why(vq, snaps: SnapshotSet) -> Answer:
    ans = Answer(vq)
    snap = snaps.for_quarter(vq.quarter)
    A, B, C, booked, pipeline, quota, combined, cov, shortfall, verdict = _coverage_core(
        ans, vq, snaps, rep_id=vq.rep_id)
    start, end = QUARTERS[vq.quarter]
    L = ans.dataset(select_deals(snap, _filters([LOST], start, end, rep_id=vq.rep_id), "L",
                                 "Closed-lost deals, close date in %s %d" % (vq.quarter, YEAR)))
    name = vq.rep_name

    if verdict is None:
        ans.headline = "Whether %s is at risk can't be determined: %s." % (name, cov.undefined_reason)
    elif verdict == "unknown":
        ans.headline = ("Whether %s is at risk can't be determined: some of %s's rows "
                        "couldn't be counted." % (name, vq.rep_id))
    elif verdict == "not at risk":
        ans.headline = ("%s is not at risk by this definition: closed-won plus open "
                        "pipeline is %s against a %s %s quota (%s coverage)." % (
                            name, money(combined.value), money(quota.value), vq.quarter,
                            cov.display(2)))
        if "at risk" in vq.raw.question.lower():
            ans.lines.append("The question assumed a risk this data doesn't show.")
    else:
        ans.headline = ("%s is at risk: closed-won plus open pipeline is %s against a %s "
                        "%s quota (%s coverage), leaving %s of quota with no pipeline "
                        "behind it." % (name, money(combined.value), money(quota.value),
                                        vq.quarter, cov.display(2), money(shortfall)))

    reasons = []
    if verdict in ("at risk", "not at risk"):
        reasons.append("Closed-won so far: %s from %d deal%s, by %s." % (
            money(booked.value), A.count, _s(A.count), _elapsed(vq)))
        if C.count:
            reasons.append(
                "Open pipeline: %s across %d deal%s. Every one would have to close at "
                "full value to reach %s of quota." % (
                    money(pipeline.value), C.count, _s(C.count),
                    pct(combined.value / quota.value, 0)))
            top = C.rows.sort_values("deal_value", ascending=False).iloc[0]
            reasons.append("Concentration: the largest open deal, %s %s (%s), is %s of "
                           "that pipeline." % (top.deal_id, cell(top.account_name),
                                               money(top.deal_value),
                                               pct(top.deal_value / pipeline.value, 0)))
            present = ["%s %s (%d deal%s)" % (stage, money(C.total_for("stage", stage)),
                                              C.count_for("stage", stage), _s(C.count_for("stage", stage)))
                       for stage in OPEN_STAGES[::-1] if C.count_for("stage", stage)]
            empty = [stage for stage in OPEN_STAGES if not C.count_for("stage", stage)]
            reasons.append("Stage mix: %s%s." % (
                ", ".join(present),
                "; nothing in %s" % " or ".join(empty) if empty else ""))
        else:
            reasons.append("Open pipeline: none with a close date in %s." % vq.period)
        if L.count:
            reasons.append("Lost this quarter: %s across %d deal%s." % (
                money(L.total), L.count, _s(L.count)))
    ans.lines += ["Why:"] + ["- " + r for r in reasons]

    ans.metrics += [
        Metric("Closed-won to date [A]", money(booked.value, booked.lower_bound),
               "%d deal%s" % (A.count, _s(A.count))),
        Metric("Open pipeline [C]", money(pipeline.value, pipeline.lower_bound),
               "%d deal%s" % (C.count, _s(C.count))),
        Metric("Quota [B]", money(quota.value), quota.undefined_reason or ""),
        Metric("Coverage", cov.display(2)),
        Metric("Shortfall" if (shortfall or 0) >= 0 else "Cushion",
               money(abs(shortfall)) if shortfall is not None else "unknown"),
        Metric("Verdict", verdict or "undefined"),
    ]

    # Last quarter, for context only: it doesn't feed the verdict.
    if vq.quarter == "Q2" and snaps.at_q1_close.as_of is not None:
        q1 = snaps.at_q1_close
        D = ans.dataset(_won(q1, "Q1", QUARTERS["Q1"][1], "D", rep_id=vq.rep_id))
        E = ans.dataset(_quota(q1, "Q1", "E", rep_id=vq.rep_id))
        q1_calc = divide("Q1 attainment (context only)", "[D] Q1 closed-won / [E] Q1 quota",
                         _term_deals(D, "[D] Q1 closed-won"), _term_quota(E), "pct")
        ans.calculations.append(q1_calc)
        ans.metrics.append(Metric("Q1 result (context)", "%s against %s: %s" % (
            money(D.total), money(E.total), q1_calc.display(1)), "extract taken at Q1 close"))
        ans.flag(snapshot_conflict_flag(
            snaps, D, "context",
            " It affects only the Q1 context figure, not the Q2 verdict."))
        ans.assume(_precedence(q1, "Q1"))

    deals = pd.concat([A.rows, C.rows, L.rows])
    ans.tables.append(_deal_table(snaps, snap, deals, "%s's %s deals [A] [C] [L]" % (name, vq.period)))

    ans.values.update(bookings=booked.value, pipeline=pipeline.value, quota=quota.value,
                      coverage=cov.result, shortfall=shortfall, verdict=verdict,
                      largest=(C.rows.sort_values("deal_value", ascending=False).deal_id.iloc[0]
                               if C.count else None))
    return ans


# ---------------------------------------------------------------------------
# quarter comparison
# ---------------------------------------------------------------------------

def quarter_comparison(vq, snaps: SnapshotSet) -> Answer:
    ans = Answer(vq)
    live, close = snaps.for_quarter("Q2"), snaps.for_quarter("Q1")
    seg = vq.segment

    A = ans.dataset(_won(live, "Q2", vq.through, "A", seg))
    B = ans.dataset(_quota(live, "Q2", "B", seg))
    C = ans.dataset(_won(close, "Q1", vq.compare_through, "C", seg))
    D = ans.dataset(_quota(close, "Q1", "D", seg))

    q2_booked = _term_deals(A, "[A] Q2 closed-won to day %d" % vq.day)
    q1_booked = _term_deals(C, "[C] Q1 closed-won to day %d" % vq.day)
    q2 = divide("Q2 attainment at day %d" % vq.day, "[A] / [B] Q2 quota", q2_booked, _term_quota(B))
    q1 = divide("Q1 attainment at day %d" % vq.day, "[C] / [D] Q1 quota", q1_booked, _term_quota(D))
    ans.calculations += [q2, q1]

    relative = None
    if q1.result and q2.result is not None:
        relative = divide("Q2 pace relative to Q1", "Q2 attainment at day %d / Q1 attainment at day %d"
                          % (vq.day, vq.day),
                          Term("Q2 attainment", q2.result, "result of the Q2 calculation above",
                               q2.lower_bound, fmt="pct"),
                          Term("Q1 attainment", q1.result, "result of the Q1 calculation above",
                               fmt="pct"), "pct")
        ans.calculations.append(relative)

    slipped = slipped_flag(snaps, A)
    moved = int(A.rows[A.rows.deal_id.isin(slipped.deal_ids)].deal_value.sum()) if slipped else 0
    net = q2_booked.value - moved
    net_calc = None
    if moved:
        ans.calculations.append(Calculation(
            "Q2 bookings excluding deals moved from Q1", "[A] - moved deals (%s)" % ", ".join(slipped.deal_ids),
            q2_booked, Term("moved deals", moved, "sum of deal_value over %s in [A]" % ", ".join(slipped.deal_ids)),
            net, "money"))
        if B.total:
            net_calc = divide("Q2 attainment excluding moved deals", "(Q2 bookings excluding moved deals) / [B]",
                              Term("Q2 bookings excluding moved deals", net, "%s - %s" % (
                                  money(q2_booked.value), money(moved))), _term_quota(B))
            ans.calculations.append(net_calc)

    label = seg or "Meridian"
    if q1.result is None or q2.result is None:
        ans.headline = "The comparison is undefined: %s." % (q2.undefined_reason or q1.undefined_reason)
    else:
        direction = "behind" if q2.result < q1.result else "ahead of" if q2.result > q1.result else "level with"
        ans.headline = ("%s is %s Q1's pace: %s of Q2 quota booked by day %d (%s), against %s "
                        "of Q1 quota by day %d (%s)." % (
                            label, direction, q2.display(1), vq.day, vq.through.date(),
                            q1.display(1), vq.day, vq.compare_through.date()))
        ans.lines.append("%s booked in Q2 so far, against %s by the same point in Q1%s." % (
            money(q2_booked.value, q2_booked.lower_bound), money(q1_booked.value),
            ": %s of last quarter's pace" % relative.display(0) if relative else
            "; Q1 had booked nothing by then, so relative pace is undefined"))
        if moved and net_calc is not None and net_calc.result is not None and q1.result:
            ans.lines.append(strong(
                "%s of the Q2 figure is Q1 deals whose close dates moved into Q2. Excluding "
                "those, Q2 has booked %s: %s of quota, %s of Q1's pace." % (
                    money(moved), money(net), net_calc.display(1), pct(net_calc.result / q1.result, 0))))

    # Is the Q1 baseline itself affected by the snapshot conflict?
    baseline_conflict = snapshot_conflict_flag(snaps, C, "critical")
    if baseline_conflict:
        ans.flag(baseline_conflict)
    else:
        ans.lines.append("The Q1 baseline is identical in both extracts (%s), so the Q1 "
                         "snapshot conflict doesn't affect this comparison." % money(C.total))

    # Would a share-of-quarter definition of "same point" move the baseline?
    share_day = int((vq.day / quarter_length("Q2")) * quarter_length("Q1"))
    alt_cut = QUARTERS["Q1"][0] + pd.Timedelta(days=max(share_day, 1) - 1)
    alt = select_deals(close, _filters([WON], QUARTERS["Q1"][0], alt_cut, seg), "C*", "")
    same = alt.total == C.total
    ans.assume(Assumption(
        "SAME_POINT_ROBUSTNESS",
        "Q1 has %d days and Q2 has %d, so day %d is %s of Q2 but %s of Q1. Cutting Q1 "
        "at the same share of the quarter instead (%s) gives %s." % (
            quarter_length("Q1"), quarter_length("Q2"), vq.day,
            pct(vq.day / quarter_length("Q2")), pct(vq.day / quarter_length("Q1")),
            alt_cut.date(),
            "the same Q1 baseline" if same else "a different Q1 baseline, %s" % money(alt.total))))

    rows = []
    for s in ("Enterprise", "Mid-Market", "SMB"):
        if seg and s != seg:
            continue
        q2q = select_quota(live, "Q2", "B", s).total
        q1q = select_quota(close, "Q1", "D", s).total
        a, c = A.total_for("segment", s), C.total_for("segment", s)
        m = int(A.rows[(A.rows.segment == s) & A.rows.deal_id.isin(slipped.deal_ids if slipped else [])].deal_value.sum())
        rows.append([s, "%s (%s)" % (pct(c / q1q if q1q else None), money(c)),
                     "%s (%s)" % (pct(a / q2q if q2q else None), money(a)),
                     "%s (%s)" % (pct((a - m) / q2q if q2q else None), money(a - m))])
    if not seg:
        rows.append(["Total", "%s (%s)" % (q1.display(1), money(q1_booked.value)),
                     "%s (%s)" % (q2.display(1), money(q2_booked.value)),
                     "%s (%s)" % (net_calc.display(1) if net_calc else q2.display(1), money(net))])
    ans.tables.append(Table("Attainment at day %d, by segment" % vq.day,
                            ["Segment", "Q1 [C]/[D]", "Q2 [A]/[B]", "Q2 excl. moved deals"], rows,
                            numeric=["Q1 [C]/[D]", "Q2 [A]/[B]", "Q2 excl. moved deals"]))

    q1_final_close = select_deals(close, _filters([WON], *QUARTERS["Q1"], segment=seg), "F", "")
    ans.metrics += [
        Metric("Q2 closed-won by day %d [A]" % vq.day, money(q2_booked.value, q2_booked.lower_bound),
               "%d deal%s" % (A.count, _s(A.count))),
        Metric("Q2 quota [B]", money(B.total)),
        Metric("Q2 attainment", q2.display(1)),
        Metric("Q1 closed-won by day %d [C]" % vq.day, money(q1_booked.value),
               "%d deal%s" % (C.count, _s(C.count))),
        Metric("Q1 quota [D]", money(D.total)),
        Metric("Q1 attainment", q1.display(1)),
        Metric("Q2 pace relative to Q1", relative.display(0) if relative else "undefined"),
        Metric("Q2 excluding deals moved from Q1", money(net), net_calc.display(1) if net_calc else ""),
        Metric("Q1 final result (context)", money(q1_final_close.total),
               "extract taken at Q1 close; not used in the comparison"),
    ]
    final_conflict = snapshot_conflict_flag(
        snaps, q1_final_close, "context",
        " This affects only the Q1 final figure shown for context, not the comparison.")
    if final_conflict:
        final_conflict.message = final_conflict.message.replace("[F] totals", "Q1's final result totals")
        ans.flag(final_conflict)

    for f in (unplaced_flag(A), unplaced_flag(C), quota_flag(B), quota_flag(D), slipped):
        ans.flag(f)
    X = ans.dataset(_future_wins(live, "Q2", "X", seg))
    ans.flag(future_win_flag(X, q2_booked, _term_quota(B)))

    ans.assume(Assumption(
        "ATTAINMENT",
        "Attainment means closed-won bookings divided by the full quarter's quota. The comparison "
        "uses bookings only, not coverage: open pipeline isn't counted in either quarter, because "
        "neither extract records what Q1's open pipeline looked like on %s." % vq.compare_through.date()))
    ans.assume(_bookings_def("Q2", vq.through))
    ans.assume(Assumption("BOOKINGS_Q1", "Q1 bookings for the comparison are Closed Won "
                          "deal_value with a close date from %s to %s." % (
                              QUARTERS["Q1"][0].date(), vq.compare_through.date())))
    ans.assume(_precedence(live, "Q2"))
    ans.assume(_precedence(close, "Q1"))

    ans.values.update(q2_bookings=q2_booked.value, q1_bookings=q1_booked.value,
                      q2_attainment=q2.result, q1_attainment=q1.result,
                      relative=relative.result if relative else None, moved=moved, net=net,
                      baseline_conflict=baseline_conflict is not None)
    return ans


# ---------------------------------------------------------------------------
# deal list
# ---------------------------------------------------------------------------

def _deal_table(snaps: SnapshotSet, snap: Snapshot, rows: pd.DataFrame, title: str) -> Table:
    order = {stage: i for i, stage in enumerate((WON,) + OPEN_STAGES[::-1] + (LOST,))}
    rows = rows.copy()
    rows["_order"] = rows.stage.map(order)
    rows = rows.sort_values(["_order", "deal_value"], ascending=[True, False])
    show_loss = bool((rows.stage == LOST).any())
    live = snap.name == "live"
    recycled = snaps.recycled_ids() if live else set()
    slipped = snaps.slipped_ids() if live else set()
    out = []
    for _, r in rows.iterrows():
        notes = []
        if r.deal_id in recycled:
            notes.append("recycled ID")
        if r.deal_id in slipped and r.stage == WON:
            notes.append("moved from Q1")
        if r.stage in OPEN_STAGES and r.close_date < snap.as_of:
            notes.append("close date passed")
        line = [str(r._line), r.deal_id, cell(r.account_name), r.rep_id, r.segment, r.stage,
                money(r.deal_value), str(r.close_date.date())]
        if show_loss:
            line.append(("MISSING" if pd.isna(r.loss_reason) else r.loss_reason)
                        if r.stage == LOST else "-")
        line.append(", ".join(notes))
        out.append(line)
    cols = ["Line", "Deal", "Account", "Rep", "Segment", "Stage", "Value", "Close date"]
    if show_loss:
        cols.append("Loss reason")
    cols.append("Notes")
    return Table(title, cols, out, numeric=["Line", "Value"])


def deal_list(vq, snaps: SnapshotSet) -> Answer:
    ans = Answer(vq)
    snap = snaps.for_quarter(vq.quarter)
    start, end = QUARTERS[vq.quarter]
    stages = tuple(vq.stages) if vq.stages else None
    A = ans.dataset(select_deals(snap, _filters(stages, start, end, vq.segment, vq.rep_id), "A",
                                 "Deals with a close date in %s" % vq.period))

    who = (vq.rep_name + "'s") if vq.rep_name else (vq.segment or "")
    kind = ("open" if stages == OPEN_STAGES else "closed-won" if stages == (WON,)
            else "closed-lost" if stages == (LOST,) else stages[0] if stages else "")
    noun = " ".join(x for x in (who, kind, "deal" + _s(A.count)) if x)

    if not A.count and A.complete:
        ans.headline = "No %s have a close date in %s: 0 rows match." % (
            " ".join(x for x in (who, kind, "deals") if x), vq.period)
    elif stages == OPEN_STAGES:
        parts = ["%s in %s (%d)" % (money(A.total_for("stage", s)), s, A.count_for("stage", s))
                 for s in OPEN_STAGES if A.count_for("stage", s)]
        subject = ("%s's %s open pipeline" % (vq.rep_name, vq.period) if vq.rep_name else
                   "The %s %s open pipeline" % (vq.segment, vq.period) if vq.segment else
                   "The %s open pipeline" % vq.period)
        ans.headline = "%s is %d deal%s worth %s: %s." % (
            subject, A.count, _s(A.count), money(A.total, not A.complete), ", ".join(parts))
    else:
        won_n, won_v = A.count_for("stage", WON), A.total_for("stage", WON)
        lost_n, lost_v = A.count_for("stage", LOST), A.total_for("stage", LOST)
        open_rows = A.rows[A.rows.stage.isin(OPEN_STAGES)]
        split = []
        if won_n:
            split.append("%d closed-won (%s)" % (won_n, money(won_v)))
        if len(open_rows):
            split.append("%d open (%s)" % (len(open_rows), money(open_rows.deal_value.sum())))
        if lost_n:
            split.append("%d closed-lost (%s)" % (lost_n, money(lost_v)))
        ans.headline = "%d %s %s a close date in %s, worth %s in total%s." % (
            A.count, noun, "has" if A.count == 1 else "have", vq.period,
            money(A.total, not A.complete), (": " + ", ".join(split)) if len(split) > 1 else "")

    if stages == OPEN_STAGES and vq.quarter == "Q2":
        check = _open(snap, vq.quarter, "C", vq.segment, vq.rep_id)
        if check.total == A.total and check.count == A.count:
            ans.lines.append("This is the same %s used as open pipeline in coverage and "
                             "at-risk answers." % money(A.total))

    ans.metrics.append(Metric("Deals", str(A.count)))
    ans.metrics.append(Metric("Total value", money(A.total, not A.complete)))
    for s in (WON,) + OPEN_STAGES + (LOST,):
        n = A.count_for("stage", s)
        if n:
            ans.metrics.append(Metric(s, money(A.total_for("stage", s)), "%d deal%s" % (n, _s(n))))
    if A.count:
        ans.tables.append(_deal_table(snaps, snap, A.rows, "Deals [A], sorted by stage then value"))
    ans.calculations.append(Calculation(
        "total value", "sum of deal_value over the rows in [A]",
        _term_deals(A, "[A] deal_value"), None, A.total, "money"))

    for f in (unplaced_flag(A), no_rows_flag(A, "deals"), missing_loss_flag(A),
              slipped_flag(snaps, A), stale_flag(A), recycled_flag(snaps, A)):
        ans.flag(f)
    ans.assume(_precedence(snap, vq.quarter))
    if stages == OPEN_STAGES:
        ans.assume(_open_def(vq.quarter))
    ans.values.update(count=A.count, total=A.total, deal_ids=A.ids)
    return ans


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

def audit_answer(vq, snaps: SnapshotSet) -> Answer:
    ans = Answer(vq, "Data integrity audit")
    ans.audit_snaps = snaps
    return ans
