"""Company A's partners, their rate cards and the shape of each partner's report.

Everything here is synthetic (TDD [A20]). The generator writes files in these formats and the
seed script saves a mapping for each, so the two can't drift apart.
"""

from dataclasses import dataclass, field

from app.enums import FileKind, PartnerKind

# --- Partners ----------------------------------------------------------------


@dataclass(frozen=True)
class Partner:
    name: str
    kind: PartnerKind
    settlement_cycle_days: int = 7  # [A12]
    tolerance_paise: int = 1000  # ₹10, from the brief
    return_window_days: int = 3  # [A10]


WEBSITE = "Website"
M1, M2, M3 = "Marketplace 1", "Marketplace 2", "Marketplace 3"
QC1, QC2 = "Quick-commerce 1", "Quick-commerce 2"
COURIER_A, COURIER_B = "Courier A", "Courier B"
ORDER_SYSTEM, WAREHOUSE = "Order system", "Warehouse"

PARTNERS = (
    Partner(WEBSITE, PartnerKind.STOREFRONT),
    Partner(M1, PartnerKind.MARKETPLACE, settlement_cycle_days=7),
    Partner(M2, PartnerKind.MARKETPLACE, settlement_cycle_days=10),
    Partner(M3, PartnerKind.MARKETPLACE, settlement_cycle_days=15),
    Partner(QC1, PartnerKind.QUICK_COMMERCE, settlement_cycle_days=7),
    Partner(QC2, PartnerKind.QUICK_COMMERCE, settlement_cycle_days=7),
    Partner(COURIER_A, PartnerKind.COURIER, settlement_cycle_days=7),
    Partner(COURIER_B, PartnerKind.COURIER, settlement_cycle_days=9),
    Partner(ORDER_SYSTEM, PartnerKind.INTERNAL),
    Partner(WAREHOUSE, PartnerKind.INTERNAL),
)
PARTNER_BY_NAME = {p.name: p for p in PARTNERS}

# --- Rate cards (rupees in the rules; the engine works in paise) ---------------


def _marketplace(commission_pct, fixed_fee, shipping=None, rounding="paise") -> dict:
    return {
        "type": "marketplace",
        "commission_pct": commission_pct,
        "commission_rounding": rounding,
        "fixed_fee": fixed_fee,
        "shipping": shipping,  # {"slab_g", "first", "additional"} or None
        "on_return": {  # [A5]
            "refund_commission": True,
            "refund_fixed_fee": True,
            "return_shipping_pct_of_forward": 100,
        },
    }


def _courier(forward: dict[str, tuple[int, int]], cod_pct: float, cod_min: int) -> dict:
    return {
        "type": "courier",
        "slab_g": 500,  # [A8]
        "forward": {zone: {"first": f, "additional": a} for zone, (f, a) in forward.items()},
        "return_pct_of_forward": 100,
        "cod_fee": {"pct": cod_pct, "min": cod_min},
    }


RATE_CARDS: dict[str, dict] = {
    M1: _marketplace(15.0, 30, {"slab_g": 500, "first": 90, "additional": 40}, rounding="rupee"),
    M2: _marketplace(18.0, 20, {"slab_g": 500, "first": 70, "additional": 35}),
    M3: _marketplace(12.0, 25, {"slab_g": 500, "first": 80, "additional": 40}),
    QC1: _marketplace(22.0, 15),
    QC2: _marketplace(20.0, 10),
    COURIER_A: _courier(
        {"A": (30, 15), "B": (38, 18), "C": (48, 23), "D": (60, 28), "E": (72, 34)}, 2.0, 30
    ),
    COURIER_B: _courier(
        {"A": (32, 16), "B": (40, 19), "C": (48, 23), "D": (58, 27), "E": (70, 33)}, 1.8, 28
    ),
}

# --- File formats ------------------------------------------------------------


@dataclass(frozen=True)
class Col:
    """One column of a partner file.

    `key` is where the generator reads the value; `field` is the target field the saved mapping
    assigns it to (None = not imported). The other options become part of that mapping.
    """

    header: str
    key: str
    field: str | None = None
    personal: bool = False
    negate: bool = False  # the partner shows a deduction as a positive number
    date_format: str | None = None
    scale: int | None = None  # file unit → stored unit, e.g. kg → g = 1000
    values: dict[str, str] | None = None  # file text → stored value
    strip_suffix: str | None = None  # regex removed from the end of an ID


def mapped(header: str, name: str, **options) -> Col:
    return Col(header, key=name, field=name, **options)


def extra(header: str, key: str, personal: bool = False) -> Col:
    return Col(header, key=key, personal=personal)


@dataclass(frozen=True)
class FileFormat:
    partner: str
    file_kind: FileKind
    filename: str
    columns: tuple[Col, ...]
    title_rows: tuple[str, ...] = ()  # lines above the header
    money: str = "plain"  # "plain" 1199.00 | "rupee" ₹1,199.00 with (123.00) for negatives
    excel: bool = False
    id_as_number: tuple[str, ...] = field(default=())  # Excel number cells, as partners export

    @property
    def source_name(self) -> str:
        return source_name(self.partner, self.file_kind)

    @property
    def header_row(self) -> int:
        return len(self.title_rows) + 1


KIND_LABELS = {
    FileKind.ORDERS: "Orders",
    FileKind.SETTLEMENT: "Settlement",
    FileKind.COD_REMITTANCE: "COD remittance",
    FileKind.FREIGHT_INVOICE: "Freight invoice",
    FileKind.RETURN_SCANS: "Return scans",
}


def source_name(partner: str, kind: FileKind) -> str:
    return f"{partner} · {KIND_LABELS[kind]}"


STATUS_LABELS = {
    "pending": "Pending",
    "in_transit": "In Transit",
    "delivered": "Delivered",
    "return_in_transit": "RTO In Transit",
    "returned_to_warehouse": "Returned to warehouse",
    "cancelled": "Cancelled",
}
DMY = "%d/%m/%Y"
ISO = "%Y-%m-%d"

ORDERS_FORMAT = FileFormat(
    ORDER_SYSTEM,
    FileKind.ORDERS,
    "order_system_orders.csv",
    (
        mapped("Order No", "order_id"),
        mapped("Channel", "channel"),
        mapped("Order Date", "order_date", date_format=DMY),
        extra("Customer Name", "customer_name", personal=True),
        extra("Phone", "phone", personal=True),
        extra("Shipping Address", "address", personal=True),
        extra("Pincode", "pincode"),
        mapped("Payment Method", "payment_mode", values={"COD": "cod", "Prepaid": "prepaid"}),
        mapped("Order Value", "value"),
        mapped("Units", "units"),
        mapped("Pkg Weight (g)", "declared_weight"),
        mapped("Courier", "courier"),
        mapped("AWB", "awb"),
        mapped("Zone", "zone"),
        mapped("Status", "delivery_status", values={v: k for k, v in STATUS_LABELS.items()}),
        mapped("Delivered On", "delivered_at", date_format=DMY),
        mapped("Returned On", "returned_at", date_format=DMY),
    ),
)

_SETTLEMENT_FORMATS = (
    FileFormat(
        M1,
        FileKind.SETTLEMENT,
        "marketplace1_settlement.csv",
        (
            mapped("Order ID", "order_ref"),
            mapped("Settlement Date", "txn_date", date_format=ISO),
            mapped("Sale Amount", "sale"),
            mapped("Commission", "commission", negate=True),
            mapped("Closing Fee", "fixed_fee", negate=True),
            mapped("Shipping Fee", "shipping_fee", negate=True),
            mapped("Reverse Shipping Fee", "return_shipping_fee", negate=True),
            mapped("TCS", "tcs", negate=True),
            mapped("TDS", "tds", negate=True),
            mapped("Other Adjustments", "adjustment"),
            mapped("Net Payable", "net"),
            mapped("UTR", "reference"),
        ),
    ),
    FileFormat(
        M2,
        FileKind.SETTLEMENT,
        "marketplace2_settlement.xlsx",
        (
            mapped("Order Item ID", "order_ref"),
            mapped("Payout Date", "txn_date"),
            mapped("Item Price", "sale"),
            mapped("Referral Fee", "commission"),
            mapped("Fixed Closing Fee", "fixed_fee"),
            mapped("Logistics Fee", "shipping_fee"),
            mapped("Return Logistics Fee", "return_shipping_fee"),
            mapped("TCS (GST)", "tcs"),
            mapped("TDS (194-O)", "tds"),
            mapped("Adjustment", "adjustment"),
            mapped("Settlement Value", "net"),
            mapped("Payout Ref", "reference"),
        ),
        title_rows=("Marketplace 2 Seller Payout Report", "{period}", ""),
        excel=True,
        id_as_number=("order_ref",),
    ),
    FileFormat(
        M3,
        FileKind.SETTLEMENT,
        "marketplace3_settlement.csv",
        (
            mapped("Sub Order No", "order_ref", strip_suffix=r"-\d+$"),
            mapped("Paid On", "txn_date", date_format=DMY),
            mapped("Selling Price", "sale"),
            mapped("Marketplace Commission", "commission"),
            mapped("Fixed Fee", "fixed_fee"),
            mapped("Shipping Charges", "shipping_fee"),
            mapped("Return Shipping Charges", "return_shipping_fee"),
            mapped("TCS Deducted", "tcs"),
            mapped("TDS Deducted", "tds"),
            mapped("Deductions/Adjustments", "adjustment"),
            mapped("Final Settlement", "net"),
            mapped("Transaction ID", "reference"),
        ),
        money="rupee",
    ),
    FileFormat(
        QC1,
        FileKind.SETTLEMENT,
        "quickcommerce1_settlement.csv",
        (
            mapped("Order Ref", "order_ref"),
            mapped("Payout Cycle End", "txn_date", date_format=DMY),
            mapped("GMV", "sale"),
            mapped("Platform Commission", "commission", negate=True),
            mapped("Fixed Fee", "fixed_fee", negate=True),
            mapped("GST TCS", "tcs", negate=True),
            mapped("IT TDS", "tds", negate=True),
            mapped("Payout", "net"),
            mapped("Payout ID", "reference"),
        ),
    ),
    FileFormat(
        QC2,
        FileKind.SETTLEMENT,
        "quickcommerce2_settlement.csv",
        (
            mapped("order_id", "order_ref"),
            mapped("payout_date", "txn_date", date_format=ISO),
            mapped("order_value", "sale"),
            mapped("commission", "commission"),
            mapped("platform_fee", "fixed_fee"),
            mapped("tcs", "tcs"),
            mapped("tds", "tds"),
            mapped("adjustments", "adjustment"),
            mapped("net_payout", "net"),
            mapped("ref", "reference"),
        ),
    ),
)

_COURIER_FORMATS = (
    FileFormat(
        COURIER_A,
        FileKind.COD_REMITTANCE,
        "couriera_cod_remittance.csv",
        (
            mapped("AWB No", "awb"),
            mapped("Order Ref", "order_ref"),
            extra("Delivered On", "delivered_on"),
            mapped("COD Amount", "sale"),
            mapped("Freight Deducted", "freight_forward", negate=True),
            mapped("COD Charges", "cod_fee", negate=True),
            mapped("Net Remitted", "net"),
            mapped("Remittance Date", "txn_date", date_format=DMY),
            mapped("UTR", "reference"),
            mapped("Charged Weight (kg)", "billed_weight", scale=1000),
            mapped("Zone", "zone"),
        ),
    ),
    FileFormat(
        COURIER_B,
        FileKind.COD_REMITTANCE,
        "courierb_cod_remittance.xlsx",
        (
            mapped("Waybill", "awb"),
            mapped("Client Order ID", "order_ref"),
            mapped("COD Collected", "sale"),
            mapped("Shipping Charge", "freight_forward"),
            mapped("COD Fee", "cod_fee"),
            mapped("Amount Remitted", "net"),
            mapped("Remit Date", "txn_date"),
            mapped("Bank UTR", "reference"),
            mapped("Billed Wt (g)", "billed_weight"),
            mapped("Zone", "zone"),
        ),
        excel=True,
        id_as_number=("awb",),
    ),
    FileFormat(
        COURIER_A,
        FileKind.FREIGHT_INVOICE,
        "couriera_freight_invoice.csv",
        (
            mapped("Invoice No", "reference"),
            mapped("Invoice Date", "txn_date", date_format=DMY),
            mapped("AWB", "awb"),
            mapped("Order No", "order_ref"),
            mapped("Shipment Type", "charge_type", values={"Forward": "forward", "RTO": "return"}),
            mapped("Zone", "zone"),
            mapped("Charged Weight (kg)", "billed_weight", scale=1000),
            mapped("Freight (Rs)", "amount", negate=True),
        ),
    ),
    FileFormat(
        COURIER_B,
        FileKind.FREIGHT_INVOICE,
        "courierb_freight_invoice.csv",
        (
            mapped("inv_no", "reference"),
            mapped("inv_date", "txn_date", date_format=ISO),
            mapped("waybill", "awb"),
            mapped("order_ref", "order_ref"),
            mapped("leg", "charge_type", values={"FWD": "forward", "RTN": "return"}),
            mapped("zone", "zone"),
            mapped("billed_weight_g", "billed_weight"),
            mapped("amount", "amount", negate=True),
        ),
    ),
)

RETURN_SCANS_FORMAT = FileFormat(
    WAREHOUSE,
    FileKind.RETURN_SCANS,
    "warehouse_return_scans.csv",
    (
        mapped("Scan Time", "scanned_at", date_format="%Y-%m-%d %H:%M"),
        mapped("AWB", "awb"),
        mapped("Order No", "order_ref"),
        mapped("Condition", "condition"),
        extra("Scanned By", "scanned_by"),
        extra("Bin", "bin"),
    ),
)

SEED_FORMATS: tuple[FileFormat, ...] = (
    ORDERS_FORMAT,
    *_SETTLEMENT_FORMATS,
    *_COURIER_FORMATS,
    RETURN_SCANS_FORMAT,
)

# Marketplace 1 changed its report without notice: renamed and reordered columns, a title block,
# an extra column and a new date format. Used to demo the AI mapping flow (TDD §9).
M1_NEW_FORMAT = FileFormat(
    M1,
    FileKind.SETTLEMENT,
    "marketplace1_settlement_new_format.csv",
    (
        mapped("Payment Date", "txn_date", date_format="%d-%b-%Y"),
        mapped("Order Number", "order_ref"),
        mapped("Gross Sale (INR)", "sale"),
        mapped("Fee: Commission", "commission", negate=True),
        mapped("Fee: Closing", "fixed_fee", negate=True),
        mapped("Fee: Shipping", "shipping_fee", negate=True),
        mapped("Fee: Reverse Shipping", "return_shipping_fee", negate=True),
        mapped("GST TCS", "tcs", negate=True),
        mapped("Income Tax TDS", "tds", negate=True),
        mapped("Adjustments", "adjustment"),
        mapped("Amount Paid", "net"),
        mapped("Bank Reference", "reference"),
        extra("Settlement Batch", "batch"),
    ),
    title_rows=("Marketplace 1 - Payment Report", ""),
)


def saved_mapping(fmt: FileFormat) -> dict:
    """The mapping a person would have approved for this format (stored in mapping_versions)."""
    fields: dict[str, dict] = {}
    for col in fmt.columns:
        if col.field is None:
            continue
        spec: dict = {"column": col.header}
        for option in ("negate", "date_format", "scale", "values", "strip_suffix"):
            if value := getattr(col, option):
                spec[option] = value
        fields[col.field] = spec
    return {"header_row": fmt.header_row, "sheet": None, "fields": fields}


def personal_columns(fmt: FileFormat) -> list[str]:
    return [col.header for col in fmt.columns if col.personal]
