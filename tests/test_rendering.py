"""What an executive actually sees: six sections, in order, with enough source
detail to recompute every figure by hand."""
import json

import pytest

from meridian import answer as render
from meridian.query import CAPABILITIES, QueryRejected

SECTIONS = ["ANSWER", "SUPPORTING METRICS", "SOURCE DATA", "METHOD", "ASSUMPTIONS", "TRUST FLAGS"]
QUESTIONS = [
    "How is the Enterprise segment tracking against quota this quarter?",
    "Which reps are at risk of missing Q2?",
    "How does Q2 attainment compare to where we were at the same point in Q1?",
    "What's our pipeline coverage this quarter?",
    "Show me the Enterprise deals.",
    "Why is Priya at risk?",
    "Which deals make up the open pipeline?",
]


def headings(out):
    return [line.split("  (")[0] for line in out.splitlines() if line.split("  (")[0] in SECTIONS]


@pytest.mark.parametrize("question", QUESTIONS)
def test_six_sections_in_order(engine, question):
    assert headings(render.render(engine.ask(question))) == SECTIONS


def test_enterprise_answer_can_be_recomputed_from_the_output(engine):
    out = " ".join(render.render(engine.ask(QUESTIONS[0])).split())
    for expected in [
        "File      Q2/q2_deals.csv",
        "Snapshot  live extract, as of 2026-05-02 (stated in the client brief)",
        'Filters   stage = "Closed Won"',
        "close_date from 2026-04-01 to 2026-05-02, inclusive",
        'segment = "Enterprise"',
        "Rows      1 of 92: OPP-060 (line 8)",
        "Total     sum(deal_value) = $165,000",
        "File      Q2/q2_reps.csv",
        "REP-03 Priya Patel $1,000,000 (line 4)",
        "Total     sum(quota_q2_2026) = $3,650,000",
        "attainment = [A] closed-won bookings / [B] quota",
        "= $165,000 / $3,650,000",
        "= 4.52%",
    ]:
        assert " ".join(expected.split()) in out, expected


def test_method_prints_the_validated_query(engine):
    vq = engine.interpret(QUESTIONS[0])
    out = render.render(engine.ask(QUESTIONS[0]))
    for line in json.dumps(vq.as_dict(), indent=2).splitlines():
        assert line in out


def test_how_the_question_was_read_comes_before_the_number(engine):
    out = render.render(engine.ask(QUESTIONS[0]))
    assert out.index("Read as:") < out.index("$165,000")


def test_answer_section_states_flag_severity_before_the_detail(engine):
    out = render.render(engine.ask(QUESTIONS[0]))
    assert out.index("Flags raised: 1 critical") < out.index("\nTRUST FLAGS")


def test_rejection_rendering(engine):
    with pytest.raises(QueryRejected) as exc:
        engine.ask("Why is John Smith at risk in April?")
    out = render.render_rejection(exc.value, CAPABILITIES)
    assert "NO ANSWER" in out and "nothing was computed" in out
    assert "UNKNOWN_REP" in out and "UNSUPPORTED_PERIOD" in out
