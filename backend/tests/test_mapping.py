"""Mapping suggestions (rules, AI, merge) and the review → preview → approve → rollback flow."""

from datetime import date

import polars as pl
import pytest
from sqlalchemy import func, select

from app.db import get_engine
from app.enums import FileKind, Role
from app.ingest import worker
from app.mapping import suggest as suggest_module
from app.mapping.fields import field
from app.mapping.llm import AISuggestionError, FieldChoice, MappingProposal
from app.mapping.profile import profile_column
from app.mapping.rules import infer_options, suggest_with_rules
from app.schema import audit_log, mapping_versions, sources
from scripts.catalog import M1_NEW_FORMAT
from scripts.generate_data import generate, write_dataset
from scripts.seed import DemoUser, seed

AS_OF = date(2026, 11, 3)


def _profile(header: str, values: list[str]):
    return profile_column(header, pl.Series(values, dtype=pl.String))


# --- Column profiles -------------------------------------------------------------


def test_profile_spots_personal_data_by_name_and_by_value():
    assert _profile("Customer Name", ["Asha Rao", "Dev Nair"]).personal
    assert _profile("Contact", ["9876543210", "+91 9123456780"]).personal
    assert _profile("Alt", ["9876543210", "9123456780"]).personal  # phone-shaped values
    assert _profile("Order No", ["9876543210", "9123456780"]).personal is None  # an ID
    assert _profile("Pincode", ["560001", "560034"]).personal is None


def test_profile_describes_values_without_showing_them():
    p = _profile("Paid On", ["03-Nov-2026", "01-Nov-2026"])
    assert (p.kind, p.date_format) == ("date", "%d-%b-%Y")
    fee = _profile("Fee", ["105.00", "30.00"])
    assert fee.kind == "amount" and "all positive" in fee.detail
    assert "105" not in str(fee.for_ai()) and "30" not in str(fee.for_ai())


def test_ambiguous_day_month_dates_are_read_the_indian_way():
    assert _profile("Date", ["03/11/2026", "04/11/2026"]).date_format == "%d/%m/%Y"


# --- Rules -----------------------------------------------------------------------


def _profiles(columns: dict[str, list[str]]):
    return [_profile(h, v) for h, v in columns.items()]


NEW_M1 = {
    "Payment Date": ["03-Nov-2026", "03-Nov-2026"],
    "Order Number": ["ORD-M-80089", "ORD-M-80090"],
    "Gross Sale (INR)": ["699.00", "1199.00"],
    "Fee: Commission": ["105.00", "180.00"],
    "Fee: Closing": ["30.00", "30.00"],
    "Fee: Shipping": ["90.00", "90.00"],
    "GST TCS": ["3.33", "5.71"],
    "Income Tax TDS": ["0.70", "1.20"],
    "Amount Paid": ["469.97", "862.00"],
    "Bank Reference": ["M1-261103-1", "M1-261103-2"],
    "Customer Phone": ["9876543210", "9123456780"],
}


def test_rules_map_a_renamed_settlement_report():
    got = suggest_with_rules(FileKind.SETTLEMENT, _profiles(NEW_M1))
    assert {k: c.column for k, c in got.items()} == {
        "txn_date": "Payment Date",
        "order_ref": "Order Number",
        "sale": "Gross Sale (INR)",
        "commission": "Fee: Commission",
        "fixed_fee": "Fee: Closing",
        "shipping_fee": "Fee: Shipping",
        "tcs": "GST TCS",
        "tds": "Income Tax TDS",
        "net": "Amount Paid",
        "reference": "Bank Reference",
    }


def test_rules_never_map_personal_data_columns():
    got = suggest_with_rules(FileKind.SETTLEMENT, _profiles(NEW_M1))
    assert "Customer Phone" not in {c.column for c in got.values()}


def test_options_are_read_from_the_values():
    kind = FileKind.SETTLEMENT
    assert infer_options(field(kind, "commission"), _profile("Fee", ["105.00"])) == {"negate": True}
    assert infer_options(field(kind, "commission"), _profile("Fee", ["-105.00"])) == {}
    assert infer_options(field(kind, "txn_date"), _profile("D", ["03-Nov-2026"])) == {
        "date_format": "%d-%b-%Y"
    }
    weight = field(FileKind.FREIGHT_INVOICE, "billed_weight")
    assert infer_options(weight, _profile("Charged Weight (kg)", ["0.724", "1.2"])) == {
        "scale": 1000
    }
    leg = field(FileKind.FREIGHT_INVOICE, "charge_type")
    assert infer_options(leg, _profile("Leg", ["FWD", "RTN"] * 20)) == {
        "values": {"FWD": "forward", "RTN": "return"}
    }


# --- Merging rules with AI (AI stubbed) ------------------------------------------


def _with_ai(monkeypatch, answer):
    def fake(kind, partner, profiles):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(suggest_module, "suggest_with_ai", fake)


def _build(profiles, saved=()):
    return suggest_module.build_suggestion(
        FileKind.SETTLEMENT, "Marketplace 1", profiles, "fp", list(saved), use_ai=True
    )


def test_agreement_raises_confidence(monkeypatch):
    _with_ai(
        monkeypatch,
        MappingProposal(
            fields=[
                FieldChoice(
                    field="commission",
                    column="Fee: Commission",
                    confidence=0.9,
                    reason="Commission fee",
                )
            ],
            personal_data_columns=["Customer Phone"],
        ),
    )
    s = _build(_profiles(NEW_M1))
    assert s.ai_status == "used"
    assert s.fields["commission"].source == "both"
    assert s.fields["commission"].confidence >= 0.95
    assert "Customer Phone" in s.personal_data_columns


def test_disagreement_is_flagged_for_review(monkeypatch):
    _with_ai(
        monkeypatch,
        MappingProposal(
            fields=[FieldChoice(field="sale", column="Amount Paid", confidence=0.99, reason="x")],
            personal_data_columns=[],
        ),
    )
    s = _build(_profiles(NEW_M1))
    sale = s.fields["sale"]
    assert sale.confidence < suggest_module.REVIEW_BELOW
    assert {sale.column, sale.alternative} == {"Amount Paid", "Gross Sale (INR)"}


def test_ai_naming_a_column_that_does_not_exist_is_ignored(monkeypatch):
    _with_ai(
        monkeypatch,
        MappingProposal(
            fields=[
                FieldChoice(field="tcs", column="TCS Amount", confidence=1.0, reason="made up"),
                FieldChoice(field="not_a_field", column="GST TCS", confidence=1.0, reason="?"),
            ],
            personal_data_columns=["Nope"],
        ),
    )
    s = _build(_profiles(NEW_M1))
    assert s.fields["tcs"].column == "GST TCS" and s.fields["tcs"].source == "rules"
    assert "not_a_field" not in s.fields and "Nope" not in s.personal_data_columns


def test_ai_failure_falls_back_to_rules(monkeypatch):
    _with_ai(monkeypatch, AISuggestionError("The AI took too long to answer."))
    s = _build(_profiles(NEW_M1))
    assert s.ai_status == "unavailable"
    assert s.ai_message.startswith("The AI took too long to answer.")
    assert s.fields["commission"].column == "Fee: Commission"


def test_a_saved_version_with_the_same_columns_is_reused(monkeypatch):
    _with_ai(monkeypatch, AssertionError("the AI must not be called"))
    saved = suggest_module.SavedVersion(
        3, "fp", {"fields": {"sale": {"column": "Gross Sale (INR)"}}}, ["Customer Phone"], False
    )
    s = _build(_profiles(NEW_M1), [saved])
    assert s.matched_version == 3 and s.ai_status == "not_needed"
    assert s.fields["sale"].source == "saved"


# --- The flow through the API ----------------------------------------------------


@pytest.fixture(scope="module")
def demo_file(tmp_path_factory):
    out = tmp_path_factory.mktemp("data")
    write_dataset(generate(AS_OF, 3000, 60, 11), out, {"as_of": AS_OF.isoformat()})
    return (out / "demo" / M1_NEW_FORMAT.filename).read_bytes()


@pytest.fixture
def waiting_upload(signed_in, demo_file):
    with get_engine().begin() as conn:
        seed(conn, AS_OF, [DemoUser("cf@demo.test", "C", "password-123", Role.COFOUNDER)])
        source_id = conn.execute(
            select(sources.c.id).where(sources.c.name == M1_NEW_FORMAT.source_name)
        ).scalar_one()
    client = signed_in(Role.EXEC)
    upload = client.post(
        "/api/uploads",
        data={"source_id": str(source_id)},
        files={"file": (M1_NEW_FORMAT.filename, demo_file, "text/csv")},
    ).json()
    worker.run_one()
    assert client.get(f"/api/uploads/{upload['id']}").json()["status"] == "needs_mapping"
    return client, upload["id"], source_id


def _spec(suggestion: dict) -> dict:
    return {
        "fields": {
            name: {"column": s["column"], **s["options"]}
            for name, s in suggestion["fields"].items()
            if s["column"]
        },
        "personal_data_columns": suggestion["personal_data_columns"],
        "provenance": {name: s["source"] for name, s in suggestion["fields"].items()},
    }


def test_review_suggests_a_complete_mapping_without_ai(waiting_upload):
    client, upload_id, _ = waiting_upload
    review = client.get(f"/api/uploads/{upload_id}/mapping").json()
    suggestion = review["suggestion"]
    assert suggestion["ai_status"] == "unavailable"  # no API key in tests
    assert "Showing rule-based suggestions" in suggestion["ai_message"]
    assert suggestion["fields"]["commission"]["column"] == "Fee: Commission"
    assert suggestion["fields"]["commission"]["options"] == {"negate": True}
    assert suggestion["fields"]["txn_date"]["options"] == {"date_format": "%d-%b-%Y"}
    assert review["active_version"] == 1
    assert review["upload"]["header_row"] == 3


def test_preview_shows_twenty_rows_as_they_will_load(waiting_upload):
    client, upload_id, _ = waiting_upload
    suggestion = client.get(f"/api/uploads/{upload_id}/mapping").json()["suggestion"]
    preview = client.post(f"/api/uploads/{upload_id}/mapping/preview", json=_spec(suggestion))
    body = preview.json()
    assert preview.status_code == 200
    assert len(body["rows"]) == min(20, body["total_rows"]) > 0
    assert body["blocking"] == [] and body["rejected_rows"] == 0
    first = body["rows"][0]["values"]
    assert first["commission"] < 0 and first["sale"] > 0  # deductions stored as negatives
    assert first["txn_date"] == "2026-11-03"


def test_preview_explains_what_blocks_approval(waiting_upload):
    client, upload_id, _ = waiting_upload
    spec = {
        "fields": {
            "sale": {"column": "Gross Sale (INR)"},
            "txn_date": {"column": "Payment Date", "date_format": "%d/%m/%Y"},
        }
    }
    body = client.post(f"/api/uploads/{upload_id}/mapping/preview", json=spec).json()
    assert "Order ID needs a column." in body["blocking"]
    assert any("can't be read" in b for b in body["blocking"])
    assert body["rows"][0]["problems"]["txn_date"].startswith("Settlement date:")


def test_approve_saves_a_version_and_loads_the_file(waiting_upload):
    client, upload_id, source_id = waiting_upload
    suggestion = client.get(f"/api/uploads/{upload_id}/mapping").json()["suggestion"]
    spec = _spec(suggestion)
    spec["provenance"]["reference"] = "manual"
    response = client.post(f"/api/uploads/{upload_id}/mapping", json=spec)
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2 and upload_id in response.json()["requeued_uploads"]

    worker.run_one()
    done = client.get(f"/api/uploads/{upload_id}").json()
    assert done["status"] == "done" and done["mapping_version"] == 2
    assert done["stats"]["lines_created"] > 0  # no orders loaded here, so none match yet

    versions = client.get(f"/api/sources/{source_id}/mappings").json()
    assert [(v["version"], v["is_active"]) for v in versions] == [(2, True), (1, False)]
    assert versions[0]["suggested_by"] == "manual"
    with get_engine().begin() as conn:
        actions = conn.execute(select(audit_log.c.action)).scalars().all()
    assert actions == ["mapping.approve"]


def test_approve_refuses_an_incomplete_mapping(waiting_upload):
    client, upload_id, _ = waiting_upload
    response = client.post(
        f"/api/uploads/{upload_id}/mapping",
        json={"fields": {"sale": {"column": "Gross Sale (INR)"}}},
    )
    assert response.status_code == 422
    assert "Order ID needs a column." in response.json()["detail"]


def test_rollback_makes_an_earlier_version_active(waiting_upload):
    client, upload_id, source_id = waiting_upload
    suggestion = client.get(f"/api/uploads/{upload_id}/mapping").json()["suggestion"]
    client.post(f"/api/uploads/{upload_id}/mapping", json=_spec(suggestion))
    v1 = next(
        v for v in client.get(f"/api/sources/{source_id}/mappings").json() if v["version"] == 1
    )
    response = client.post(f"/api/sources/{source_id}/mappings/{v1['id']}/activate")
    assert response.json()["active_version"] == 1
    with get_engine().begin() as conn:
        active = conn.execute(
            select(mapping_versions.c.version).where(
                mapping_versions.c.source_id == source_id, mapping_versions.c.is_active
            )
        ).scalar_one()
        audits = conn.execute(select(func.count()).select_from(audit_log)).scalar_one()
    assert active == 1 and audits == 2


def test_mapping_endpoints_refuse_a_file_that_is_not_waiting(waiting_upload):
    client, upload_id, _ = waiting_upload
    suggestion = client.get(f"/api/uploads/{upload_id}/mapping").json()["suggestion"]
    client.post(f"/api/uploads/{upload_id}/mapping", json=_spec(suggestion))
    assert client.get(f"/api/uploads/{upload_id}/mapping").status_code == 409


def test_invalid_options_are_rejected(waiting_upload):
    client, upload_id, _ = waiting_upload
    bad = {"fields": {"txn_date": {"column": "Payment Date", "date_format": "%Q"}}}
    assert client.post(f"/api/uploads/{upload_id}/mapping/preview", json=bad).status_code == 422
