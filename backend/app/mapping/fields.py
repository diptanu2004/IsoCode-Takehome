"""The fields each file kind can be mapped to. The mapping screen, the rule-based suggester and
the LLM prompt all read their labels and descriptions from here."""

from dataclasses import dataclass
from enum import StrEnum

from app.enums import Component, DeliveryStatus, FileKind, PaymentMode


class FieldType(StrEnum):
    ID = "id"
    TEXT = "text"
    DATE = "date"
    DATETIME = "datetime"
    MONEY = "money"
    INTEGER = "integer"
    WEIGHT = "weight"  # grams
    ENUM = "enum"


class ChargeType(StrEnum):
    FORWARD = "forward"
    RETURN = "return"


@dataclass(frozen=True)
class TargetField:
    name: str
    label: str
    description: str
    type: FieldType
    required: bool = False
    component: Component | None = None  # money fields that become ledger lines
    deduction: bool = False  # money the partner keeps; stored as a negative amount
    choices: type[StrEnum] | None = None
    key_group: str | None = None  # at least one field of the group must be mapped
    blank_ok: bool = False  # a row may leave it empty; the line then loads unmatched


def _f(*args, **kwargs) -> TargetField:
    return TargetField(*args, **kwargs)


_ORDERS = (
    _f("order_id", "Order ID", "Order ID as issued by the sales channel", FieldType.ID, True),
    _f("channel", "Channel", "Sales channel, e.g. Website or Marketplace 1", FieldType.TEXT, True),
    _f("order_date", "Order date", "Date the customer placed the order", FieldType.DATE, True),
    _f("value", "Order value", "Amount charged to the customer, incl. GST", FieldType.MONEY, True),
    _f("units", "Units", "Number of items in the order", FieldType.INTEGER),
    _f(
        "payment_mode",
        "Payment mode",
        "Cash on delivery or prepaid",
        FieldType.ENUM,
        True,
        choices=PaymentMode,
    ),
    _f("declared_weight", "Declared weight", "Weight declared to the courier", FieldType.WEIGHT),
    _f("courier", "Courier", "Courier partner that shipped the order", FieldType.TEXT),
    _f("awb", "AWB", "Courier tracking number (air waybill)", FieldType.ID),
    _f("zone", "Zone", "Courier delivery zone", FieldType.TEXT),
    _f(
        "delivery_status",
        "Delivery status",
        "Latest shipment status",
        FieldType.ENUM,
        True,
        choices=DeliveryStatus,
    ),
    _f("delivered_at", "Delivered on", "Date delivered to the customer", FieldType.DATE),
    _f("returned_at", "Returned on", "Date the return reached the warehouse", FieldType.DATE),
)

_SETTLEMENT = (
    _f(
        "order_ref",
        "Order ID",
        "Order ID the payout line belongs to",
        FieldType.ID,
        True,
        blank_ok=True,
    ),
    _f("txn_date", "Settlement date", "Date the partner paid out", FieldType.DATE, True),
    _f(
        "sale",
        "Sale amount",
        "Order value the partner collected",
        FieldType.MONEY,
        component=Component.SALE,
    ),
    _f(
        "commission",
        "Commission",
        "Commission charged",
        FieldType.MONEY,
        component=Component.COMMISSION,
        deduction=True,
    ),
    _f(
        "fixed_fee",
        "Fixed fee",
        "Per-order fixed or closing fee",
        FieldType.MONEY,
        component=Component.FIXED_FEE,
        deduction=True,
    ),
    _f(
        "shipping_fee",
        "Shipping fee",
        "Forward shipping fee charged",
        FieldType.MONEY,
        component=Component.SHIPPING_FEE,
        deduction=True,
    ),
    _f(
        "return_shipping_fee",
        "Return shipping fee",
        "Shipping fee for a return",
        FieldType.MONEY,
        component=Component.RETURN_SHIPPING_FEE,
        deduction=True,
    ),
    _f(
        "tcs",
        "TCS",
        "GST tax collected at source",
        FieldType.MONEY,
        component=Component.TCS,
        deduction=True,
    ),
    _f(
        "tds",
        "TDS",
        "Income tax deducted at source",
        FieldType.MONEY,
        component=Component.TDS,
        deduction=True,
    ),
    _f(
        "adjustment",
        "Adjustment",
        "Other credits or deductions, signed",
        FieldType.MONEY,
        component=Component.ADJUSTMENT,
    ),
    _f(
        "net",
        "Net paid",
        "Amount paid for the line; used to check the parts add up",
        FieldType.MONEY,
    ),
    _f("reference", "Payout reference", "Payout ID or bank reference (UTR)", FieldType.TEXT),
)

_COD_REMITTANCE = (
    _f("awb", "AWB", "Courier tracking number", FieldType.ID, True, key_group="key"),
    _f(
        "order_ref",
        "Order ID",
        "Order ID, if the courier reports it",
        FieldType.ID,
        key_group="key",
    ),
    _f("txn_date", "Remittance date", "Date the courier remitted the cash", FieldType.DATE, True),
    _f(
        "sale",
        "COD collected",
        "Cash collected from the customer",
        FieldType.MONEY,
        True,
        component=Component.SALE,
    ),
    _f(
        "freight_forward",
        "Freight deducted",
        "Forward freight deducted from the remittance",
        FieldType.MONEY,
        component=Component.FREIGHT_FORWARD,
        deduction=True,
    ),
    _f(
        "cod_fee",
        "COD fee",
        "Cash-handling fee deducted",
        FieldType.MONEY,
        component=Component.COD_FEE,
        deduction=True,
    ),
    _f("net", "Net remitted", "Amount remitted for the shipment", FieldType.MONEY),
    _f("billed_weight", "Billed weight", "Weight the courier charged for", FieldType.WEIGHT),
    _f("zone", "Zone", "Zone the courier charged for", FieldType.TEXT),
    _f("reference", "UTR", "Bank reference of the remittance", FieldType.TEXT),
)

_FREIGHT_INVOICE = (
    _f("awb", "AWB", "Courier tracking number", FieldType.ID, True),
    _f("order_ref", "Order ID", "Order ID, if the courier reports it", FieldType.ID),
    _f("txn_date", "Invoice date", "Date of the freight invoice", FieldType.DATE, True),
    _f("reference", "Invoice number", "Freight invoice number", FieldType.TEXT),
    _f(
        "charge_type",
        "Leg",
        "Forward shipment or return (RTO)",
        FieldType.ENUM,
        True,
        choices=ChargeType,
    ),
    _f("zone", "Zone", "Zone the courier charged for", FieldType.TEXT),
    _f("billed_weight", "Billed weight", "Weight the courier charged for", FieldType.WEIGHT),
    _f("amount", "Freight", "Freight charged for the leg", FieldType.MONEY, True, deduction=True),
)

_RETURN_SCANS = (
    _f("awb", "AWB", "Tracking number on the returned parcel", FieldType.ID, key_group="key"),
    _f("order_ref", "Order ID", "Order ID on the returned parcel", FieldType.ID, key_group="key"),
    _f(
        "scanned_at",
        "Scanned at",
        "When the warehouse received the return",
        FieldType.DATETIME,
        True,
    ),
    _f("condition", "Condition", "Condition noted at receipt", FieldType.TEXT),
)

FIELDS: dict[FileKind, tuple[TargetField, ...]] = {
    FileKind.ORDERS: _ORDERS,
    FileKind.SETTLEMENT: _SETTLEMENT,
    FileKind.COD_REMITTANCE: _COD_REMITTANCE,
    FileKind.FREIGHT_INVOICE: _FREIGHT_INVOICE,
    FileKind.RETURN_SCANS: _RETURN_SCANS,
}


def field(kind: FileKind, name: str) -> TargetField:
    return next(f for f in FIELDS[kind] if f.name == name)
