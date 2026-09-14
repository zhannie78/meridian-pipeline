"""Edge cases, each run through the full pipeline on a modified copy of the data.

The rule under test throughout: missing data is never silently turned into zero.
"""
import os
import re

import pandas as pd
import pytest

from conftest import ROOT, chain, set_cell
from meridian import answer as render
from meridian.query import QueryRejected

LIVE = "Q2/q2_deals.csv"
LIVE_REPS = "Q2/q2_reps.csv"


def flag(ans, code):
    return next(f for f in ans.flags if f.code == code)


def text(ans):
    return render.render(ans, full=True)


# --- unknown rep / unknown segment -----------------------------------------

def test_unknown_rep(engine):
    with pytest.raises(QueryRejected) as exc:
        engine.ask("Why is Jordan Blake at risk?")
    assert exc.value.codes == ["UNKNOWN_REP"]


def test_unknown_segment(engine):
    with pytest.raises(QueryRejected) as exc:
        engine.ask("How is the Commercial segment tracking against quota?")
    assert exc.value.codes == ["UNKNOWN_SEGMENT"]


# --- no matching deals ------------------------------------------------------

def test_no_matching_deals_is_an_explicit_zero(engine):
    a = engine.ask("Show me the SMB closed-won deals")
    assert (a.values["count"], a.values["total"]) == (0, 0)
    assert "NO_MATCHING_DEALS" in a.flag_codes
    assert a.headline == "No SMB closed-won deals have a close date in Q2 2026: 0 rows match."


def test_zero_bookings_with_a_real_quota_is_zero_percent_not_undefined(engine):
    a = engine.ask("How is SMB tracking against quota this quarter?")
    assert a.values["bookings"] == 0 and a.values["attainment"] == 0.0
    assert "NO_MATCHING_DEALS" in a.flag_codes


# --- zero or missing quota --------------------------------------------------

def zero_smb_quota(df):
    df = df.copy()
    df.loc[df.segment == "SMB", "quota_q2_2026"] = "0"
    return df


def test_zero_quota_makes_attainment_undefined(make_engine):
    a = make_engine({LIVE_REPS: zero_smb_quota}).ask("How is SMB tracking against quota this quarter?")
    assert a.values["quota"] == 0
    assert a.values["attainment"] is None
    assert "ZERO_QUOTA" in a.flag_codes
    out = text(a)
    assert "undefined" in a.headline
    assert re.search(r"Attainment\s+undefined \(\[B\] quota is \$0\)", out)
    assert not re.search(r"\b0\.0+%|\binf\b", out)


def test_zero_quota_rep_is_unknown_not_at_risk(make_engine):
    a = make_engine({LIVE_REPS: zero_smb_quota}).ask("Which SMB reps are at risk?")
    verdicts = {r["rep_id"]: r["verdict"] for r in a.values["records"]}
    assert verdicts == {"REP-09": "unknown: quota $0", "REP-10": "unknown: quota $0"}
    assert "ZERO_QUOTA" in a.flag_codes


def test_missing_quota_is_never_summed_as_zero(make_engine):
    engine = make_engine({LIVE_REPS: set_cell("REP-09", "quota_q2_2026", "", key="rep_id")})
    a = engine.ask("How is SMB tracking against quota this quarter?")
    assert a.values["quota"] is None  # not 240,000, which is what summing would give
    assert "REP-09" in flag(a, "MISSING_QUOTA").message
    out = " ".join(text(a).split())
    assert "sum(quota_q2_2026) = undefined (quota is missing or invalid for REP-09 (line 10))" in out
    assert "Missing REP-09 (line 10): quota_q2_2026 is blank" in out


# --- missing loss reason ----------------------------------------------------

def test_missing_loss_reason_is_shown_and_flagged(make_engine):
    engine = make_engine({LIVE: set_cell("OPP-043", "loss_reason", "")})
    a = engine.ask("Show me the Mid-Market deals")
    assert flag(a, "MISSING_LOSS_REASON").deal_ids == ["OPP-043"]
    table = a.tables[0]
    row = next(r for r in table.rows if r[1] == "OPP-043")
    assert row[table.columns.index("Loss reason")] == "MISSING"


def test_closed_quarter_uses_the_close_extract_where_the_loss_reason_survives(engine):
    a = engine.ask("Show me the Enterprise deals in Q1")
    table = a.tables[0]
    row = next(r for r in table.rows if r[1] == "OPP-008")
    assert row[table.columns.index("Loss reason")] == "No decision"
    assert "MISSING_LOSS_REASON" not in a.flag_codes


# --- closed-won after the snapshot date -------------------------------------

def test_closed_won_dated_after_snapshot_is_excluded_and_flagged(make_engine):
    engine = make_engine({LIVE: set_cell("OPP-044", "close_date", "2026-05-20")})
    a = engine.ask("How is Mid-Market tracking against quota this quarter?")
    assert a.values["bookings"] == 353_000 - 85_000
    f = flag(a, "FUTURE_DATED_WIN")
    assert f.level == "critical" and f.deal_ids == ["OPP-044"]
    assert "$353,000" in f.message  # what bookings would be if it were counted
    assert any(d.key == "X" and d.ids == ["OPP-044"] for d in a.datasets)


def test_future_dated_win_is_not_counted_as_open_pipeline_either(make_engine):
    engine = make_engine({LIVE: set_cell("OPP-044", "close_date", "2026-05-20")})
    a = engine.ask("What's the Mid-Market pipeline coverage this quarter?")
    C = next(d for d in a.datasets if d.key == "C")
    assert "OPP-044" not in C.ids
    assert "FUTURE_DATED_WIN" in a.flag_codes


# --- open deals and their close dates ---------------------------------------

def test_open_deal_closing_after_snapshot_counts_as_pipeline(engine):
    a = engine.ask("What's our pipeline coverage this quarter?")
    C = next(d for d in a.datasets if d.key == "C")
    assert "OPP-079" in C.ids  # closes 2026-05-30, after the 2026-05-02 snapshot
    assert "OPP-066" in C.ids and "STALE_CLOSE_DATE" in a.flag_codes  # closed-date already passed


def test_open_deal_closing_after_quarter_end_is_excluded_and_flagged(make_engine):
    engine = make_engine({LIVE: set_cell("OPP-080", "close_date", "2026-07-15")})
    a = engine.ask("What's our pipeline coverage this quarter?")
    assert a.values["pipeline"] == 5_198_000 - 175_000
    assert flag(a, "OPEN_OUTSIDE_QUARTER").deal_ids == ["OPP-080"]


# --- duplicate or changed deal IDs ------------------------------------------

def duplicate_opp_044(df):
    extra = df[df.deal_id == "OPP-044"].copy()
    extra["deal_value"] = "40000"
    return pd.concat([df, extra], ignore_index=True)


def test_duplicate_deal_id_is_uncounted_and_the_figure_is_a_lower_bound(make_engine):
    engine = make_engine({LIVE: duplicate_opp_044})
    a = engine.ask("How is Mid-Market tracking against quota this quarter?")
    f = flag(a, "UNPLACED_ROWS")
    assert f.level == "critical" and f.deal_ids == ["OPP-044", "OPP-044"]
    assert "appears 2 times" in f.message
    assert a.values["bookings"] == 353_000 - 85_000
    assert "at least" in a.headline
    render.render_audit(engine.snaps)  # the audit still runs on the checked rows


def test_changed_deal_ids_across_extracts_are_flagged_where_used(engine):
    a = engine.ask("Why is Priya at risk?")
    f = flag(a, "RECYCLED_DEAL_ID")
    assert f.deal_ids == ["OPP-079", "OPP-080"]
    assert "$455,000" in f.message


# --- other ways data goes missing --------------------------------------------

def test_missing_deal_value_is_not_zero(make_engine):
    engine = make_engine({LIVE: set_cell("OPP-060", "deal_value", "")})
    a = engine.ask("How is the Enterprise segment tracking against quota this quarter?")
    assert "UNPLACED_ROWS" in a.flag_codes
    assert "NO_MATCHING_DEALS" not in a.flag_codes  # it isn't a genuine zero
    assert "deal_value missing" in flag(a, "UNPLACED_ROWS").message
    # Without OPP-060's value, what can be counted is 0.99x; the missing value could
    # push it over quota, so coverage is reported as undeterminable, not guessed.
    assert a.headline.startswith("Enterprise has booked at least 0.0%")
    assert a.values["verdict"] == "unknown"


def test_unparseable_close_date_is_reported(make_engine):
    engine = make_engine({LIVE: set_cell("OPP-037", "close_date", "2026-13-45")})
    a = engine.ask("How is Mid-Market tracking against quota this quarter?")
    assert "close_date '2026-13-45' is not a date" in flag(a, "UNPLACED_ROWS").message


def test_unknown_stage_makes_a_close_verdict_unknown(make_engine):
    engine = make_engine({LIVE: set_cell("OPP-056", "stage", "Commit")})
    a = engine.ask("What's the Enterprise pipeline coverage this quarter?")
    assert "stage 'Commit' is not a known stage" in flag(a, "UNPLACED_ROWS").message
    # Without the $220,000 deal Enterprise falls below quota; with it, it doesn't.
    assert a.values["verdict"] == "unknown"


# --- ambiguous "same point" -------------------------------------------------

def test_ambiguous_same_point(engine):
    a = engine.ask("Are we behind the same point in Q1?")
    assert a.intent == "quarter_comparison"
    assert "AMBIGUOUS_PHRASE" in a.flag_codes and "SAME_POINT" in a.assumption_codes


# --- sweeps -----------------------------------------------------------------

SWEEP = [
    "How is the Enterprise segment tracking against quota this quarter?",
    "Which reps are at risk of missing Q2?",
    "How does Q2 attainment compare to where we were at the same point in Q1?",
    "Show me the Enterprise deals.", "Why is Priya at risk?",
    "Which deals make up the open pipeline?", "How is SMB tracking against quota this quarter?",
    "Which SMB reps are at risk?", "Show me the Mid-Market deals",
]


@pytest.mark.parametrize("edits", [
    None,
    {LIVE: chain(set_cell("OPP-060", "deal_value", ""), set_cell("OPP-043", "loss_reason", ""),
                 set_cell("OPP-044", "close_date", "2026-05-20"))},
    {LIVE_REPS: set_cell("REP-09", "quota_q2_2026", "", key="rep_id")},
    {LIVE_REPS: zero_smb_quota},
])
def test_no_rendered_answer_contains_nan_none_or_inf(make_engine, edits):
    engine = make_engine(edits)
    for q in SWEEP:
        out = text(engine.ask(q))
        assert not re.search(r"\bnan\b|\bNaN\b|\bNone\b|\binf\b|\bNaT\b", out), q


def test_metric_code_never_fills_missing_values_with_zero():
    for module in ("metrics.py", "selection.py", "model.py"):
        with open(os.path.join(ROOT, "meridian", module)) as fh:
            assert "fillna(0" not in fh.read(), module
