"""Golden tests: the three required questions and the three follow-ups.

Every expected figure was computed independently from the raw CSVs before this
code existed. If one fails, the code is wrong, not the number.
"""
Q_ENTERPRISE = "How is the Enterprise segment tracking against quota this quarter?"
Q_RISK = "Which reps are at risk of missing Q2?"
Q_COMPARE = "How does Q2 attainment compare to where we were at the same point in Q1?"


def dataset(answer, key):
    return next(d for d in answer.datasets if d.key == key)


def flag(answer, code):
    return next(f for f in answer.flags if f.code == code)


# ---------------------------------------------------------------------------
# 1. Enterprise attainment
# ---------------------------------------------------------------------------

class TestEnterpriseAttainment:
    def test_query_object(self, engine):
        assert engine.interpret(Q_ENTERPRISE).as_dict() == {
            "intent": "attainment", "period": "Q2 2026", "through": "2026-05-02",
            "as_of": "2026-05-02", "segment": "Enterprise", "rep": None, "stages": None,
            "compare_to": None, "ambiguous_phrases": [], "parsed_by": "rules"}

    def test_figures(self, engine):
        a = engine.ask(Q_ENTERPRISE)
        assert a.values["bookings"] == 165_000
        assert a.values["quota"] == 3_650_000
        assert round(a.values["attainment"], 4) == 0.0452
        assert a.values["deal_ids"] == ["OPP-060"]
        assert a.values["slipped"] == 165_000

    def test_source_details(self, engine):
        a = engine.ask(Q_ENTERPRISE)
        A, B = dataset(a, "A"), dataset(a, "B")
        assert A.file == "Q2/q2_deals.csv"
        assert str(A.snapshot.as_of.date()) == "2026-05-02"
        assert [f.text for f in A.filters] == [
            'stage = "Closed Won"',
            "close_date from 2026-04-01 to 2026-05-02, inclusive",
            'segment = "Enterprise"']
        assert A.ids == ["OPP-060"] and list(A.rows._line) == [8]
        assert A.complete
        assert B.file == "Q2/q2_reps.csv"
        assert sorted(B.rows.rep_id) == ["REP-01", "REP-02", "REP-03", "REP-04"]
        assert B.total == 3_650_000

    def test_calculation(self, engine):
        calc = engine.ask(Q_ENTERPRISE).calculations[0]
        assert calc.formula == "[A] closed-won bookings / [B] quota"
        assert calc.numerator.value == 165_000
        assert calc.denominator.value == 3_650_000
        assert calc.display(2) == "4.52%"

    def test_headline_pairs_bookings_with_pipeline(self, engine):
        a = engine.ask(Q_ENTERPRISE)
        assert a.headline == (
            "Enterprise has booked 4.5% of its Q2 quota, but with open pipeline it's at 1.03x: "
            "technically covered, with no room for slippage.")
        assert "**All of it was won in Q1 and moved into Q2; excluding moved deals, Q2 bookings are $0.**" in a.lines
        v = a.values
        assert (v["pipeline"], v["shortfall"], v["verdict"]) == (3_600_000, -115_000, "not at risk")
        assert round(v["coverage"], 2) == 1.03
        assert v["recycled_pipeline"] == 1_475_000
        assert v["reps_short"] == ["REP-03", "REP-01"]
        assert "losing any one of the 18 open deals larger than that" in " ".join(a.lines)

    def test_rep_split_is_flagged_when_the_total_hides_it(self, engine):
        a = engine.ask(Q_ENTERPRISE)
        f = flag(a, "REPS_SHORT_WITHIN_TOTAL")
        assert "Priya Patel (0.74x), Sarah Chen (0.88x)" in f.message
        assert "James Okafor (1.25x) and Marcus Rivera (1.32x)" in f.message

    def test_closed_quarter_stays_bookings_only(self, engine):
        a = engine.ask("How did Enterprise do against quota in Q1?")
        assert a.headline.startswith("Enterprise finished Q1 2026 with")
        assert "pipeline" not in a.values

    def test_flags_and_assumptions(self, engine):
        a = engine.ask(Q_ENTERPRISE)
        assert set(a.flag_codes) == {"SLIPPED_ACROSS_BOUNDARY", "RECYCLED_DEAL_ID", "REPS_SHORT_WITHIN_TOTAL"}
        assert flag(a, "SLIPPED_ACROSS_BOUNDARY").deal_ids == ["OPP-060"]
        assert {"THIS_QUARTER", "ATTAINMENT", "BOOKINGS", "SNAPSHOT_Q2", "SEGMENT_SOURCE", "AT_RISK",
                "OPEN_PIPELINE", "PIPELINE_NOT_WEIGHTED"} <= set(a.assumption_codes)
        this_quarter = next(x for x in a.assumptions if x.code == "THIS_QUARTER")
        assert this_quarter.text == ('"This quarter" means Q2 2026, because the dataset '
                                     'is current as of 2026-05-02.')


# ---------------------------------------------------------------------------
# 2. Rep risk
# ---------------------------------------------------------------------------

class TestRepRisk:
    def test_ranking_and_verdicts(self, engine):
        a = engine.ask(Q_RISK)
        records = {r["rep_id"]: r for r in a.values["records"]}
        assert [r["rep_id"] for r in a.values["records"]] == [
            "REP-07", "REP-05", "REP-09", "REP-03", "REP-06",
            "REP-01", "REP-10", "REP-08", "REP-04", "REP-02"]
        assert sorted(a.values["at_risk"]) == [
            "REP-01", "REP-03", "REP-05", "REP-06", "REP-07", "REP-08", "REP-09", "REP-10"]
        assert records["REP-02"]["verdict"] == "not at risk"
        assert records["REP-04"]["verdict"] == "not at risk"

    def test_per_rep_numerators_and_denominators(self, engine):
        records = {r["rep_id"]: r for r in engine.ask(Q_RISK).values["records"]}
        priya = records["REP-03"]
        assert (priya["won"], priya["open"], priya["quota"]) == (0, 740_000, 1_000_000)
        assert priya["shortfall"] == 260_000 and round(priya["coverage"], 2) == 0.74
        tom = records["REP-08"]
        assert (tom["won"], tom["open"], tom["quota"], tom["shortfall"]) == (293_000, 220_000, 520_000, 7_000)

    def test_largest_shortfall_is_named(self, engine):
        a = engine.ask(Q_RISK)
        assert "Largest shortfall: Priya Patel, $260,000" in " ".join(a.lines)

    def test_sources(self, engine):
        a = engine.ask(Q_RISK)
        assert dataset(a, "A").total == 518_000
        assert dataset(a, "C").total == 5_198_000 and dataset(a, "C").count == 46
        assert dataset(a, "B").total == 6_200_000

    def test_flags_and_assumptions(self, engine):
        a = engine.ask(Q_RISK)
        assert flag(a, "SLIPPED_ACROSS_BOUNDARY").deal_ids == ["OPP-042", "OPP-060", "OPP-074"]
        assert flag(a, "STALE_CLOSE_DATE").deal_ids == ["OPP-066", "OPP-075"]
        assert len(flag(a, "RECYCLED_DEAL_ID").deal_ids) == 13
        assert {"AT_RISK", "PIPELINE_NOT_WEIGHTED", "OPEN_PIPELINE"} <= set(a.assumption_codes)
        at_risk = next(x for x in a.assumptions if x.code == "AT_RISK")
        assert at_risk.text == '"At risk" means closed-won plus open pipeline is below quota.'


# ---------------------------------------------------------------------------
# 3. Quarter comparison
# ---------------------------------------------------------------------------

class TestQuarterComparison:
    def test_query_object(self, engine):
        d = engine.interpret(Q_COMPARE).as_dict()
        assert d["intent"] == "quarter_comparison"
        assert d["compare_to"] == "Q1 2026 through 2026-02-01 (day 32)"
        assert d["ambiguous_phrases"] == ["same point"]

    def test_figures(self, engine):
        v = engine.ask(Q_COMPARE).values
        assert (v["q2_bookings"], v["q1_bookings"]) == (518_000, 1_260_000)
        assert round(v["q2_attainment"], 4) == 0.0835
        assert round(v["q1_attainment"], 4) == 0.2128
        assert round(v["relative"], 2) == 0.39
        assert (v["moved"], v["net"]) == (373_000, 145_000)

    def test_baseline_comes_from_close_extract_and_is_not_in_conflict(self, engine):
        a = engine.ask(Q_COMPARE)
        C = dataset(a, "C")
        assert C.file == "Q1/q1_deals.csv"
        assert "close_date from 2026-01-01 to 2026-02-01, inclusive" in [f.text for f in C.filters]
        assert C.count == 10
        assert a.values["baseline_conflict"] is False
        conflicts = [f for f in a.flags if f.code == "SNAPSHOT_CONFLICT"]
        assert [f.level for f in conflicts] == ["context"]  # only the Q1-final context figure

    def test_same_point_is_flagged_and_defined(self, engine):
        a = engine.ask(Q_COMPARE)
        assert "AMBIGUOUS_PHRASE" in a.flag_codes
        same = next(x for x in a.assumptions if x.code == "SAME_POINT")
        assert "day 32 of Q2 (2026-05-02) against day 32 of Q1 (2026-02-01)" in same.text
        attainment = next(x for x in a.assumptions if x.code == "ATTAINMENT")
        assert "uses bookings only, not coverage" in attainment.text
        assert a.lines[1].startswith("**$373,000 of the Q2 figure") and a.lines[1].endswith("11% of Q1's pace.**")
        robust = next(x for x in a.assumptions if x.code == "SAME_POINT_ROBUSTNESS")
        assert "gives the same Q1 baseline" in robust.text


# ---------------------------------------------------------------------------
# follow-ups
# ---------------------------------------------------------------------------

class TestFollowUps:
    def test_enterprise_deals(self, engine):
        a = engine.ask("Show me the Enterprise deals.")
        assert (a.values["count"], a.values["total"]) == (21, 3_765_000)
        assert {"ALL_STAGES", "DEAL_LIST_SCOPE"} <= set(a.assumption_codes)
        assert flag(a, "RECYCLED_DEAL_ID").deal_ids == [
            "OPP-076", "OPP-077", "OPP-078", "OPP-079", "OPP-080", "OPP-081", "OPP-082"]

    def test_why_is_priya_at_risk(self, engine):
        a = engine.ask("Why is Priya at risk?")
        v = a.values
        assert v["verdict"] == "at risk"
        assert (v["bookings"], v["pipeline"], v["quota"], v["shortfall"]) == (0, 740_000, 1_000_000, 260_000)
        assert v["largest"] == "OPP-079"
        assert flag(a, "RECYCLED_DEAL_ID").deal_ids == ["OPP-079", "OPP-080"]
        assert "PARTIAL_NAME" in a.assumption_codes
        assert flag(a, "SNAPSHOT_CONFLICT").level == "context"

    def test_open_pipeline_deals_tie_to_coverage(self, engine):
        listing = engine.ask("Which deals make up the open pipeline?")
        cover = engine.ask("What's our pipeline coverage this quarter?")
        assert (listing.values["count"], listing.values["total"]) == (46, 5_198_000)
        assert cover.values["pipeline"] == listing.values["total"]
        assert sorted(dataset(cover, "C").ids) == sorted(listing.values["deal_ids"])

    def test_coverage(self, engine):
        v = engine.ask("What's our pipeline coverage this quarter?").values
        assert round(v["coverage"], 2) == 0.92
        assert v["shortfall"] == 484_000 and v["verdict"] == "at risk"

    def test_question_premise_that_the_data_contradicts(self, engine):
        a = engine.ask("Why is Marcus at risk?")
        assert a.values["verdict"] == "not at risk"
        assert "not at risk" in a.headline
