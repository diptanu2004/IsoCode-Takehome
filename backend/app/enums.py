"""Closed sets of values shared by the schema, the API and the matching engine."""

from enum import StrEnum


class Role(StrEnum):
    EXEC = "exec"
    COFOUNDER = "cofounder"


class PartnerKind(StrEnum):
    STOREFRONT = "storefront"
    MARKETPLACE = "marketplace"
    QUICK_COMMERCE = "quick_commerce"
    COURIER = "courier"
    INTERNAL = "internal"  # Company A's own systems: order export, warehouse


class FileKind(StrEnum):
    ORDERS = "orders"
    SETTLEMENT = "settlement"
    COD_REMITTANCE = "cod_remittance"
    FREIGHT_INVOICE = "freight_invoice"
    RETURN_SCANS = "return_scans"


class UploadStatus(StrEnum):
    QUEUED = "queued"
    NEEDS_MAPPING = "needs_mapping"
    LOADING = "loading"
    MATCHING = "matching"
    DONE = "done"
    FAILED = "failed"
    VOIDED = "voided"


class MappingSource(StrEnum):
    SEED = "seed"
    RULES = "rules"
    AI = "ai"
    MANUAL = "manual"


class PaymentMode(StrEnum):
    PREPAID = "prepaid"
    COD = "cod"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    RETURN_IN_TRANSIT = "return_in_transit"
    RETURNED_TO_WAREHOUSE = "returned_to_warehouse"
    CANCELLED = "cancelled"


class Component(StrEnum):
    """One money component of an order's ledger (TDD §8.1). Positive = money to Company A."""

    SALE = "sale"
    COMMISSION = "commission"
    FIXED_FEE = "fixed_fee"
    SHIPPING_FEE = "shipping_fee"
    RETURN_SHIPPING_FEE = "return_shipping_fee"
    FREIGHT_FORWARD = "freight_forward"
    FREIGHT_RETURN = "freight_return"
    COD_FEE = "cod_fee"
    TCS = "tcs"
    TDS = "tds"
    ADJUSTMENT = "adjustment"


class ReconStatus(StrEnum):
    """Listed from most to least severe; an order shows its most severe open issue."""

    RETURN_NOT_RECEIVED = "return_not_received"
    UNPAID_PAST_DUE = "unpaid_past_due"
    SHORT_PAID = "short_paid"
    FREIGHT_OVERCHARGED = "freight_overcharged"
    OVERPAID = "overpaid"
    PENDING = "pending"
    WITHIN_TOLERANCE = "within_tolerance"
    SETTLED = "settled"
    NOT_APPLICABLE = "not_applicable"


class ClaimType(StrEnum):
    RETURN_NOT_RECEIVED = "return_not_received"
    UNPAID_PAST_DUE = "unpaid_past_due"
    SHORT_PAID = "short_paid"
    FREIGHT_OVERCHARGED = "freight_overcharged"
    SYSTEMATIC_GAP = "systematic_gap"


class ClaimStatus(StrEnum):
    OPEN = "open"
    RAISED = "raised"
    PARTNER_REJECTED = "partner_rejected"
    RECOVERED = "recovered"
    WRITTEN_OFF = "written_off"
    AUTO_RESOLVED = "auto_resolved"
