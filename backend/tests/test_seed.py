from datetime import date

from sqlalchemy import func, select

from app.db import get_engine
from app.enums import Role
from app.schema import mapping_versions, partners, rate_cards, sources, users
from scripts.catalog import PARTNERS, RATE_CARDS, SEED_FORMATS
from scripts.seed import DemoUser, seed

PEOPLE = [
    DemoUser("exec@demo.test", "Finance Executive", "exec-password-1", Role.EXEC),
    DemoUser("cofounder@demo.test", "Co-founder", "cofounder-password-1", Role.COFOUNDER),
]


def _count(conn, table) -> int:
    return conn.execute(select(func.count()).select_from(table)).scalar_one()


def test_seed_creates_the_demo_setup():
    with get_engine().begin() as conn:
        counts = seed(conn, date(2026, 11, 3), PEOPLE)
        assert counts == {
            "users": 2,
            "partners": len(PARTNERS),
            "sources": len(SEED_FORMATS),
            "rate_cards": len(RATE_CARDS),
            "mappings": len(SEED_FORMATS),
        }
        active = conn.execute(select(func.count()).where(mapping_versions.c.is_active)).scalar_one()
        assert active == len(SEED_FORMATS)
        approvers = set(conn.execute(select(mapping_versions.c.approved_by)).scalars())
        cofounder = conn.execute(select(users.c.id).where(users.c.role == "cofounder")).scalar_one()
        assert approvers == {cofounder}


def test_seed_is_safe_to_run_twice():
    with get_engine().begin() as conn:
        seed(conn, date(2026, 11, 3), PEOPLE)
        again = seed(conn, date(2026, 11, 3), PEOPLE)
        assert set(again.values()) == {0}
        for table in (users, partners, sources, rate_cards, mapping_versions):
            assert _count(conn, table) > 0


def test_seeded_users_can_sign_in(client):
    with get_engine().begin() as conn:
        seed(conn, date(2026, 11, 3), PEOPLE)
    response = client.post(
        "/api/auth/login", json={"email": "cofounder@demo.test", "password": "cofounder-password-1"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "cofounder"
