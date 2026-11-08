"""Settings: partner settings re-match orders, the co-founder manages people, and the audit log."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_engine
from app.enums import Role
from app.main import app
from app.recon.engine import match_all
from app.schema import audit_log
from scripts.seed import DemoUser, seed
from tests.builders import AS_OF, add_order, live_claims, m1_paid, partner_id, result

PEOPLE = [DemoUser("cf@demo.test", "C", "password-123", Role.COFOUNDER)]


@pytest.fixture
def data(monkeypatch):
    """A Marketplace 1 order ₹5 short: within the ₹10 tolerance, so no claim yet."""
    monkeypatch.setattr("app.recon.engine.today", lambda: AS_OF)
    with get_engine().begin() as conn:
        seed(conn, AS_OF, PEOPLE)
        order = add_order(conn, "ORD-M-60001")
        m1_paid(conn, order, -500)
        match_all(conn, AS_OF)
        assert result(conn, order).status == "within_tolerance"
        return {"order": order, "m1": partner_id(conn, "Marketplace 1")}


def test_a_lower_tolerance_turns_a_small_gap_into_a_claim(signed_in, data):
    client = signed_in(Role.COFOUNDER)
    response = client.patch(f"/api/partners/{data['m1']}", json={"tolerance_paise": 100})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["partner"]["tolerance_paise"] == 100
    assert body["orders_matched"] == 1 and body["claims_opened"] == 1
    with get_engine().begin() as conn:
        assert live_claims(conn, data["order"]) == {"short_paid": 500}
        entry = conn.execute(
            select(audit_log.c.action, audit_log.c.before, audit_log.c.after)
        ).one()
    assert entry == ("partner.update", {"tolerance_paise": 1000}, {"tolerance_paise": 100})

    # Saving the same value again changes nothing and matches nothing.
    again = client.patch(f"/api/partners/{data['m1']}", json={"tolerance_paise": 100}).json()
    assert again["orders_matched"] == 0


def test_partner_settings_are_the_cofounders(signed_in, data):
    client = signed_in(Role.EXEC)
    assert (
        client.patch(f"/api/partners/{data['m1']}", json={"tolerance_paise": 0}).status_code == 403
    )


def test_partner_settings_are_checked(signed_in, data):
    client = signed_in(Role.COFOUNDER)
    url = f"/api/partners/{data['m1']}"
    assert client.patch(url, json={"tolerance_paise": -1}).status_code == 422
    assert client.patch(url, json={"settlement_cycle_days": 0}).status_code == 422
    assert client.patch("/api/partners/9999", json={"return_window_days": 5}).status_code == 404


def test_cofounder_adds_and_removes_people(signed_in):
    client = signed_in(Role.COFOUNDER)
    new = {
        "email": "New.Person@Example.test",
        "name": "New Person",
        "role": "exec",
        "password": "a-long-password",
    }
    created = client.post("/api/users", json=new)
    assert created.status_code == 201, created.text
    person = created.json()
    assert person["email"] == "new.person@example.test" and person["is_active"]
    assert client.post("/api/users", json=new).status_code == 409
    assert (
        client.post(
            "/api/users", json={**new, "email": "x@y.test", "password": "short"}
        ).status_code
        == 422
    )

    # The new person can sign in...
    with TestClient(app) as theirs:
        login = {"email": "new.person@example.test", "password": "a-long-password"}
        assert theirs.post("/api/auth/login", json=login).status_code == 200
        assert theirs.get("/api/me").status_code == 200

        # ...until they're deactivated, which ends their session at once.
        changed = client.patch(f"/api/users/{person['id']}", json={"is_active": False})
        assert changed.status_code == 200 and not changed.json()["is_active"]
        assert theirs.get("/api/me").status_code == 401
        assert theirs.post("/api/auth/login", json=login).status_code == 401

    me = client.get("/api/me").json()
    assert client.patch(f"/api/users/{me['id']}", json={"role": "exec"}).status_code == 409

    with get_engine().begin() as conn:
        actions = conn.execute(
            select(audit_log.c.action, audit_log.c.after).order_by(audit_log.c.id)
        ).all()
    assert [a.action for a in actions] == ["user.create", "user.update"]
    assert "password" not in str(actions)


def test_only_the_cofounder_manages_people(signed_in):
    client = signed_in(Role.EXEC)
    body = {"email": "a@b.test", "name": "A", "role": "cofounder", "password": "a-long-password"}
    assert client.post("/api/users", json=body).status_code == 403


def test_audit_log_shows_your_own_actions_unless_you_see_all(client, make_user, data):
    def sign_in(role: Role, email: str) -> TestClient:
        c = TestClient(app)
        user = make_user(role, email=email)
        c.post("/api/auth/login", json={"email": user["email"], "password": user["password"]})
        c.headers["X-CSRF-Token"] = c.cookies["settle_csrf"]
        return c

    boss = sign_in(Role.COFOUNDER, "boss@example.test")
    worker = sign_in(Role.EXEC, "worker@example.test")
    boss.patch(f"/api/partners/{data['m1']}", json={"return_window_days": 5})
    claim = worker.get("/api/claims").json()["rows"]
    assert claim == []  # nothing to claim yet; give the executive an action of their own:
    boss.patch(f"/api/partners/{data['m1']}", json={"tolerance_paise": 100})
    [row] = worker.get("/api/claims").json()["rows"]
    worker.post(f"/api/claims/{row['id']}/actions", json={"action": "raise"})

    mine = worker.get("/api/audit").json()
    assert not mine["everyone"] and [e["action"] for e in mine["rows"]] == ["claim.raise"]
    every = boss.get("/api/audit").json()
    assert every["everyone"] and every["total"] == 3
    assert every["rows"][0]["actor"] == "Test exec"
    assert every["rows"][0]["subject"] == "ORD-M-60001 (Short-paid)"
    assert every["rows"][1]["subject"] == "Marketplace 1"
    partners_only = boss.get("/api/audit", params={"area": "partner"}).json()
    assert {e["action"] for e in partners_only["rows"]} == {"partner.update"}
