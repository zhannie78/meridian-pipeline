"""What a naive reporting tool would say, for contrast.

This is computed, not invented. It reads the same files, but the way a quick
CSV-to-chatbot tool does, following the shortcuts in RULES. The page prints
those rules next to the naive answer, so it can be checked rather than taken
as a strawman.

Nothing here feeds a real answer. It is shown only when someone asks to see it.
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from .data import OPEN_STAGES, QUARTERS, QUOTA_COLUMN, WON, YEAR
from .model import money, pct

RULES = [
    "Reads only the most recent extract, for every quarter.",
    "Answers the question's words at face value, with no definitions of its own.",
    "Takes every row at face value: blanks add up as zero, deals that moved "
    "quarters count as new, reused deal IDs go unnoticed.",
    "Treats \"at risk\" as \"hasn't closed enough yet\", ignoring pipeline.",
    "States no assumptions and raises no flags.",
]

# What each flag on the real answer tells the reader that the naive answer doesn't.
FLAG_MISSES = {
    "SLIPPED_ACROSS_BOUNDARY": "Some of the bookings were won in Q1 and moved into Q2, so they aren't new business.",
    "RECYCLED_DEAL_ID": "Part of the pipeline sits on deal IDs that belonged to different Q1 deals.",
    "STALE_CLOSE_DATE": "Some open deals are already past their own close date.",
    "SNAPSHOT_CONFLICT": "The two extracts disagree about Q1.",
    "AMBIGUOUS_PHRASE": "The question's wording has more than one reasonable meaning.",
    "UNPLACED_ROWS": "Some rows couldn't be counted, so the total is incomplete.",
    "MISSING_QUOTA": "A quota is missing, so the ratio can't be known.",
    "ZERO_QUOTA": "Quota is $0, so the ratio can't be known.",
    "FUTURE_DATED_WIN": "A deal is marked Closed Won with a close date after the data was pulled.",
    "OPEN_OUTSIDE_QUARTER": "Some open deals close outside the quarter.",
    "MISSING_LOSS_REASON": "Some lost deals have no recorded loss reason.",
}


def _deals(snaps) -> pd.DataFrame:
    # Every row, including ones that fail checks: that's the naive part.
    return snaps.live.deals


def _total(rows: pd.DataFrame) -> int:
    return int(pd.to_numeric(rows.deal_value, errors="coerce").sum())  # blanks add up as zero


def _won(df: pd.DataFrame, quarter: str) -> pd.DataFrame:
    start, end = QUARTERS[quarter]
    return df[(df.stage == WON) & (df.close_date >= start) & (df.close_date <= end)]


def _scope(df: pd.DataFrame, segment=None, rep_id=None) -> pd.DataFrame:
    if segment:
        df = df[df.segment == segment]
    if rep_id:
        df = df[df.rep_id == rep_id]
    return df


def _quota(snaps, quarter: str, segment=None, rep_id=None) -> int:
    reps = _scope(snaps.live.reps, segment, rep_id)
    return int(pd.to_numeric(reps[QUOTA_COLUMN[quarter]], errors="coerce").sum())


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0  # the naive tool reports 0% rather than "undefined"


def build(ans) -> Optional[dict]:
    vq, snaps = ans.query, ans.snaps
    if vq.intent == "audit" or snaps is None:
        return None
    df = _deals(snaps)
    label = vq.rep_name or vq.segment or "Meridian"
    misses: List[str] = []
    differs = None

    if vq.intent == "attainment":
        booked = _total(_scope(_won(df, vq.quarter), vq.segment, vq.rep_id))
        quota = _quota(snaps, vq.quarter, vq.segment, vq.rep_id)
        rate = _ratio(booked, quota)
        verdict = (("%s beat its quota." if rate >= 1 else "%s missed its quota.") % label
                   if vq.quarter == "Q1" else
                   "%s is well behind target." % label if rate < 0.5 else "%s is on target." % label)
        text = "%s has closed %s against a %s %s quota: %.1f%% attainment. %s" % (
            label, money(booked), money(quota), vq.quarter, 100 * rate, verdict)
        if vq.quarter == "Q2":
            misses.append("Q2 was only at %s when the data was pulled, so a low percentage "
                          "is expected at this point." % _elapsed(vq))
        else:
            misses.append("Q1 is read from the live extract, not the extract taken at quarter close.")
        real = ans.values.get("verdict")
        if vq.quarter == "Q2":
            misses.insert(0, "It ignores the %s of open pipeline, which is what decides whether "
                             "%s can still make quota." % (money(ans.values.get("pipeline")), label))
            if real in ("at risk", "not at risk") and (real == "not at risk") == (rate < 0.5):
                differs = ("It calls %s %s from bookings alone. Counting open pipeline, %s %s "
                           "(%.2fx coverage)." % (
                               label, "well behind target" if rate < 0.5 else "on target", label,
                               "covers its quota" if real == "not at risk" else "is at risk",
                               ans.values.get("coverage") or 0))
        if booked != ans.values.get("bookings"):
            differs = "Its bookings figure is %s; the evidence-first answer's is %s." % (
                money(booked), money(ans.values.get("bookings")))

    elif vq.intent == "coverage":
        booked = _total(_scope(_won(df, vq.quarter), vq.segment, vq.rep_id))
        pipeline = _total(_scope(df[df.stage.isin(OPEN_STAGES)], vq.segment, vq.rep_id))
        quota = _quota(snaps, vq.quarter, vq.segment, vq.rep_id)
        rate = _ratio(booked + pipeline, quota)
        text = "%s has %s in open pipeline plus %s closed against a %s quota: %.2fx coverage, so it is %s." % (
            label, money(pipeline), money(booked), money(quota), rate,
            "on track" if rate >= 1 else "not on track")
        misses.append("Every open deal in the file counts, whatever its close date.")

    elif vq.intent == "rep_risk" and vq.rep_id:
        booked = _total(_scope(_won(df, vq.quarter), rep_id=vq.rep_id))
        quota = _quota(snaps, vq.quarter, rep_id=vq.rep_id)
        rate = _ratio(booked, quota)
        at_risk = rate < 0.5
        text = "%s has closed %s against a %s quota: %.0f%% attainment, so %s %s." % (
            label, money(booked), money(quota), 100 * rate, label,
            "is at risk" if at_risk else "is not at risk")
        misses.append("It ignores %s's open pipeline entirely." % label)
        real = ans.values.get("verdict")
        if real in ("at risk", "not at risk") and (real == "at risk") != at_risk:
            differs = "Its verdict is the opposite of the evidence-first answer: that says %s." % real

    elif vq.intent == "rep_risk":
        won = _scope(_won(df, vq.quarter), vq.segment)
        reps = _scope(snaps.live.reps, vq.segment)
        idle = [r.rep_name for r in reps.itertuples() if _total(won[won.rep_id == r.rep_id]) == 0]
        text = "%d of %d reps haven't closed a deal in %s, so they're at risk: %s." % (
            len(idle), len(reps), vq.quarter, ", ".join(idle))
        misses.append("It ignores pipeline, so reps with plenty of it look at risk and reps "
                      "who have closed anything at all look safe.")
        real = {r["name"] for r in ans.values.get("records", []) if r["verdict"] == "at risk"}
        if real and set(idle) != real:
            wrongly_in = sorted(set(idle) - real)
            missed = sorted(real - set(idle))
            parts = []
            if wrongly_in:
                parts.append("calls %s at risk when their pipeline covers quota" % ", ".join(wrongly_in))
            if missed:
                parts.append("misses %s, who %s at risk" % (", ".join(missed),
                                                            "is" if len(missed) == 1 else "are"))
            differs = "Compared with the evidence-first answer, it %s." % "; it ".join(parts)

    elif vq.intent == "quarter_comparison":
        q1_start, q2_start = QUARTERS["Q1"][0], QUARTERS["Q2"][0]
        won = _scope(df[df.stage == WON], vq.segment)
        q1 = _total(won[(won.close_date >= q1_start) & (won.close_date <= vq.compare_through)])
        q2 = _total(won[(won.close_date >= q2_start) & (won.close_date <= vq.through)])
        gap = q2 - q1
        text = "By this point in Q1 (%s), %s had closed %s. Q2 has closed %s so far, %s %s, so Q2 is %s Q1." % (
            vq.compare_through.date(), label, money(q1), money(q2), money(abs(gap)),
            "less" if gap < 0 else "more", "behind" if gap < 0 else "ahead of")
        q1_quota, q2_quota = _quota(snaps, "Q1", vq.segment), _quota(snaps, "Q2", vq.segment)
        if q1_quota != q2_quota:
            misses.append("It compares dollars, not attainment. Quota went from %s in Q1 to %s in Q2, "
                          "so the same dollars are a %s share of Q2's quota." % (
                              money(q1_quota), money(q2_quota),
                              "smaller" if q2_quota > q1_quota else "larger"))
        else:
            misses.append("It compares dollars, not attainment against quota.")
        if q1 != ans.values.get("q1_bookings"):
            misses.append("Its Q1 figure comes from the live extract, not the extract taken at quarter close.")
        q1_att, q2_att = ans.values.get("q1_attainment"), ans.values.get("q2_attainment")
        if q1_att is not None and q2_att is not None:
            net = ans.values.get("net")
            differs = ("It gives a %s gap with no quota behind it. The evidence-first answer puts Q2 at "
                       "%s of quota against Q1's %s at the same point%s." % (
                           money(abs(gap)), pct(q2_att, 1), pct(q1_att, 1),
                           ", and %s once deals moved from Q1 are excluded" % pct(net / q2_quota, 1)
                           if ans.values.get("moved") and q2_quota else ""))

    elif vq.intent == "deal_list":
        rows = _scope(df, vq.segment, vq.rep_id)
        if vq.stages:
            rows = rows[rows.stage.isin(vq.stages)]
        kind = ("open" if tuple(vq.stages or ()) == OPEN_STAGES else
                "closed-won" if tuple(vq.stages or ()) == (WON,) else "")
        noun = " ".join(x for x in (vq.rep_name and vq.rep_name + "'s", vq.segment, kind) if x)
        text = "There are %d %sdeals worth %s in total." % (
            len(rows), noun + " " if noun else "", money(_total(rows)))
        if len(rows) != ans.values.get("count"):
            misses.append("It lists deals from every quarter in the file, not just %s." % vq.period)
        if len(rows) != ans.values.get("count"):
            differs = "It counts %d deals; the evidence-first answer lists %d for %s." % (
                len(rows), ans.values.get("count"), vq.period)
    else:
        return None

    for f in ans.flags:
        if f.level == "context":
            continue  # context flags don't change the answer, so they aren't something missed
        line = FLAG_MISSES.get(f.code)
        if line and line not in misses:
            misses.append(line)
    return {"text": text, "rules": RULES, "misses": misses, "differs": differs}


def _elapsed(vq) -> str:
    start, end = QUARTERS[vq.quarter]
    day = int((vq.as_of - start).days) + 1
    length = int((end - start).days) + 1
    return "day %d of %d" % (min(day, length), length)

