"""Golden-number tests for the snapshot audit.

Every figure here was hand-verified against the raw CSVs before any of the
library code existed. If one of these fails, the library is wrong, not the test.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meridian import audit, data


@pytest.fixture(scope="module")
def snaps():
    return data.load()


def test_both_snapshots_load_with_expected_shape(snaps):
    assert len(snaps.at_q1_close.deals) == 88
    assert len(snaps.live.deals) == 92
    assert snaps.at_q1_close.deals.deal_id.is_unique
    assert snaps.live.deals.deal_id.is_unique


def test_q1_bookings_differ_between_snapshots(snaps):
    """The headline finding: 'final' Q1 is not final."""
    at_close = audit.q1_bookings(snaps.at_q1_close)
    live = audit.q1_bookings(snaps.live)
    assert at_close.total == 6_041_000
    assert at_close.deal_count == 51
    assert live.total == 4_050_000
    assert live.deal_count == 32
    assert at_close.total - live.total == 1_991_000


def test_recycled_ids_detected_by_rule(snaps):
    """Detection is by rule (account renamed OR created_date moved forward),
    never by a hard-coded list. All 13 must fall out of the rule."""
    recycled = audit.recycled_ids(snaps.at_q1_close, snaps.live)
    assert recycled == ["OPP-0%d" % n for n in range(76, 89)]
    assert len(recycled) == 13


def test_reconciliation_bridge_balances_to_the_dollar(snaps):
    bridge = audit.reconcile_q1(snaps.at_q1_close, snaps.live)
    assert bridge.opening == 6_041_000
    assert bridge.closing == 4_050_000

    by_cause = {line.cause: line for line in bridge.lines}
    assert by_cause["recycled_id"].amount == -1_220_000
    assert by_cause["recycled_id"].deal_count == 12
    assert by_cause["slipped_to_q2"].amount == -373_000
    assert by_cause["slipped_to_q2"].deal_count == 3
    assert by_cause["reverted_to_open"].amount == -253_000
    assert by_cause["reverted_to_open"].deal_count == 3
    assert by_cause["flipped_to_lost"].amount == -110_000
    assert by_cause["flipped_to_lost"].deal_count == 1
    assert by_cause["value_revised"].amount == -35_000
    assert by_cause["value_revised"].deal_count == 1

    # The whole point: the bridge closes exactly, with nothing unexplained.
    assert bridge.opening + sum(l.amount for l in bridge.lines) == bridge.closing
    assert bridge.unexplained == 0


def test_specific_reversals_are_named(snaps):
    bridge = audit.reconcile_q1(snaps.at_q1_close, snaps.live)
    by_cause = {line.cause: line for line in bridge.lines}
    assert by_cause["flipped_to_lost"].deal_ids == ["OPP-043"]
    assert by_cause["slipped_to_q2"].deal_ids == ["OPP-042", "OPP-060", "OPP-074"]
    assert by_cause["reverted_to_open"].deal_ids == ["OPP-020", "OPP-049", "OPP-050"]
    assert by_cause["value_revised"].deal_ids == ["OPP-021"]


def test_opp_076_is_a_different_company_in_each_snapshot(snaps):
    before = snaps.at_q1_close.deal("OPP-076")
    after = snaps.live.deal("OPP-076")
    assert before["account_name"] == "Cascade Financial"
    assert after["account_name"] == "Evergreen Capital"
    assert before["deal_value"] == 112_000
    assert after["deal_value"] == 250_000


def test_region_is_ambiguous_but_segment_is_clean(snaps):
    amb = audit.dimension_conflicts(snaps.live)
    assert amb["region"] == 17
    assert amb["segment"] == 0


def test_stale_open_deals_flagged(snaps):
    stale = audit.stale_deals(snaps.live)
    assert sorted(stale.deal_id) == ["OPP-066", "OPP-075"]


def test_genuinely_new_q2_deals_have_new_ids(snaps):
    """4 deals carry fresh IDs; 13 more were created in Q2 on recycled IDs."""
    new_ids = sorted(set(snaps.live.deals.deal_id) - set(snaps.at_q1_close.deals.deal_id))
    assert new_ids == ["OPP-089", "OPP-090", "OPP-091", "OPP-092"]
