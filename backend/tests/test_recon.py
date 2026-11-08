"""The matching engine against hand-built orders, one rule at a time (TDD §8)."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import insert, select, text, update

from app.db import get_engine
from app.enums import Role
from app.recon import engine
from app.recon.engine import match_all, match_pending
from app.schema import (
    claims,
    partners,
    rate_cards,
    return_scans,
)
from scripts.seed import DemoUser, seed
from tests.builders import (
    AS_OF,
    DELIVERED,
    add_lines,
    add_order,
    add_scan,
    live_claims,
    m1_paid,
    partner_id,
    result,
)

PEOPLE = [DemoUser("cf@demo.test", "C", "password-123", Role.COFOUNDER)]


@pytest.fixture
def conn():
    with get_engine().connect() as connection:
        seed(connection, AS_OF, PEOPLE)
        yield connection
        connection.rollback()


# --- Amounts and the sample orders --------------------------------------------------


def test_marketplace_sample_expected_and_tcs_tds_kept_out_of_the_gap(conn):
    order = add_order(conn, "ORD-M-88120")
    add_lines(
        conn,
        order,
        "Marketplace 1",
        sale=119900,
        commission=-21009,
        fixed_fee=-3000,
        shipping_fee=-9000,
        tcs=-571,
        tds=-120,
    )
    match_all(conn, AS_OF)
    r = result(conn, order)
    assert (r.expected_paise, r.received_paise, r.diff_paise) == (89900, 86891, -3009)
    assert (r.tcs_paise, r.tds_paise) == (-571, -120)
    assert r.status == "short_paid"
    assert live_claims(conn, order) == {"short_paid": 3009}
    assert r.breakdown["commission"] == [-18000, -21009]


def test_cod_sample_unpaid_for_the_expected_net(conn):
    order = add_order(
        conn,
        "ORD-W-204118",
        "Website",
        149900,
        mode="cod",
        weight=450,
        courier="Courier A",
        zone="D",
    )
    add_lines(conn, None, "Courier A", on=AS_OF, sale=100)  # the courier's reports are current
    match_all(conn, AS_OF)
    r = result(conn, order)
    assert (r.expected_paise, r.received_paise, r.diff_paise) == (140900, 0, -140900)
    assert r.status == "unpaid_past_due"
    assert live_claims(conn, order) == {"unpaid_past_due": 140900}
    assert r.due_date == DELIVERED + timedelta(days=7)


def test_returned_prepaid_sample_freight_overcharged(conn):
    order = add_order(
        conn,
        "ORD-W-204377",
        "Website",
        239800,
        status="returned_to_warehouse",
        weight=480,
        courier="Courier B",
        zone="C",
        returned=AS_OF - timedelta(days=15),
    )
    add_lines(conn, order, "Courier B", freight_forward=-7100, freight_return=-7100)
    conn.execute(
        insert(return_scans).values(
            upload_id=0,
            row_no=1,
            order_id=order,
            order_ref="ORD-W-204377",
            scanned_at=AS_OF - timedelta(days=14),
            line_key=str(uuid.uuid4()),
        )
    )
    match_all(conn, AS_OF)
    r = result(conn, order)
    assert (r.expected_paise, r.received_paise, r.diff_paise) == (-9600, -14200, -4600)
    assert r.status == "freight_overcharged"
    assert live_claims(conn, order) == {"freight_overcharged": 4600}


def test_marketplace_return_nets_to_shipping_both_ways(conn):
    order = add_order(
        conn, "O1", status="returned_to_warehouse", returned=AS_OF - timedelta(days=15)
    )
    add_lines(conn, order, "Marketplace 1", shipping_fee=-9000, return_shipping_fee=-9000)
    match_all(conn, AS_OF)
    assert result(conn, order).expected_paise == -18000


def test_commission_rounding_follows_the_rate_card(conn):
    """Marketplace 1 rounds to the rupee (₹179.85 → ₹180); Marketplace 2 keeps paise."""
    m1 = add_order(conn, "O1")
    m2 = add_order(conn, "O2", "Marketplace 2")
    match_all(conn, AS_OF)
    assert result(conn, m1).breakdown["commission"][0] == -18000
    assert result(conn, m2).breakdown["commission"][0] == -21582  # 18% of ₹1,199


def test_heavier_parcels_cost_more_slabs(conn):
    light = add_order(conn, "O1", weight=500)
    heavy = add_order(conn, "O2", weight=501)
    match_all(conn, AS_OF)
    assert result(conn, light).breakdown["shipping_fee"][0] == -9000
    assert result(conn, heavy).breakdown["shipping_fee"][0] == -13000  # one more ₹40 slab


def test_the_rate_card_in_force_on_the_order_date_is_used(conn):
    m1 = partner_id(conn, "Marketplace 1")
    rules = conn.execute(select(rate_cards.c.rules).where(rate_cards.c.partner_id == m1)).scalar()
    change = date(2026, 10, 1)
    conn.execute(
        insert(rate_cards).values(
            partner_id=m1,
            version=2,
            effective_from=change,
            rules={**rules, "commission_pct": 20.0},
        )
    )
    before = add_order(conn, "O1", order_date=change - timedelta(days=1))
    after = add_order(conn, "O2", order_date=change)
    match_all(conn, AS_OF)
    assert result(conn, before).breakdown["commission"][0] == -18000
    assert result(conn, after).breakdown["commission"][0] == -24000


def test_no_rate_card_means_no_false_claim(conn):
    order = add_order(conn, "O1", order_date=AS_OF - timedelta(days=900))
    add_lines(conn, order, "Marketplace 1", sale=119900, commission=-50000)
    match_all(conn, AS_OF)
    assert live_claims(conn, order) == {}


# --- Tolerance bands ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("gap", "status", "claim"),
    [
        (0, "settled", {}),
        (-99, "settled", {}),
        (-100, "within_tolerance", {}),
        (-1000, "within_tolerance", {}),
        (-1001, "short_paid", {"short_paid": 1001}),
        (1000, "within_tolerance", {}),
        (1001, "overpaid", {}),
    ],
)
def test_bands_around_the_ten_rupee_tolerance(conn, gap, status, claim):
    order = add_order(conn, "O1")
    m1_paid(conn, order, gap)
    match_all(conn, AS_OF)
    assert result(conn, order).status == status
    assert live_claims(conn, order) == claim


def test_tolerance_can_be_set_per_partner(conn):
    conn.execute(
        update(partners).where(partners.c.name == "Marketplace 1").values(tolerance_paise=5000)
    )
    order = add_order(conn, "O1")
    m1_paid(conn, order, -3000)
    match_all(conn, AS_OF)
    assert result(conn, order).status == "within_tolerance"


# --- Time: when something becomes late ----------------------------------------------


def test_payment_is_pending_until_the_day_after_it_is_due(conn):
    due = DELIVERED + timedelta(days=7)
    order = add_order(conn, "O1")
    add_lines(conn, None, "Marketplace 1", on=AS_OF, sale=100)  # reports reach today
    match_all(conn, due)
    assert result(conn, order).status == "pending"
    match_all(conn, due + timedelta(days=1))
    assert result(conn, order).status == "unpaid_past_due"


def test_not_late_until_the_partners_reports_reach_the_due_date(conn):
    due = DELIVERED + timedelta(days=7)
    order = add_order(conn, "O1")
    add_lines(conn, None, "Marketplace 1", on=due - timedelta(days=1), sale=100)
    match_all(conn, AS_OF)
    assert result(conn, order).status == "pending"
    assert live_claims(conn, order) == {}


def test_backstop_catches_a_partner_that_stopped_sending_reports(conn):
    due = DELIVERED + timedelta(days=7)
    order = add_order(conn, "O1")
    add_lines(conn, None, "Marketplace 1", on=due - timedelta(days=1), sale=100)
    days = engine.PAYMENT_BACKSTOP_DAYS
    match_all(conn, due + timedelta(days=days))
    assert result(conn, order).status == "pending"
    match_all(conn, due + timedelta(days=days + 1))
    assert result(conn, order).status == "unpaid_past_due"


def test_no_report_from_a_partner_at_all_is_not_called_late(conn):
    order = add_order(conn, "O1")
    match_all(conn, AS_OF + timedelta(days=200))
    assert result(conn, order).status == "pending"


def test_undelivered_order_is_pending_with_no_due_date(conn):
    order = add_order(conn, "O1", status="in_transit", delivered=None)
    add_lines(conn, None, "Marketplace 1", on=AS_OF, sale=100)
    match_all(conn, AS_OF)
    r = result(conn, order)
    assert r.status == "pending" and r.due_date is None


def test_cancelled_order_expects_nothing(conn):
    order = add_order(conn, "O1", status="cancelled", delivered=None)
    match_all(conn, AS_OF)
    r = result(conn, order)
    assert r.status == "not_applicable" and r.expected_paise == 0


# --- Courier charges ----------------------------------------------------------------


def site_prepaid(conn, order_id="O1", **kwargs) -> int:
    return add_order(
        conn, order_id, "Website", 119900, weight=400, courier="Courier A", zone="C", **kwargs
    )


def test_unbilled_freight_waits_and_is_not_called_overpaid(conn):
    order = site_prepaid(conn)
    match_all(conn, AS_OF)
    r = result(conn, order)
    assert (r.status, r.expected_paise, r.received_paise) == ("pending", -4800, 0)


def test_correctly_billed_freight_settles(conn):
    order = site_prepaid(conn)
    add_lines(conn, order, "Courier A", freight_forward=-4800)
    match_all(conn, AS_OF)
    assert result(conn, order).status == "settled"


def test_overcharged_freight_is_claimed_from_the_courier(conn):
    order = site_prepaid(conn)
    add_lines(conn, order, "Courier A", freight_forward=-7100)
    match_all(conn, AS_OF)
    assert live_claims(conn, order) == {"freight_overcharged": 2300}
    partner = conn.execute(select(claims.c.partner_id)).scalar()
    assert partner == partner_id(conn, "Courier A")


def test_one_order_can_have_two_problems(conn):
    order = add_order(
        conn, "O1", "Website", 149900, mode="cod", weight=450, courier="Courier A", zone="D"
    )
    add_lines(conn, order, "Courier A", sale=139900, freight_forward=-8800, cod_fee=-3000)
    match_all(conn, AS_OF)
    assert live_claims(conn, order) == {"short_paid": 10000, "freight_overcharged": 2800}
    assert result(conn, order).status == "short_paid"  # the more severe one shows


# --- Returns ------------------------------------------------------------------------


def returned_order(conn, returned: date) -> int:
    return add_order(conn, "O1", status="returned_to_warehouse", returned=returned)


def test_return_is_missing_only_after_the_window(conn):
    returned = AS_OF - timedelta(days=3)
    order = returned_order(conn, returned)
    add_scan(conn, None, AS_OF)  # the warehouse's scans are current
    match_all(conn, AS_OF)  # day 3 of 3
    assert result(conn, order).status == "pending"
    match_all(conn, AS_OF + timedelta(days=1))
    r = result(conn, order)
    assert r.status == "return_not_received"
    assert live_claims(conn, order) == {"return_not_received": 119900}


def test_scanned_return_is_not_missing(conn):
    order = returned_order(conn, AS_OF - timedelta(days=10))
    add_scan(conn, order, AS_OF - timedelta(days=9))
    match_all(conn, AS_OF)
    assert "return_not_received" not in live_claims(conn, order)


def test_no_scan_file_at_all_is_not_called_missing(conn):
    order = returned_order(conn, AS_OF - timedelta(days=30))
    match_all(conn, AS_OF)
    assert "return_not_received" not in live_claims(conn, order)


# --- Claim lifecycle ----------------------------------------------------------------


def test_late_payment_closes_the_claim_with_a_reason(conn):
    order = add_order(conn, "O1")
    add_lines(conn, None, "Marketplace 1", on=AS_OF, sale=100)
    match_all(conn, AS_OF)
    assert "unpaid_past_due" in live_claims(conn, order)

    m1_paid(conn, order)
    stats = match_all(conn, AS_OF)
    assert stats["claims_resolved"] == 1
    assert live_claims(conn, order) == {}
    assert result(conn, order).status == "settled"
    notes = conn.execute(
        text(
            "SELECT from_status, to_status, note FROM claim_events e "
            "JOIN claims c ON c.id = e.claim_id WHERE c.order_id = :o ORDER BY e.id"
        ),
        {"o": order},
    ).all()
    assert [tuple(n) for n in notes] == [
        (None, "open", "Found by matching"),
        (
            "open",
            "auto_resolved",
            "Matched again and the gap is gone: a later file paid it, or a rate card changed",
        ),
    ]


def test_claim_amount_follows_a_changed_gap(conn):
    order = add_order(conn, "O1")
    m1_paid(conn, order, -5000)
    match_all(conn, AS_OF)
    add_lines(conn, order, "Marketplace 1", adjustment=2000)
    stats = match_all(conn, AS_OF)
    assert stats["claims_updated"] == 1
    assert live_claims(conn, order) == {"short_paid": 3000}


@pytest.mark.parametrize("closed", ["recovered", "written_off"])
def test_a_settled_claim_is_not_opened_again(conn, closed):
    order = add_order(conn, "O1")
    m1_paid(conn, order, -5000)
    match_all(conn, AS_OF)
    conn.execute(update(claims).where(claims.c.order_id == order).values(status=closed))
    assert match_all(conn, AS_OF)["claims_opened"] == 0
    assert live_claims(conn, order) == {}


def test_matching_again_changes_nothing(conn):
    order = add_order(conn, "O1")
    m1_paid(conn, order, -5000)
    match_all(conn, AS_OF)
    assert match_all(conn, AS_OF) == {
        "orders_matched": 1,
        "claims_opened": 0,
        "claims_updated": 0,
        "claims_resolved": 0,
    }


def test_the_daily_sweep_catches_orders_that_became_late(conn):
    order = add_order(conn, "O1")
    add_lines(conn, None, "Marketplace 1", on=AS_OF, sale=100)
    due = DELIVERED + timedelta(days=7)
    match_all(conn, due)
    assert result(conn, order).status == "pending"
    match_pending(conn, due + timedelta(days=1))
    assert result(conn, order).status == "unpaid_past_due"


def test_the_sweep_also_checks_returns_on_orders_with_another_problem(conn):
    returned = AS_OF - timedelta(days=3)
    order = add_order(
        conn,
        "O1",
        "Website",
        119900,
        status="returned_to_warehouse",
        weight=400,
        courier="Courier A",
        zone="C",
        returned=returned,
    )
    add_lines(conn, order, "Courier A", freight_forward=-7100, freight_return=-4800)
    add_scan(conn, None, AS_OF)
    match_all(conn, AS_OF)
    assert result(conn, order).status == "freight_overcharged"
    match_pending(conn, AS_OF + timedelta(days=1))
    assert set(live_claims(conn, order)) == {"freight_overcharged", "return_not_received"}


# --- Patterns in small gaps (TDD §8.5) ----------------------------------------------


def small_gap_orders(conn, gaps: list[int]) -> None:
    for i, gap in enumerate(gaps):
        m1_paid(conn, add_order(conn, f"S{i}"), gap)


def systematic_claims(conn):
    return conn.execute(
        select(claims.c.amount_paise, claims.c.component, claims.c.period_month).where(
            claims.c.type == "systematic_gap", claims.c.status == "open"
        )
    ).all()


def test_small_gaps_one_way_become_one_claim(conn):
    small_gap_orders(conn, [-500] * 50)
    match_all(conn, AS_OF)
    [claim] = systematic_claims(conn)
    assert claim.amount_paise == 25000 and claim.component == "commission"
    assert claim.period_month == DELIVERED.replace(day=1)
    orders_linked = conn.execute(text("SELECT count(*) FROM claim_orders")).scalar()
    assert orders_linked == 50


def test_small_gaps_both_ways_are_just_rounding(conn):
    small_gap_orders(conn, [-500, 500] * 30)
    match_all(conn, AS_OF)
    assert systematic_claims(conn) == []


def test_too_few_small_gaps_is_no_pattern(conn):
    small_gap_orders(conn, [-500] * 49)
    match_all(conn, AS_OF)
    assert systematic_claims(conn) == []


def test_a_large_one_way_total_is_a_pattern_even_with_few_orders(conn, monkeypatch):
    monkeypatch.setattr(engine, "PATTERN_SUM_PAISE", 2000)
    small_gap_orders(conn, [-500] * 4)
    match_all(conn, AS_OF)
    assert len(systematic_claims(conn)) == 1


def test_a_pattern_that_goes_away_closes_its_claim(conn):
    small_gap_orders(conn, [-500] * 50)
    match_all(conn, AS_OF)
    conn.execute(text("DELETE FROM partner_lines WHERE component = 'commission'"))
    add_lines(conn, None, "Marketplace 1", on=AS_OF, sale=100)
    conn.execute(
        text(
            "INSERT INTO partner_lines (upload_id, row_no, partner_id, order_id, component, "
            "amount_paise, txn_date, line_key) "
            "SELECT 0, 1, channel_partner_id, id, 'commission', -18000, :on, gen_random_uuid() "
            "FROM orders"
        ),
        {"on": DELIVERED},
    )
    stats = match_all(conn, AS_OF)
    assert stats["claims_resolved"] == 1
    assert systematic_claims(conn) == []
