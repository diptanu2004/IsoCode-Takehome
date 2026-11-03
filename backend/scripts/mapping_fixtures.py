"""Report layouts with column names the rule synonyms were not written for (TDD §9.4).

Each case lists columns as (header, kind of values, field it should map to or None). Values are
generated from the kind, so the rules and the AI see realistic shapes without real data.
"""

import random
from dataclasses import dataclass

import polars as pl

from app.enums import FileKind
from app.ingest.reader import RawTable


@dataclass(frozen=True)
class Case:
    name: str
    kind: FileKind
    columns: tuple[tuple[str, str, str | None], ...]

    def expected(self) -> dict[str, str]:
        return {f: h for h, _, f in self.columns if f}


def _values(kind: str, n: int, rng: random.Random) -> list[str]:
    makers = {
        "date_dmy": lambda: f"{rng.randint(1, 28):02d}/{rng.randint(9, 11):02d}/2026",
        "date_iso": lambda: f"2026-{rng.randint(9, 11):02d}-{rng.randint(1, 28):02d}",
        "date_mon": lambda: f"{rng.randint(1, 28):02d}-{rng.choice(['Sep', 'Oct', 'Nov'])}-2026",
        "datetime": lambda: (
            f"2026-10-{rng.randint(1, 28):02d} {rng.randint(9, 18)}:{rng.randint(10, 59)}"
        ),
        "order_id": lambda: f"ORD-M-{rng.randint(80000, 99999)}",
        "awb": lambda: f"CA{rng.randint(4000000000, 4999999999)}",
        "awb_num": lambda: str(rng.randint(790000000000, 799999999999)),
        "utr": lambda: f"UTR{rng.randint(10**13, 10**14 - 1)}",
        "sale": lambda: f"{rng.choice([499, 899, 1199, 1499])}.00",
        "fee_pos": lambda: f"{rng.randint(10, 300)}.{rng.randint(0, 99):02d}",
        "fee_neg": lambda: f"-{rng.randint(10, 300)}.{rng.randint(0, 99):02d}",
        "small_pos": lambda: f"{rng.randint(1, 9)}.{rng.randint(0, 99):02d}",
        "adjust": lambda: rng.choice(["0.00", "0.00", "0.00", "-120.00", "50.00"]),
        "grams": lambda: str(rng.randint(250, 2500)),
        "kg": lambda: f"{rng.randint(250, 2500) / 1000:.3f}",
        "units": lambda: str(rng.choice([1, 1, 1, 2, 3])),
        "zone": lambda: rng.choice("ABCDE"),
        "leg": lambda: rng.choice(["Forward", "Forward", "Return"]),
        "pay": lambda: rng.choice(["COD", "Prepaid"]),
        "status": lambda: rng.choice(["Delivered", "Delivered", "Returned to warehouse"]),
        "channel": lambda: rng.choice(["Website", "Marketplace 1", "Marketplace 2"]),
        "courier": lambda: rng.choice(["Courier A", "Courier B"]),
        "condition": lambda: rng.choice(["Good", "Good", "Damaged"]),
        "name": lambda: rng.choice(["Asha Rao", "Dev Nair", "Meera Iyer", "Kabir Das"]),
        "phone": lambda: f"9{rng.randint(10**8, 10**9 - 1)}",
        "address": lambda: f"{rng.randint(1, 300)}, Road No. {rng.randint(1, 20)}, Bengaluru",
        "pincode": lambda: str(rng.randint(560001, 560100)),
        "batch": lambda: rng.choice(["B1", "B2"]),
    }
    return [makers[kind]() for _ in range(n)]


def table_for(case: Case, rows: int = 60, seed: int = 1) -> RawTable:
    rng = random.Random(seed)
    headers = [h for h, _, _ in case.columns]
    data = pl.DataFrame(
        {h: _values(k, rows, rng) for h, k, _ in case.columns},
        schema=dict.fromkeys(headers, pl.String),
    )
    return RawTable(headers, data, header_row=1, first_row_no=2)


S, C, F, ORD, R = (
    FileKind.SETTLEMENT,
    FileKind.COD_REMITTANCE,
    FileKind.FREIGHT_INVOICE,
    FileKind.ORDERS,
    FileKind.RETURN_SCANS,
)

CASES = (
    Case(
        "settlement, abbreviations",
        S,
        (
            ("Txn Dt", "date_dmy", "txn_date"),
            ("Sub-Order ID", "order_id", "order_ref"),
            ("Gross Amt", "sale", "sale"),
            ("Mkt Fee", "fee_pos", "commission"),
            ("Fixed Chg", "fee_pos", "fixed_fee"),
            ("Ship Chg", "fee_pos", "shipping_fee"),
            ("Tax Collected at Source", "small_pos", "tcs"),
            ("Tax Deducted at Source", "small_pos", "tds"),
            ("Credited Amt", "sale", "net"),
            ("Bank Txn Ref", "utr", "reference"),
        ),
    ),
    Case(
        "settlement, long names",
        S,
        (
            ("Settled On", "date_mon", "txn_date"),
            ("Marketplace Order ID", "order_id", "order_ref"),
            ("Product Price", "sale", "sale"),
            ("Seller Commission", "fee_neg", "commission"),
            ("Closing Charge", "fee_neg", "fixed_fee"),
            ("Forward Logistics", "fee_neg", "shipping_fee"),
            ("Reverse Logistics", "fee_neg", "return_shipping_fee"),
            ("GST TCS (0.5%)", "small_pos", "tcs"),
            ("TDS u/s 194-O", "small_pos", "tds"),
            ("Misc Adjustments", "adjust", "adjustment"),
            ("Payout Amount", "sale", "net"),
            ("NEFT Ref", "utr", "reference"),
            ("Buyer Name", "name", None),
        ),
    ),
    Case(
        "COD remittance",
        C,
        (
            ("Tracking No", "awb", "awb"),
            ("Merchant Order", "order_id", "order_ref"),
            ("Amount Collected", "sale", "sale"),
            ("Freight Charges", "fee_pos", "freight_forward"),
            ("COD Handling", "small_pos", "cod_fee"),
            ("Remitted Amount", "sale", "net"),
            ("Credit Date", "date_iso", "txn_date"),
            ("UTR Number", "utr", "reference"),
            ("Chargeable Wt (kg)", "kg", "billed_weight"),
            ("Delivery Zone", "zone", "zone"),
        ),
    ),
    Case(
        "freight invoice",
        F,
        (
            ("Bill No", "batch", "reference"),
            ("Bill Date", "date_dmy", "txn_date"),
            ("Consignment No", "awb_num", "awb"),
            ("Ref No", "order_id", "order_ref"),
            ("Direction", "leg", "charge_type"),
            ("Zone", "zone", "zone"),
            ("Wt Charged (gm)", "grams", "billed_weight"),
            ("Total Freight", "fee_pos", "amount"),
        ),
    ),
    Case(
        "orders export",
        ORD,
        (
            ("Order Ref", "order_id", "order_id"),
            ("Sales Channel", "channel", "channel"),
            ("Placed On", "date_dmy", "order_date"),
            ("Customer", "name", None),
            ("Mobile", "phone", None),
            ("Ship To", "address", None),
            ("PIN", "pincode", None),
            ("Pay Mode", "pay", "payment_mode"),
            ("Order Total", "sale", "value"),
            ("Qty", "units", "units"),
            ("Weight (g)", "grams", "declared_weight"),
            ("Carrier", "courier", "courier"),
            ("Tracking No", "awb", "awb"),
            ("Zone", "zone", "zone"),
            ("Order Status", "status", "delivery_status"),
            ("Delivered Date", "date_dmy", "delivered_at"),
            ("RTO Date", "date_dmy", "returned_at"),
        ),
    ),
    Case(
        "return scans",
        R,
        (
            ("Received At", "datetime", "scanned_at"),
            ("Waybill No", "awb", "awb"),
            ("Order Number", "order_id", "order_ref"),
            ("QC Result", "condition", "condition"),
        ),
    ),
)
