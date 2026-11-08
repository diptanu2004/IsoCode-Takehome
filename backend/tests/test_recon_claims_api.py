"""The reconciliation and claims API on a few hand-built orders."""

import io
import zipfile

import pytest
from sqlalchemy import select

from app.db import get_engine
from app.enums import Role
from app.recon.engine import match_all
from app.schema import audit_log, claims
from scripts.seed import DemoUser, seed
from tests.builders import AS_OF, add_lines, add_order, m1_paid, partner_id

PEOPLE = [DemoUser("cf@demo.test", "C", "password-123", Role.COFOUNDER)]


@pytest.fixture
def data():
    """Three Marketplace 1 orders: one short by ₹50, one unpaid, one settled."""
    with get_engine().begin() as conn:
        seed(conn, AS_OF, PEOPLE)
        short = add_order(conn, "ORD-M-80001")
        m1_paid(conn, short, -5000, tcs=-571, tds=-120)
        unpaid = add_order(conn, "ORD-M-80002")
        settled = add_order(conn, "ORD-M-80003")
        m1_paid(conn, settled)
        add_lines(conn, None, "Marketplace 1", on=AS_OF, sale=100)
        match_all(conn, AS_OF)
        m1 = partner_id(conn, "Marketplace 1")
    return {"short": short, "unpaid": unpaid, "settled": settled, "m1": m1}


def _claim_id(order: int) -> int:
    with get_engine().begin() as conn:
        return conn.execute(select(claims.c.id).where(claims.c.order_id == order)).scalar_one()


def test_order_list_filters_and_sorts_problems_first(signed_in, data):
    client = signed_in(Role.EXEC)
    page = client.get("/api/recon/orders").json()
    assert page["total"] == 3
    assert [r["status"] for r in page["rows"]] == ["unpaid_past_due", "short_paid", "settled"]

    problems = client.get("/api/recon/orders", params={"status": "problems"}).json()
    assert {r["order_id"] for r in problems["rows"]} == {"ORD-M-80001", "ORD-M-80002"}
    found = client.get("/api/recon/orders", params={"q": "ord-m-80003"}).json()
    assert [r["order_id"] for r in found["rows"]] == ["ORD-M-80003"]
    paged = client.get("/api/recon/orders", params={"page_size": 1, "page": 2}).json()
    assert paged["total"] == 3 and len(paged["rows"]) == 1


def test_bad_filters_are_refused(signed_in, data):
    client = signed_in(Role.EXEC)
    assert client.get("/api/recon/orders", params={"status": "nonsense"}).status_code == 422
    assert client.get("/api/recon/orders", params={"page_size": 5000}).status_code == 422
    assert client.get("/api/recon/orders", params={"sort": "drop table"}).status_code == 422


def test_status_counts(signed_in, data):
    counts = signed_in(Role.EXEC).get("/api/recon/summary").json()
    assert {c["status"]: c["orders"] for c in counts} == {
        "unpaid_past_due": 1,
        "short_paid": 1,
        "settled": 1,
    }


def test_order_detail_shows_every_number_and_where_it_came_from(signed_in, data):
    client = signed_in(Role.EXEC)
    detail = client.get(f"/api/recon/orders/{data['short']}").json()
    assert (detail["expected_paise"], detail["received_paise"], detail["diff_paise"]) == (
        89900,
        84900,
        -5000,
    )
    assert (detail["tcs_paise"], detail["tds_paise"]) == (-571, -120)
    commission = next(c for c in detail["components"] if c["component"] == "commission")
    assert commission == {
        "component": "commission",
        "label": "Commission",
        "expected_paise": -18000,
        "actual_paise": -23000,
        "gap_paise": -5000,
    }
    assert {line["component"] for line in detail["lines"]} >= {"sale", "commission", "tcs"}
    assert detail["claims"][0]["type"] == "short_paid"
    assert detail["rate_cards"] == [
        {"partner": "Marketplace 1", "version": 1, "effective_from": "2025-11-03"}
    ]
    assert client.get("/api/recon/orders/999999").status_code == 404


def test_claim_lifecycle_with_audit_trail(signed_in, data):
    client = signed_in(Role.EXEC)
    claim = _claim_id(data["short"])
    act = lambda **body: client.post(f"/api/claims/{claim}/actions", json=body)  # noqa: E731

    assert act(action="reject").status_code == 409  # only a raised claim can be rejected
    assert act(action="raise", partner_ref="M1-TKT-42").json()["status"] == "raised"
    assert act(action="reject").json()["status"] == "partner_rejected"
    assert act(action="raise").json()["status"] == "raised"
    assert act(action="recover", recovered_paise=9999).status_code == 409  # more than claimed
    assert act(action="recover", recovered_paise=3000).json()["status"] == "recovered"

    detail = client.get(f"/api/claims/{claim}").json()
    assert detail["recovered_paise"] == 3000 and detail["partner_ref"] == "M1-TKT-42"
    assert [e["to_status"] for e in detail["events"]] == [
        "open",
        "raised",
        "partner_rejected",
        "raised",
        "recovered",
    ]
    assert detail["events"][-1]["note"] == "Recovered Rs 30.00 of Rs 50.00"
    with get_engine().begin() as conn:
        actions = conn.execute(select(audit_log.c.action).order_by(audit_log.c.id)).scalars().all()
    assert actions == ["claim.raise", "claim.reject", "claim.raise", "claim.recover"]

    # A recovered claim stays closed when matching runs again.
    with get_engine().begin() as conn:
        assert match_all(conn, AS_OF)["claims_opened"] == 0


def test_only_the_cofounder_can_write_off(signed_in, data):
    claim = _claim_id(data["short"])
    client = signed_in(Role.EXEC)
    response = client.post(f"/api/claims/{claim}/actions", json={"action": "write_off"})
    assert response.status_code == 403


def test_cofounder_writes_off(client, make_user, data):
    user = make_user(Role.COFOUNDER, email="boss@example.test")
    client.post("/api/auth/login", json={"email": user["email"], "password": user["password"]})
    client.headers["X-CSRF-Token"] = client.cookies["settle_csrf"]
    claim = _claim_id(data["short"])
    response = client.post(f"/api/claims/{claim}/actions", json={"action": "write_off"})
    assert response.json()["status"] == "written_off"


def test_claim_list_groups_and_partner_totals(signed_in, data):
    client = signed_in(Role.EXEC)
    live = client.get("/api/claims").json()
    assert live["total"] == 2 and live["total_paise"] == 5000 + 89900
    assert {r["type_label"] for r in live["rows"]} == {"Short-paid", "Unpaid past due"}
    assert client.get("/api/claims", params={"group": "closed"}).json()["total"] == 0
    [m1] = client.get("/api/claims/partners").json()
    assert (m1["partner"], m1["live_claims"], m1["open_claims"]) == ("Marketplace 1", 2, 2)


def test_raise_all_open_claims_for_a_partner(signed_in, data):
    client = signed_in(Role.EXEC)
    response = client.post("/api/claims/raise-all", json={"partner_id": data["m1"]})
    assert response.json() == {"raised": 2}
    assert client.get("/api/claims", params={"group": "raised"}).json()["total"] == 2


def test_evidence_pack_for_a_partner(signed_in, data):
    client = signed_in(Role.EXEC)
    response = client.get(f"/api/claims/evidence/{data['m1']}.zip")
    assert response.status_code == 200
    assert "claims-marketplace-1-" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert sorted(archive.namelist()) == ["README.txt", "claims.csv", "evidence.csv"]
    claim_csv = archive.read("claims.csv").decode()
    assert "ORD-M-80001" in claim_csv and "Short-paid" in claim_csv
    assert "commission: expected -180.00, got -230.00" in claim_csv
    evidence = archive.read("evidence.csv").decode()
    assert "ORD-M-80001,commission,-230.00" in evidence
    assert "TCS and TDS are statutory deductions" in archive.read("README.txt").decode()


def test_systematic_claim_counts_every_order_but_lists_the_largest(signed_in):
    with get_engine().begin() as conn:
        seed(conn, AS_OF, PEOPLE)
        for i in range(210):
            m1_paid(conn, add_order(conn, f"ORD-M-9{i:04d}"), -500)
        match_all(conn, AS_OF)
        claim = conn.execute(
            select(claims.c.id).where(claims.c.type == "systematic_gap")
        ).scalar_one()
    detail = signed_in(Role.EXEC).get(f"/api/claims/{claim}").json()
    assert detail["order_count"] == 210
    assert len(detail["orders"]) == 200
    assert detail["amount_paise"] == 210 * 500


def test_partner_list_for_filters(signed_in, data):
    partners = signed_in(Role.EXEC).get("/api/partners").json()
    m1 = next(p for p in partners if p["name"] == "Marketplace 1")
    assert m1["id"] == data["m1"] and m1["kind"] == "marketplace"
    assert {"settlement_cycle_days", "tolerance_paise", "return_window_days"} <= set(m1)
