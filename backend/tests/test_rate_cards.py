"""Rate cards: reading the versions, and a new version re-pricing the partner's orders."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import get_engine
from app.enums import Role
from app.recon.engine import match_all
from app.schema import audit_log
from scripts.catalog import RATE_CARDS
from scripts.seed import DemoUser, seed
from tests.builders import AS_OF, DELIVERED, add_order, live_claims, m1_paid, partner_id, result

PEOPLE = [DemoUser("cf@demo.test", "C", "password-123", Role.COFOUNDER)]


@pytest.fixture
def data():
    """One Marketplace 1 order paid exactly as the seeded rate card says."""
    with get_engine().begin() as conn:
        seed(conn, AS_OF, PEOPLE)
        order = add_order(conn, "ORD-M-70001")
        m1_paid(conn, order)
        match_all(conn, AS_OF)
        assert result(conn, order).status == "settled"
        return {"order": order, "m1": partner_id(conn, "Marketplace 1")}


def m1_rules(**changes) -> dict:
    return {**RATE_CARDS["Marketplace 1"], **changes}


def test_everyone_reads_every_partners_cards(signed_in, data):
    cards = signed_in(Role.EXEC).get("/api/rate-cards").json()
    names = [c["partner"] for c in cards]
    assert "Marketplace 1" in names and "Courier A" in names
    assert "Website" not in names and "Warehouse" not in names  # no card of their own
    m1 = next(c for c in cards if c["partner"] == "Marketplace 1")
    [v1] = m1["versions"]
    assert (v1["version"], v1["effective_to"], v1["orders_priced"]) == (1, None, 1)
    assert v1["rules"]["commission_pct"] == 15.0


def test_only_the_cofounder_adds_a_version(signed_in, data):
    body = {"effective_from": str(AS_OF), "rules": m1_rules(), "note": "test"}
    response = signed_in(Role.EXEC).post(f"/api/rate-cards/{data['m1']}", json=body)
    assert response.status_code == 403


def test_a_new_version_reprices_the_orders_it_covers(signed_in, data):
    client = signed_in(Role.COFOUNDER)
    since = DELIVERED - timedelta(days=10)  # before the order's date
    body = {
        "effective_from": str(since),
        "rules": m1_rules(commission_pct=12.0),
        "note": "Marketplace 1 cut commission to 12% (email of 1 Oct)",
    }
    response = client.post(f"/api/rate-cards/{data['m1']}", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["version"] == 2 and response.json()["orders_matched"] == 1

    with get_engine().begin() as conn:
        # Commission at 12% of ₹1,199, rounded to the rupee as this card says, is ₹144; ₹180 was
        # taken, so the order is short by ₹36.
        assert result(conn, data["order"]).status == "short_paid"
        assert live_claims(conn, data["order"]) == {"short_paid": 3600}
        [action] = conn.execute(select(audit_log.c.action)).scalars().all()
    assert action == "rate_card.create"

    m1 = next(c for c in client.get("/api/rate-cards").json() if c["partner"] == "Marketplace 1")
    v2, v1 = m1["versions"]
    assert (v2["version"], v2["effective_to"], v2["orders_priced"]) == (2, None, 1)
    assert v1["effective_to"] == str(since - timedelta(days=1)) and v1["orders_priced"] == 0
    assert v2["created_by"]  # the signed-in co-founder


def test_bad_versions_are_refused(signed_in, data):
    client = signed_in(Role.COFOUNDER)
    url = f"/api/rate-cards/{data['m1']}"
    early = {"effective_from": "2020-01-01", "rules": m1_rules(), "note": "too early"}
    assert client.post(url, json=early).status_code == 409  # before version 1 starts
    courier = {
        "effective_from": str(AS_OF),
        "rules": RATE_CARDS["Courier A"],
        "note": "wrong kind",
    }
    assert client.post(url, json=courier).status_code == 422
    negative = {"effective_from": str(AS_OF), "rules": m1_rules(commission_pct=-1), "note": "x y"}
    assert client.post(url, json=negative).status_code == 422
    no_note = {"effective_from": str(AS_OF), "rules": m1_rules(), "note": ""}
    assert client.post(url, json=no_note).status_code == 422
