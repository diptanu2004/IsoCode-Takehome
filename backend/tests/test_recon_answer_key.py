"""The whole pipeline against the generator's answer key: every planted problem is found with the
right partner and amount, and nothing else is claimed."""

from collections import defaultdict
from datetime import date

import pytest
from sqlalchemy import select, text

from app.db import get_engine
from app.enums import Role, UploadStatus
from app.ingest.pipeline import process_upload, register_upload
from app.recon.engine import match_all
from app.schema import sources
from scripts.catalog import COURIER_A, M1_NEW_FORMAT, QC2, SEED_FORMATS
from scripts.generate_data import generate, write_dataset
from scripts.seed import DemoUser, seed

AS_OF = date(2026, 11, 3)
PEOPLE = [DemoUser("cf@demo.test", "C", "password-123", Role.COFOUNDER)]
MONEY_SLACK = 100  # Marketplace 3 rounds commission to the rupee, so its amounts differ by paise


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("data")
    data = generate(AS_OF, 6000, 60, 11)
    write_dataset(data, out, {"as_of": AS_OF.isoformat()})
    return data, out


def _upload(source_name: str, path) -> None:
    with get_engine().begin() as conn:
        source_id = conn.execute(
            select(sources.c.id).where(sources.c.name == source_name)
        ).scalar_one()
        upload_id = register_upload(
            conn, source_id, path.name, path.read_bytes(), None, status=UploadStatus.LOADING
        )
    assert process_upload(upload_id, AS_OF) == "done", path.name


@pytest.fixture
def loaded(dataset):
    data, out = dataset
    with get_engine().begin() as conn:
        seed(conn, AS_OF, PEOPLE)
    for fmt in SEED_FORMATS:
        _upload(fmt.source_name, out / "seed" / fmt.filename)
    return data, out


def _claims() -> dict[tuple[str, str], tuple[str, int]]:
    with get_engine().begin() as conn:
        rows = conn.execute(
            text(
                "SELECT o.order_id, c.type, p.name, c.amount_paise FROM claims c "
                "JOIN orders o ON o.id = c.order_id JOIN partners p ON p.id = c.partner_id "
                "WHERE c.status = 'open'"
            )
        ).all()
    return {(r.order_id, r.type): (r.name, r.amount_paise) for r in rows}


def _expected(data) -> dict[tuple[str, str], tuple[str, int]]:
    """Planted problems, less those in Marketplace 1's payout held back for the live demo."""
    held_back = {r["order_ref"] for r in data.demo_rows[M1_NEW_FORMAT.filename]}
    amounts: dict[tuple[str, str], int] = defaultdict(int)
    who: dict[tuple[str, str], str] = {}
    for t in data.truth:
        if t.issue in ("overpaid", "systematic_gap") or t.order_id in held_back:
            continue
        amounts[(t.order_id, t.issue)] += t.amount
        who[(t.order_id, t.issue)] = t.partner
    return {key: (who[key], amount) for key, amount in amounts.items()}


def test_every_planted_problem_is_claimed_and_nothing_else(loaded):
    data, _ = loaded
    found, expected = _claims(), _expected(data)
    assert expected, "the dataset should plant problems"

    missed = sorted(set(expected) - set(found))
    invented = sorted(set(found) - set(expected))
    assert missed == [], f"{len(missed)} planted problems not claimed"
    assert invented == [], f"{len(invented)} claims with no planted problem"

    wrong = {
        key: (found[key], expected[key])
        for key in expected
        if found[key][0] != expected[key][0] or abs(found[key][1] - expected[key][1]) > MONEY_SLACK
    }
    assert wrong == {}


def test_the_briefs_sample_orders_match_exactly(loaded):
    with get_engine().begin() as conn:
        rows = conn.execute(
            text(
                "SELECT o.order_id, r.expected_paise, r.received_paise, r.diff_paise, r.status, "
                "r.tcs_paise + r.tds_paise AS taxes FROM order_recon r "
                "JOIN orders o ON o.id = r.order_id "
                "WHERE o.order_id IN ('ORD-W-204118', 'ORD-M-88120', 'ORD-W-204377')"
            )
        ).all()
    assert {tuple(r) for r in rows} == {
        ("ORD-W-204118", 140900, 0, -140900, "unpaid_past_due", 0),
        ("ORD-M-88120", 89900, 86891, -3009, "short_paid", -691),
        ("ORD-W-204377", -9600, -14200, -4600, "freight_overcharged", 0),
    }


def test_systematic_gap_is_found_for_the_partner_that_has_one(loaded):
    data, _ = loaded
    with get_engine().begin() as conn:
        rows = conn.execute(
            text(
                "SELECT p.name, c.component, c.period_month, c.amount_paise FROM claims c "
                "JOIN partners p ON p.id = c.partner_id WHERE c.type = 'systematic_gap'"
            )
        ).all()
    assert {r.name for r in rows} == {QC2}  # Marketplace 2's ±₹1–2 noise must not trip it
    assert {r.component for r in rows} == {"fixed_fee"}
    planted = {t.amount for t in data.truth if t.issue == "systematic_gap"}
    big_enough = {a for a in planted if a / 500 >= 50}  # ₹5 an order, at least 50 orders
    assert {r.amount_paise for r in rows} == big_enough


def test_planted_overpayments_show_but_are_not_claimed(loaded):
    data, _ = loaded
    held_back = {r["order_ref"] for r in data.demo_rows[M1_NEW_FORMAT.filename]}
    overpaid = {t.order_id for t in data.truth if t.issue == "overpaid"} - held_back
    with get_engine().begin() as conn:
        found = set(
            conn.execute(
                text(
                    "SELECT o.order_id FROM order_recon r JOIN orders o ON o.id = r.order_id "
                    "WHERE r.status = 'overpaid'"
                )
            ).scalars()
        )
    assert found == overpaid


def test_deductions_visible_only_in_the_net_are_caught(loaded):
    """Quick-commerce 1's report has no adjustments column: the short payment shows in Payout."""
    data, _ = loaded
    planted = {
        t.order_id
        for t in data.truth
        if t.partner == "Quick-commerce 1" and t.detail == "unexplained deduction"
    }
    assert planted
    assert planted <= {order for order, kind in _claims() if kind == "short_paid"}


def test_late_remittances_close_their_claims(loaded):
    data, out = loaded
    late_file = out / "demo" / "couriera_cod_remittance_late.csv"
    late = {r["order_ref"] for r in data.demo_rows[late_file.name]}
    assert late
    before = _claims()
    assert all((order, "unpaid_past_due") in before for order in late)

    courier_a_cod = next(
        f for f in SEED_FORMATS if f.partner == COURIER_A and f.filename.endswith("remittance.csv")
    )
    _upload(courier_a_cod.source_name, late_file)

    after = _claims()
    assert not any((order, "unpaid_past_due") in after for order in late)
    with get_engine().begin() as conn:
        statuses = set(
            conn.execute(
                text(
                    "SELECT r.status FROM order_recon r JOIN orders o ON o.id = r.order_id "
                    "WHERE o.order_id = ANY(:orders)"
                ),
                {"orders": list(late)},
            ).scalars()
        )
    assert statuses == {"settled"}


def test_loading_in_any_order_gives_the_same_claims(dataset):
    data, out = dataset
    with get_engine().begin() as conn:
        seed(conn, AS_OF, PEOPLE)
    for fmt in reversed(SEED_FORMATS):  # orders arrive last
        _upload(fmt.source_name, out / "seed" / fmt.filename)
    assert set(_claims()) == set(_expected(data))


def test_rematching_everything_changes_nothing(loaded):
    before = _claims()
    with get_engine().begin() as conn:
        stats = match_all(conn, AS_OF)
    assert (stats["claims_opened"], stats["claims_updated"], stats["claims_resolved"]) == (0, 0, 0)
    assert _claims() == before
