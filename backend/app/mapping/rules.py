"""Rule-based mapping suggestions: known column names, fuzzy matching and value shapes.

Always available, so the mapping screen works even when the AI is switched off or fails. The same
module infers the options for a chosen column (sign, date format, units, label map), because those
are read from the data, which the AI never sees.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.enums import DeliveryStatus, FileKind, PaymentMode
from app.mapping.fields import FIELDS, ChargeType, FieldType, TargetField
from app.mapping.profile import ColumnProfile

_ID_NAMES = [
    "order id",
    "order no",
    "order number",
    "order ref",
    "order reference",
    "sub order no",
    "order item id",
    "client order id",
]
_DATE_NAMES = [
    "settlement date",
    "payout date",
    "payment date",
    "paid on",
    "remittance date",
    "remit date",
    "invoice date",
    "inv date",
    "payout cycle end",
    "transaction date",
    "date",
]

SYNONYMS: dict[str, list[str]] = {
    "order_id": _ID_NAMES,
    "order_ref": _ID_NAMES,
    "awb": [
        "awb",
        "awb no",
        "awb number",
        "waybill",
        "tracking number",
        "tracking id",
        "shipment id",
        "docket",
    ],
    "channel": ["channel", "sales channel", "marketplace", "platform", "source"],
    "order_date": ["order date", "ordered on", "created at", "order created", "purchase date"],
    "value": ["order value", "order amount", "order total", "invoice value", "total amount"],
    "units": ["units", "qty", "quantity", "items"],
    "payment_mode": ["payment method", "payment mode", "payment type", "cod prepaid"],
    "declared_weight": ["pkg weight", "package weight", "dead weight", "declared weight", "weight"],
    "courier": ["courier", "carrier", "logistics partner", "shipping partner"],
    "zone": ["zone", "shipping zone", "delivery zone"],
    "delivery_status": ["status", "delivery status", "shipment status", "order status"],
    "delivered_at": ["delivered on", "delivered date", "delivery date", "delivered at"],
    "returned_at": ["returned on", "return date", "rto date", "returned at"],
    "txn_date": _DATE_NAMES,
    "sale": [
        "sale amount",
        "gross sale",
        "item price",
        "selling price",
        "gmv",
        "order value",
        "cod amount",
        "cod collected",
        "collected amount",
        "gross sale inr",
    ],
    "commission": [
        "commission",
        "referral fee",
        "marketplace commission",
        "platform commission",
        "fee commission",
        "commission fee",
    ],
    "fixed_fee": [
        "closing fee",
        "fixed fee",
        "fixed closing fee",
        "platform fee",
        "fee closing",
        "closing charges",
    ],
    "shipping_fee": [
        "shipping fee",
        "shipping charges",
        "logistics fee",
        "delivery charges",
        "fee shipping",
        "forward shipping fee",
    ],
    "return_shipping_fee": [
        "reverse shipping fee",
        "return shipping fee",
        "return shipping charges",
        "return logistics fee",
        "fee reverse shipping",
        "reverse shipping charges",
    ],
    "tcs": ["tcs", "gst tcs", "tcs gst", "tcs deducted"],
    "tds": ["tds", "income tax tds", "tds 194 o", "it tds", "tds deducted"],
    "adjustment": [
        "adjustment",
        "adjustments",
        "other adjustments",
        "deductions adjustments",
        "other deductions",
    ],
    "net": [
        "net payable",
        "net amount",
        "settlement value",
        "final settlement",
        "amount paid",
        "net payout",
        "payout",
        "net remitted",
        "amount remitted",
    ],
    "reference": [
        "utr",
        "bank reference",
        "bank utr",
        "payout ref",
        "payout id",
        "transaction id",
        "reference",
        "ref",
        "invoice no",
        "invoice number",
        "inv no",
    ],
    "freight_forward": ["freight deducted", "shipping charge", "forward freight"],
    "cod_fee": ["cod charges", "cod fee", "cod handling fee", "collection fee"],
    "billed_weight": ["charged weight", "billed weight", "billed wt", "chargeable weight"],
    "charge_type": ["shipment type", "leg", "direction", "movement", "charge type"],
    "amount": ["freight", "freight amount", "amount", "total charge"],
    "scanned_at": ["scan time", "scanned at", "received at", "receipt time", "scan date"],
    "condition": ["condition", "qc status", "item condition"],
}

# Labels partners use for the enum fields, matched case-insensitively.
VALUE_SYNONYMS: dict[str, dict[str, list[str]]] = {
    "payment_mode": {
        PaymentMode.COD: ["cod", "cash on delivery", "cash", "pay on delivery"],
        PaymentMode.PREPAID: ["prepaid", "paid", "online", "pre-paid", "upi", "card"],
    },
    "charge_type": {
        ChargeType.FORWARD: ["forward", "fwd", "fw", "outbound", "delivery"],
        ChargeType.RETURN: ["return", "rto", "rtn", "reverse", "rev", "inbound"],
    },
    "delivery_status": {
        DeliveryStatus.PENDING: ["pending", "new", "processing", "confirmed"],
        DeliveryStatus.IN_TRANSIT: ["in transit", "shipped", "out for delivery", "dispatched"],
        DeliveryStatus.DELIVERED: ["delivered"],
        DeliveryStatus.RETURN_IN_TRANSIT: ["rto in transit", "return in transit", "rto initiated"],
        DeliveryStatus.RETURNED_TO_WAREHOUSE: [
            "returned to warehouse",
            "rto delivered",
            "returned",
            "return delivered",
        ],
        DeliveryStatus.CANCELLED: ["cancelled", "canceled"],
    },
}

# How well a column's value shape suits each field type (1 = ideal, low = unlikely).
_FIT: dict[FieldType, dict[str, float]] = {
    FieldType.MONEY: {"amount": 1.0, "integer": 0.9},
    FieldType.ID: {"id": 1.0, "integer": 0.6, "text": 0.5, "category": 0.4},
    FieldType.DATE: {"date": 1.0, "datetime": 0.9},
    FieldType.DATETIME: {"datetime": 1.0, "date": 0.8},
    FieldType.INTEGER: {"integer": 1.0, "amount": 0.6},
    FieldType.WEIGHT: {"integer": 1.0, "amount": 1.0},
    FieldType.ENUM: {"category": 1.0, "text": 0.4},
    FieldType.TEXT: {"category": 1.0, "text": 0.9, "id": 0.8},
}
_UNIT_WORDS = re.compile(r"\b(inr|rs|g|kg|gm|grams|in rupees)\b")
ACCEPT = 0.5  # below this, a field is left unmapped rather than guessed


@dataclass
class Choice:
    column: str | None
    confidence: float
    reason: str


def normalise(header: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", header.lower())
    return re.sub(r"\s+", " ", _UNIT_WORDS.sub(" ", text)).strip()


def _name_score(f: TargetField, header: str) -> tuple[float, str]:
    name = normalise(header)
    names = SYNONYMS.get(f.name, []) + [normalise(f.label)]
    if name in names:
        return 0.95, "Known name for this field"
    best = max(SequenceMatcher(None, name, n).ratio() for n in names)
    tokens = set(name.split())
    overlap = max(len(tokens & set(n.split())) / max(len(set(n.split())), 1) for n in names)
    score = max(best, overlap * 0.9)
    return score * 0.85, "Name is close to a known name for this field"


def suggest_with_rules(kind: FileKind, profiles: list[ColumnProfile]) -> dict[str, Choice]:
    """Best column per field, each column used once, strongest matches first."""
    candidates = []
    for f in FIELDS[kind]:
        for p in profiles:
            if p.personal or p.kind == "empty":
                continue
            name, why = _name_score(f, p.header)
            fit = _FIT[f.type].get(p.kind, 0.25)
            score = name * fit
            if fit < 0.5:
                why += f", but its values ({p.detail}) don't look like a {f.type.value}"
            candidates.append((score, f.name, p.header, why))

    chosen: dict[str, Choice] = {}
    used: set[str] = set()
    for score, field_name, header, why in sorted(candidates, reverse=True):
        if field_name in chosen or header in used or score < ACCEPT:
            continue
        chosen[field_name] = Choice(header, round(score, 2), why)
        used.add(header)

    # A required date with no name match: if the file has exactly one unused date column, offer
    # it, at a confidence that still asks a person to check.
    dates = [p.header for p in profiles if p.kind in ("date", "datetime") and p.header not in used]
    for f in FIELDS[kind]:
        is_date = f.type in (FieldType.DATE, FieldType.DATETIME)
        if is_date and f.required and f.name not in chosen and len(dates) == 1:
            chosen[f.name] = Choice(dates[0], 0.6, "The only date column in the file")
            used.add(dates[0])
    return chosen


def infer_options(f: TargetField, profile: ColumnProfile, previous: dict | None = None) -> dict:
    """Options for reading this column, worked out from its values."""
    options: dict = {}
    if f.type == FieldType.MONEY and f.deduction and profile.negative_share < 0.5:
        options["negate"] = True  # the partner shows money it keeps as positive numbers
    if f.type in (FieldType.DATE, FieldType.DATETIME) and profile.date_format:
        options["date_format"] = profile.date_format
    if f.type == FieldType.WEIGHT and (
        "kg" in profile.header.lower() or (profile.has_decimals and profile.kind == "amount")
    ):
        options["scale"] = 1000
    if f.type == FieldType.ENUM and profile.categories:
        values = map_values(f.name, profile.categories)
        if values:
            options["values"] = values
    if f.type == FieldType.ID and previous and previous.get("strip_suffix"):
        options["strip_suffix"] = previous["strip_suffix"]  # partners keep their ID habits
    return options


def map_values(field_name: str, labels: list[str]) -> dict[str, str]:
    known = VALUE_SYNONYMS.get(field_name, {})
    lookup = {syn: value.value for value, synonyms in known.items() for syn in synonyms}
    result = {}
    for label in labels:
        key = re.sub(r"\s+", " ", label.strip().lower())
        if key in lookup:
            result[label] = lookup[key]
    return result
