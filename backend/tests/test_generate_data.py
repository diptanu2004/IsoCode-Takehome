import csv
from collections import defaultdict
from datetime import date

import fastexcel
import pytest

from app.enums import FileKind
from app.mapping.fields import FIELDS
from app.mapping.fingerprint import header_fingerprint
from scripts.catalog import M1_NEW_FORMAT, ORDERS_FORMAT, SEED_FORMATS, saved_mapping
from scripts.generate_data import generate, write_dataset

AS_OF = date(2026, 11, 3)


@pytest.fixture(scope="module")
def small():
    return generate(AS_OF, n_orders=3000, days=60, seed=11)


@pytest.fixture(scope="module")
def written(small, tmp_path_factory):
    out = tmp_path_factory.mktemp("data")
    manifest = write_dataset(small, out, {"as_of": AS_OF.isoformat()})
    return out, manifest


def _truth_by_order(data) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in data.truth:
        totals[t.order_id][t.issue] += t.amount
    return totals


def test_same_seed_same_data():
    a = generate(AS_OF, n_orders=500, days=30, seed=3)
    b = generate(AS_OF, n_orders=500, days=30, seed=3)
    assert [o.order_id for o in a.orders] == [o.order_id for o in b.orders]
    assert a.truth == b.truth
    assert a.rows == b.rows


def test_brief_sample_orders_are_reproduced(small):
    truth = _truth_by_order(small)
    assert truth["ORD-W-204118"] == {"unpaid_past_due": 140900}
    assert truth["ORD-M-88120"] == {"short_paid": 3009}
    assert truth["ORD-W-204377"] == {"freight_overcharged": 4600}

    m120 = next(r for r in small.rows["Marketplace 1"] if r["order_ref"] == "ORD-M-88120")
    assert m120["net"] == 86200  # the ₹862 received in the brief
    assert m120["tcs"] + m120["tds"] == -691  # shown separately, not as leakage
    assert m120["sale"] + m120["commission"] + m120["fixed_fee"] + m120["shipping_fee"] == 86891

    w377 = [r for rows in small.rows.values() for r in rows if r.get("awb") == "790000204377"]
    assert sorted(r["amount"] for r in w377 if "amount" in r) == [-7100, -7100]  # ₹142 billed

    remitted = {r["awb"] for r in small.rows[("Courier A", FileKind.COD_REMITTANCE)]}
    assert "CA4000204118" not in remitted


def test_leakage_is_about_the_briefs_one_and_a_half_percent():
    data = generate(AS_OF, n_orders=20000, days=60, seed=5)
    gmv = sum(o.value for o in data.orders if not o.cancelled)
    leaked = sum(t.amount for t in data.truth if t.issue != "overpaid")
    assert 1.2 <= 100 * leaked / gmv <= 1.8


def test_every_issue_type_is_planted(small):
    issues = {t.issue for t in small.truth}
    assert issues >= {
        "unpaid_past_due",
        "short_paid",
        "freight_overcharged",
        "return_not_received",
        "systematic_gap",
    }


@pytest.mark.parametrize("fmt", [*SEED_FORMATS, M1_NEW_FORMAT], ids=lambda f: f.filename)
def test_saved_mappings_use_known_fields_and_cover_required_ones(fmt):
    targets = {f.name: f for f in FIELDS[fmt.file_kind]}
    mapping = saved_mapping(fmt)["fields"]
    assert set(mapping) <= set(targets)
    required = {name for name, f in targets.items() if f.required and not f.key_group}
    assert required <= set(mapping)


def test_changed_format_gets_a_new_fingerprint():
    old = next(f for f in SEED_FORMATS if f.source_name == M1_NEW_FORMAT.source_name)
    assert header_fingerprint([c.header for c in old.columns]) != header_fingerprint(
        [c.header for c in M1_NEW_FORMAT.columns]
    )


def test_files_have_the_partner_headers(written):
    out, manifest = written
    for fmt in SEED_FORMATS:
        path = out / "seed" / fmt.filename
        if fmt.excel:
            sheet = fastexcel.read_excel(path).load_sheet(0, header_row=None)
            header = list(sheet.to_polars().row(len(fmt.title_rows)))
        else:
            with path.open(encoding="utf-8") as f:
                header = list(csv.reader(f))[len(fmt.title_rows)]
        assert header == [c.header for c in fmt.columns], fmt.filename
        assert manifest["files"][f"seed/{fmt.filename}"]["rows"] > 0


def test_orders_file_carries_personal_data_to_be_dropped(written):
    out, _ = written
    with (out / "seed" / ORDERS_FORMAT.filename).open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Customer Name"] and rows[0]["Phone"].startswith("5")
    sample = next(r for r in rows if r["Order No"] == "ORD-W-204377")
    assert sample["Status"] == "Returned to warehouse"
    assert sample["Order Value"] == "2398.00"


def test_demo_files_are_written(written):
    out, manifest = written
    assert (out / "demo" / M1_NEW_FORMAT.filename).exists()
    assert manifest["files"]["demo/couriera_cod_remittance_late.csv"]["rows"] >= 0
    with (out / "demo" / M1_NEW_FORMAT.filename).open(encoding="utf-8") as f:
        lines = list(csv.reader(f))
    assert lines[0] == ["Marketplace 1 - Payment Report"]
    assert lines[len(M1_NEW_FORMAT.title_rows)] == [c.header for c in M1_NEW_FORMAT.columns]
