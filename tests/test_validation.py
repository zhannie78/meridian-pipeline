"""The structured query: built and validated before any deal data is read, and
rejected or flagged whenever the question can't be answered as asked."""
import os

import pytest

from conftest import build_dataset
from meridian.data import DataIntegrityError
from meridian.engine import Engine
from meridian.query import LLMParser, QueryRejected, RawQuery


def rejected(engine, question):
    with pytest.raises(QueryRejected) as exc:
        engine.interpret(question)
    return exc.value


# ---------------------------------------------------------------------------
# rejections
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "Why is John Smith at risk?",
    "How is expansion doing?",
    "Show me the nexus corp deals",
])
def test_unknown_rep_or_name(engine, question):
    exc = rejected(engine, question)
    assert exc.codes == ["UNKNOWN_REP"]
    assert "isn't a rep on the roster" in exc.problems[0].message


@pytest.mark.parametrize("question,written", [
    ("How is the Public Sector segment tracking against quota?", "Public Sector"),
    ("How is the strategic segment doing against quota?", "strategic"),
])
def test_unknown_segment(engine, question, written):
    exc = rejected(engine, question)
    assert exc.codes == ["UNKNOWN_SEGMENT"]
    assert '"%s" isn\'t a segment' % written in exc.problems[0].message


def test_unknown_segment_from_a_model_is_rejected_too(engine):
    raw = RawQuery("anything", intent="attainment", segments=["key accounts"], source="claude")
    with pytest.raises(QueryRejected) as exc:
        engine.validator.validate(raw)
    assert exc.value.codes == ["UNKNOWN_SEGMENT"]


def test_ambiguous_first_name(engine):
    exc = rejected(engine, "How is Lisa tracking against quota?")
    assert exc.codes == ["AMBIGUOUS_REP"]
    assert "Lisa Park (rep, REP-09) or Lisa Huang (manager)" in exc.problems[0].message


def test_manager_is_not_a_rep(engine):
    exc = rejected(engine, "How is David Kim's team tracking?")
    assert exc.codes == ["MANAGER_NOT_REP"]
    assert "entire Enterprise segment" in exc.problems[0].message


@pytest.mark.parametrize("question,code", [
    ("What did we book in April?", "UNSUPPORTED_PERIOD"),
    ("What's Q3 coverage?", "UNSUPPORTED_PERIOD"),
    ("How did Enterprise do in Q2 2025?", "UNSUPPORTED_PERIOD"),
    ("Show me YTD bookings", "UNSUPPORTED_PERIOD"),
    ("How are we tracking this month?", "UNSUPPORTED_PERIOD"),
    ("What is next quarter's pipeline?", "UNSUPPORTED_PERIOD"),
    ("Are we ahead of the same point last year?", "UNSUPPORTED_PERIOD"),
    ("Which reps missed quota in Q1?", "UNSUPPORTED_PERIOD"),
    ("What were Enterprise bookings as of 2026-06-01?", "UNSUPPORTED_AS_OF"),
])
def test_unsupported_periods(engine, question, code):
    assert code in rejected(engine, question).codes


def test_as_of_matching_a_snapshot_is_accepted(engine):
    vq = engine.interpret("What were Enterprise bookings as of 2026-05-02?")
    assert vq.quarter == "Q2"
    assert "AS_OF_REQUESTED" in [a.code for a in vq.assumptions]


def test_missing_as_of_date_is_rejected(tmp_path):
    def no_live_date(doc):
        doc["snapshots"]["live"]["as_of"] = None
    engine = Engine(build_dataset(tmp_path, manifest=no_live_date), prefer_llm=False)
    for question in ("How is the Enterprise segment tracking against quota this quarter?",
                     "Which reps are at risk of missing Q2?",
                     "How does Q2 attainment compare to where we were at the same point in Q1?"):
        exc = rejected(engine, question)
        assert "MISSING_AS_OF" in exc.codes
        assert "Q2/q2_deals.csv has no as-of date" in exc.problems[exc.codes.index("MISSING_AS_OF")].message


def test_missing_q1_as_of_blocks_q1_answers(tmp_path):
    def no_close_date(doc):
        doc["snapshots"]["at_q1_close"]["as_of"] = ""
    engine = Engine(build_dataset(tmp_path, manifest=no_close_date), prefer_llm=False)
    assert "MISSING_AS_OF" in rejected(engine, "How did Enterprise do against quota in Q1?").codes
    engine.interpret("How is the Enterprise segment tracking against quota this quarter?")


@pytest.mark.parametrize("question,code", [
    ("Why is she at risk?", "AMBIGUOUS_REFERENCE"),
    ("Show me their deals", "AMBIGUOUS_REFERENCE"),
    ("What will we close next quarter?", "FORECAST"),
    ("How is the West region doing?", "REGION_AMBIGUOUS"),
    ("What's our win rate?", "UNSUPPORTED_MEASURE"),
    ("Show me Priya's SMB deals", "REP_SEGMENT_CONFLICT"),
    ("Is Priya or Marcus more at risk?", "MULTIPLE_REPS"),
    ("How are renewals doing this quarter?", "UNKNOWN_REP"),
    ("What's our renewal pipeline?", "UNSUPPORTED_QUESTION"),
])
def test_other_rejections(engine, question, code):
    assert code in rejected(engine, question).codes


def test_every_problem_is_reported_not_just_the_first(engine):
    exc = rejected(engine, "Why is John Smith at risk in April?")
    assert set(exc.codes) == {"UNKNOWN_REP", "UNSUPPORTED_PERIOD"}


# ---------------------------------------------------------------------------
# flagged and answered, with the interpretation visible
# ---------------------------------------------------------------------------

def test_on_track_is_flagged_and_defined(engine):
    a = engine.ask("Is Mid-Market on track this quarter?")
    assert a.intent == "coverage"
    assert "AMBIGUOUS_PHRASE" in a.flag_codes
    assert "ON_TRACK" in a.assumption_codes
    assert a.headline.startswith("Mid-Market is not on track")


def test_same_point_without_a_named_quarter(engine):
    vq = engine.interpret("How do we compare to the same point?")
    assert (vq.quarter, vq.compare_quarter, str(vq.compare_through.date())) == ("Q2", "Q1", "2026-02-01")
    assert "AMBIGUOUS_PHRASE" in [f.code for f in vq.flags]
    assert "SAME_POINT" in [a.code for a in vq.assumptions]


def test_comparison_without_same_point_still_states_its_basis(engine):
    vq = engine.interpret("How does Q2 compare with Q1?")
    assert "COMPARISON_BASIS" in [a.code for a in vq.assumptions]
    assert "AMBIGUOUS_PHRASE" not in [f.code for f in vq.flags]


def test_no_period_named_is_an_assumption(engine):
    vq = engine.interpret("Why is Priya at risk?")
    assert "NO_PERIOD" in [a.code for a in vq.assumptions]


@pytest.mark.parametrize("question,intent,segment,rep", [
    ("How is Sarah Chen doing?", "rep_risk", None, "REP-01"),
    ("What did Enterprise book this quarter?", "attainment", "Enterprise", None),
    ("Which Enterprise reps are at risk?", "rep_risk", "Enterprise", None),
    ("Which reps may miss Q2?", "rep_risk", None, None),
    ("How did SMB do last quarter?", "attainment", "SMB", None),
    ("How many deals did we close in Q2?", "deal_list", None, None),
    ("Show me Priya's deals", "deal_list", None, "REP-03"),
    ("What's the pipeline for Q2?", "coverage", None, None),
])
def test_in_scope_phrasings_still_answer(engine, question, intent, segment, rep):
    vq = engine.interpret(question)
    assert (vq.intent, vq.segment, vq.rep_id) == (intent, segment, rep)


# ---------------------------------------------------------------------------
# ordering guarantee: validate before reading deal data
# ---------------------------------------------------------------------------

def test_questions_are_validated_without_any_deal_file(tmp_path):
    root = build_dataset(tmp_path, drop=("Q1/q1_deals.csv", "Q2/q2_deals.csv"))
    engine = Engine(root, prefer_llm=False)
    vq = engine.interpret("Why is Priya at risk?")
    assert vq.rep_id == "REP-03"
    assert not engine.data_loaded
    with pytest.raises(DataIntegrityError):
        engine.ask("Why is Priya at risk?")


def test_rejected_questions_never_load_deal_data(tmp_path):
    engine = Engine(build_dataset(tmp_path), prefer_llm=False)
    with pytest.raises(QueryRejected):
        engine.ask("Why is John Smith at risk?")
    assert not engine.data_loaded


# ---------------------------------------------------------------------------
# a model parser can't get past the validator
# ---------------------------------------------------------------------------

class _FakeMessages:
    def __init__(self, payload):
        self.payload = payload

    def create(self, **kwargs):
        block = type("Block", (), {"type": "text", "text": self.payload})()
        return type("Response", (), {"content": [block]})()


def fake_llm(payload):
    parser = LLMParser.__new__(LLMParser)
    parser._client = type("Client", (), {"messages": _FakeMessages(payload)})()
    parser._model = "test"
    return parser


def test_model_output_goes_through_the_same_validation(engine):
    raw = fake_llm('{"intent": "rep_risk", "people": ["Jon Smith"]}').parse("x")
    with pytest.raises(QueryRejected) as exc:
        engine.validator.validate(raw)
    assert exc.value.codes == ["UNKNOWN_REP"]


def test_model_resolves_nothing_the_validator_does(engine):
    raw = fake_llm('{"intent": "rep_risk", "people": ["Priya"], "periods": ["this quarter"]}').parse("x")
    vq = engine.validator.validate(raw)
    assert (vq.rep_id, vq.quarter) == ("REP-03", "Q2")


@pytest.mark.parametrize("payload", [
    '{"intent": "forecast"}',
    '{"intent": "attainment", "answer": "$4.2M"}',
    'Enterprise is at 4.5% of quota.',
    '{"intent": "attainment", "people": "Priya"}',
])
def test_malformed_model_output_is_rejected(payload):
    with pytest.raises(QueryRejected) as exc:
        fake_llm(payload).parse("x")
    assert exc.value.codes == ["PARSER_OUTPUT"]


def test_model_supplied_month_is_still_an_unsupported_period(engine):
    raw = fake_llm('{"intent": "attainment", "periods": ["April"]}').parse("x")
    with pytest.raises(QueryRejected) as exc:
        engine.validator.validate(raw)
    assert exc.value.codes == ["UNSUPPORTED_PERIOD"]
