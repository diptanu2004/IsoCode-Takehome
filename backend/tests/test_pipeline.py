"""The upload pipeline end to end, on a small generated dataset."""

from datetime import date

import pytest
from sqlalchemy import func, select, text

from app.db import get_engine
from app.enums import Role
from app.ingest.pipeline import UploadRejected, process_upload, register_upload
from app.schema import partner_lines, source_rows, sources, uploads
from scripts.catalog import M1_NEW_FORMAT, ORDERS_FORMAT, SEED_FORMATS
from scripts.generate_data import generate, write_dataset
from scripts.seed import DemoUser, seed

AS_OF = date(2026, 11, 3)
PEOPLE = [DemoUser("cofounder@demo.test", "Arjun Menon", "cofounder-password-1", Role.COFOUNDER)]


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("data")
    data = generate(AS_OF, n_orders=3000, days=60, seed=11)
    write_dataset(data, out, {"as_of": AS_OF.isoformat()})
    return data, out


@pytest.fixture
def setup():
    with get_engine().begin() as conn:
        seed(conn, AS_OF, PEOPLE)


def _source_id(name: str) -> int:
    with get_engine().begin() as conn:
        return conn.execute(select(sources.c.id).where(sources.c.name == name)).scalar_one()


def _upload(fmt_or_name, content: bytes, filename: str | None = None) -> tuple[int, str]:
    name = getattr(fmt_or_name, "source_name", fmt_or_name)
    with get_engine().begin() as conn:
        upload_id = register_upload(
            conn,
            _source_id(name),
            filename or getattr(fmt_or_name, "filename", "f.csv"),
            content,
            None,
        )
    return upload_id, process_upload(upload_id)


def _load_all(out) -> dict[str, int]:
    ids = {}
    for fmt in SEED_FORMATS:
        upload_id, status = _upload(fmt, (out / "seed" / fmt.filename).read_bytes())
        assert status == "done", fmt.filename
        ids[fmt.filename] = upload_id
    return ids


def _scalar(sql: str, **params):
    with get_engine().begin() as conn:
        return conn.execute(text(sql), params).scalar()


def test_every_seed_file_loads_without_rejected_rows(setup, dataset):
    data, out = dataset
    _load_all(out)
    assert _scalar("SELECT count(*) FROM orders") == len(data.orders)
    assert _scalar("SELECT coalesce(sum(error_count), 0) FROM uploads") == 0
    # Every line links to its order, except settlement lines that arrived without an order ID.
    missing_ids = sum(1 for t in data.truth if t.detail == "settlement line has no order ID")
    unmatched_rows = _scalar(
        "SELECT count(DISTINCT (upload_id, row_no)) FROM partner_lines WHERE order_id IS NULL"
    )
    assert unmatched_rows == missing_ids
    assert _scalar("SELECT count(*) FROM return_scans WHERE order_id IS NULL") == 0


def test_brief_sample_order_lines_land_with_the_right_signs(setup, dataset):
    _load_all(dataset[1])
    with get_engine().begin() as conn:
        lines = dict(
            conn.execute(
                text(
                    "SELECT pl.component, pl.amount_paise FROM partner_lines pl "
                    "JOIN orders o ON o.id = pl.order_id WHERE o.order_id = 'ORD-M-88120'"
                )
            ).all()
        )
    assert lines == {
        "sale": 119900,
        "commission": -21009,
        "fixed_fee": -3000,
        "shipping_fee": -9000,
        "tcs": -571,
        "tds": -120,
    }
    assert (
        _scalar(
            "SELECT sum(amount_paise) FROM partner_lines pl JOIN orders o ON o.id = pl.order_id "
            "WHERE o.order_id = 'ORD-W-204377'"
        )
        == -14200
    )


def test_partner_id_formats_are_normalised_and_matched(setup, dataset):
    _load_all(dataset[1])
    # Marketplace 3 sends lower-case sub-order IDs with a suffix; Marketplace 2 sends Excel numbers.
    for partner in ("Marketplace 2", "Marketplace 3"):
        unmatched = _scalar(
            "SELECT count(*) FILTER (WHERE pl.order_id IS NULL AND pl.order_ref IS NOT NULL) "
            "FROM partner_lines pl JOIN partners p ON p.id = pl.partner_id WHERE p.name = :p",
            p=partner,
        )
        assert unmatched == 0, partner


def test_personal_data_is_never_stored(setup, dataset):
    _load_all(dataset[1])
    with get_engine().begin() as conn:
        stored = conn.execute(
            select(func.count()).where(
                source_rows.c.raw.has_key("Customer Name")
                | source_rows.c.raw.has_key("Phone")
                | source_rows.c.raw.has_key("Shipping Address")
            )
        ).scalar_one()
    assert stored == 0


def test_lines_never_point_at_a_missing_order(setup, dataset):
    """The bulk tables have no foreign keys (see app/schema.py), so check integrity here."""
    _load_all(dataset[1])
    for table in ("partner_lines", "return_scans"):
        orphans = _scalar(
            f"SELECT count(*) FROM {table} t WHERE t.order_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.id = t.order_id)"
        )
        assert orphans == 0


def test_the_same_file_is_refused_the_second_time(setup, dataset):
    content = (dataset[1] / "seed" / ORDERS_FORMAT.filename).read_bytes()
    _upload(ORDERS_FORMAT, content)
    with pytest.raises(UploadRejected, match="already uploaded"):
        _upload(ORDERS_FORMAT, content)


def test_an_overlapping_export_adds_nothing_twice(setup, dataset):
    _load_all(dataset[1])
    m1 = next(f for f in SEED_FORMATS if f.partner == "Marketplace 1")
    before = _scalar("SELECT count(*) FROM partner_lines")
    # Same rows, different bytes (as a re-download would be): accepted, but no line is new.
    content = (dataset[1] / "seed" / m1.filename).read_bytes() + b"\n"
    upload_id, status = _upload(m1, content, "m1-again.csv")
    assert status == "done"
    assert _scalar("SELECT count(*) FROM partner_lines") == before
    stats = _scalar("SELECT stats FROM uploads WHERE id = :i", i=upload_id)
    assert stats["lines_created"] == 0 and stats["lines_already_loaded"] == stats["lines"]


def test_reuploading_orders_rewrites_only_what_changed(setup, dataset):
    content = (dataset[1] / "seed" / ORDERS_FORMAT.filename).read_bytes()
    _upload(ORDERS_FORMAT, content)
    changed = content.replace(b",Delivered,", b",Returned to warehouse,", 1)
    upload_id, _ = _upload(ORDERS_FORMAT, changed, "orders-next-day.csv")
    stats = _scalar("SELECT stats FROM uploads WHERE id = :i", i=upload_id)
    assert stats["orders_created"] == 0
    assert stats["orders_updated"] == 1


def test_files_can_arrive_before_their_orders(setup, dataset):
    out = dataset[1]
    m1 = next(f for f in SEED_FORMATS if f.partner == "Marketplace 1")
    _upload(m1, (out / "seed" / m1.filename).read_bytes())
    assert _scalar("SELECT count(*) FROM partner_lines WHERE order_id IS NOT NULL") == 0
    upload_id, _ = _upload(ORDERS_FORMAT, (out / "seed" / ORDERS_FORMAT.filename).read_bytes())
    linked = _scalar("SELECT stats->>'newly_linked' FROM uploads WHERE id = :i", i=upload_id)
    assert int(linked) > 0
    assert (
        _scalar(
            "SELECT count(*) FROM partner_lines WHERE order_id IS NULL AND order_ref IS NOT NULL"
        )
        == 0
    )


def test_a_changed_format_waits_for_mapping(setup, dataset):
    content = (dataset[1] / "demo" / M1_NEW_FORMAT.filename).read_bytes()
    upload_id, status = _upload(M1_NEW_FORMAT, content)
    assert status == "needs_mapping"
    detected = _scalar("SELECT detected FROM uploads WHERE id = :i", i=upload_id)
    assert detected["header_row"] == 3
    assert "Fee: Commission" in detected["headers"]
    assert _scalar("SELECT count(*) FROM partner_lines") == 0


def test_bad_rows_are_rejected_with_a_reason(setup):
    content = (
        "Order ID,Settlement Date,Sale Amount,Commission,Closing Fee,Shipping Fee,"
        "Reverse Shipping Fee,TCS,TDS,Other Adjustments,Net Payable,UTR\n"
        + "".join(
            f"ORD-M-{80001 + i},2026-10-01,1199.00,180,30,90,0,5.71,1.20,0,892.09,U{i}\n"
            for i in range(40)
        )
        + "ORD-M-90001,01/10/2026,1199.00,180,30,90,0,5.71,1.20,0,892.09,BAD1\n"
    ).encode()
    upload_id, status = _upload("Marketplace 1 · Settlement", content, "m1.csv")
    assert status == "done"
    with get_engine().begin() as conn:
        upload = conn.execute(select(uploads).where(uploads.c.id == upload_id)).one()
        errors = conn.execute(
            select(source_rows.c.row_no, source_rows.c.error).where(
                source_rows.c.upload_id == upload_id, source_rows.c.error.is_not(None)
            )
        ).all()
        lines = conn.execute(select(func.count()).select_from(partner_lines)).scalar_one()
    assert upload.error_count == 1
    assert errors == [(42, "Settlement date: “01/10/2026” isn't a valid date")]
    assert lines == 40 * 6  # sale, commission, two fees, TCS, TDS; zeros and the bad row add none


def test_too_many_bad_rows_fails_the_whole_upload(setup):
    content = (
        "Order ID,Settlement Date,Sale Amount,Commission,Closing Fee,Shipping Fee,"
        "Reverse Shipping Fee,TCS,TDS,Other Adjustments,Net Payable,UTR\n"
        + "".join(
            f"ORD-M-{80001 + i},2026-10-01,abc,180,30,90,0,5.71,1.20,0,892.09,U{i}\n"
            for i in range(10)
        )
    ).encode()
    upload_id, status = _upload("Marketplace 1 · Settlement", content, "m1.csv")
    assert status == "failed"
    message = _scalar("SELECT error_message FROM uploads WHERE id = :i", i=upload_id)
    assert "10 of 10 rows" in message and "mapped wrongly" in message
    assert _scalar("SELECT count(*) FROM partner_lines") == 0
