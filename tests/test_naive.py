"""The naive contrast: computed from the same file by stated shortcuts, and
never part of a real answer."""
from meridian import naive, serialize


def build(engine, q):
    return naive.build(engine.ask(q))


def test_enterprise(engine):
    n = build(engine, "How is the Enterprise segment tracking against quota this quarter?")
    assert n["text"].startswith("Enterprise has closed $165,000 against a $3,650,000 Q2 quota: 4.5%")
    assert any("moved into Q2" in m for m in n["misses"])


def test_rep_risk_gets_the_wrong_reps(engine):
    n = build(engine, "Which reps are at risk of missing Q2?")
    assert n["text"].startswith("7 of 10 reps haven't closed a deal in Q2")
    assert "James Okafor, Marcus Rivera at risk when their pipeline covers quota" in n["differs"]
    assert "misses Aisha Williams, Sarah Chen, Tom Bradley" in n["differs"]


def test_comparison_gives_dollars_at_the_same_point_but_no_quota(engine):
    n = build(engine, "How does Q2 attainment compare to where we were at the same point in Q1?")
    assert n["text"] == ("By this point in Q1 (2026-02-01), Meridian had closed $1,260,000. Q2 has "
                         "closed $518,000 so far, $742,000 less, so Q2 is behind Q1.")
    assert "%" not in n["text"]
    assert any("compares dollars, not attainment" in m for m in n["misses"])
    assert n["differs"].endswith("8.4% of quota against Q1's 21.3% at the same point, "
                                 "and 2.3% once deals moved from Q1 are excluded.")


def test_contradicts_a_verdict(engine):
    n = build(engine, "Why is Marcus at risk?")
    assert "Marcus Rivera is at risk" in n["text"]
    assert n["differs"].startswith("Its verdict is the opposite")


def test_context_flags_are_not_listed_as_misses(engine):
    n = build(engine, "Why is Priya at risk?")
    assert not any("disagree about Q1" in m for m in n["misses"])


def test_differs_only_when_it_does(engine):
    assert build(engine, "Which deals make up the open pipeline?")["differs"] is None
    assert "39 deals" in build(engine, "Show me the Enterprise deals.")["differs"]


def test_naive_never_changes_the_real_answer(engine):
    q = "Why is Marcus at risk?"
    payload = serialize.answer_payload(engine.ask(q), q)
    assert payload["headline"].startswith("Marcus Rivera is not at risk")
    assert payload["naive"]["text"] != payload["headline"]
