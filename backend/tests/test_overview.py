"""The Overview numbers on a few hand-built orders, and the claims list filtered by age."""

import pytest
from sqlalchemy import select

from app.claims.service import AGE_BUCKETS
from app.db import get_engine
from app.enums import Role
from app.recon.engine import match_all
from app.schema import claims
from scripts.seed import DemoUser, seed
from tests.builders import AS_OF, add_lines, add_order, m1_paid

PEOPLE = [DemoUser("cf@demo.test", "C", "password-123", Role.COFOUNDER)]


def bucket_of(age_days: int) -> int:
    return next(i for i, (_, _, last) in enumerate(AGE_BUCKETS) if last is None or age_days <= last)


@pytest.fixture
def data(monkeypatch):
    """Marketplace 1: one order short by ₹50, one unpaid, one settled, one row with no order."""
    monkeypatch.setattr("app.overview.routes.today", lambda: AS_OF)
    monkeypatch.setattr("app.claims.routes.today", lambda: AS_OF)
    with get_engine().begin() as conn:
        seed(conn, AS_OF, PEOPLE)
        m1_paid(conn, add_order(conn, "ORD-M-80001"), -5000, tcs=-571, tds=-120)
        add_order(conn, "ORD-M-80002")
        m1_paid(conn, add_order(conn, "ORD-M-80003"), tcs=-600)
        # A row with no order, dated today: Marketplace 1's reports reach today, so the unpaid
        # order is past due rather than waiting for a later report.
        add_lines(conn, None, "Marketplace 1", on=AS_OF, sale=100)
        match_all(conn, AS_OF)
        ages = conn.execute(select(claims.c.amount_paise, claims.c.aging_from)).all()
    return ages


def test_money_at_risk_by_partner_and_age(signed_in, data):
    body = signed_in(Role.EXEC).get("/api/overview").json()
    risk = body["at_risk"]
    assert risk["buckets"] == ["0-7", "8-15", "16-30", "31-60", "61+"]
    assert risk["total_paise"] == 5000 + 89900 and risk["claims"] == 2
    assert risk["not_raised_paise"] == risk["total_paise"]

    expected = [0] * 5
    for amount, aging_from in data:
        expected[bucket_of((AS_OF - aging_from).days)] += amount
    [m1] = risk["partners"]
    assert m1["partner"] == "Marketplace 1"
    assert m1["by_age"] == expected == risk["by_age"]
    assert sum(m1["claims_by_age"]) == 2


def test_targets_tolerance_and_taxes(signed_in, data):
    body = signed_in(Role.EXEC).get("/api/overview").json()
    assert body["orders"] == 3
    assert body["recovery"] == {
        "recovered_paise": 0,
        "target_paise": 300_000_000,
        "written_off_paise": 0,
        "paid_late_paise": 0,
    }
    # Two orders reached by a partner row, one row that matches nothing.
    assert body["auto_match"]["matched_orders"] == 2
    assert body["auto_match"]["unmatched_rows"] == 1
    assert body["auto_match"]["rate"] == pytest.approx(2 / 3)
    assert body["within_tolerance"] == {"orders": 0, "gap_paise": 0, "patterns": 0}
    [taxes] = body["taxes"]
    assert (taxes["tcs_paise"], taxes["tds_paise"]) == (571 + 600, 120)


def test_recovered_money_counts_toward_the_target(signed_in, data):
    client = signed_in(Role.EXEC)
    short = client.get("/api/claims", params={"type": "short_paid"}).json()["rows"][0]
    client.post(
        f"/api/claims/{short['id']}/actions", json={"action": "recover", "recovered_paise": 3000}
    )
    body = client.get("/api/overview").json()
    assert body["recovery"]["recovered_paise"] == 3000
    assert body["at_risk"]["total_paise"] == 89900  # the recovered claim is closed


def test_claims_filtered_by_age_add_up(signed_in, data):
    client = signed_in(Role.EXEC)
    risk = client.get("/api/overview").json()["at_risk"]
    for i, key in enumerate(risk["buckets"]):
        page = client.get("/api/claims", params={"age": key}).json()
        assert page["total_paise"] == risk["by_age"][i], key
    assert client.get("/api/claims", params={"age": "9-99"}).status_code == 422


def test_empty_database(signed_in):
    body = signed_in(Role.EXEC).get("/api/overview").json()
    assert body["orders"] == 0 and body["auto_match"]["rate"] is None
    assert body["at_risk"]["partners"] == [] and body["taxes"] == []
