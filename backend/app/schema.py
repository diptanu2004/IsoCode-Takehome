"""Database tables (TDD §6). Money is always integer paise in BIGINT columns."""

from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from app.enums import (
    ClaimStatus,
    ClaimType,
    Component,
    DeliveryStatus,
    FileKind,
    MappingSource,
    PartnerKind,
    PaymentMode,
    ReconStatus,
    Role,
    UploadStatus,
)

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


# IDs compare byte by byte: faster than locale-aware comparison, and IDs have no locale.
ID_TEXT = Text(collation="C")


def _id() -> Column:
    return Column("id", BigInteger, Identity(), primary_key=True)


def _created_at() -> Column:
    return Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now())


def _one_of(column: str, values: type[StrEnum], nullable: bool = False) -> CheckConstraint:
    allowed = ", ".join(f"'{v.value}'" for v in values)
    condition = f"{column} IN ({allowed})"
    if nullable:
        condition = f"{column} IS NULL OR {condition}"
    return CheckConstraint(condition, name=f"{column}_valid")


# --- People and access -------------------------------------------------------

users = Table(
    "users",
    metadata,
    _id(),
    Column("email", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    _created_at(),
    _one_of("role", Role),
    CheckConstraint("email = lower(email)", name="email_lowercase"),
)

sessions = Table(
    "sessions",
    metadata,
    _id(),
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("token_hash", Text, nullable=False, unique=True),
    _created_at(),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

login_attempts = Table(
    "login_attempts",
    metadata,
    _id(),
    Column("email", Text, nullable=False),
    Column("ip", Text, nullable=False),
    Column("succeeded", Boolean, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index(None, "email", "ip", "at"),
)

audit_log = Table(
    "audit_log",
    metadata,
    _id(),
    Column("actor_id", BigInteger, ForeignKey("users.id")),  # null = the system
    Column("action", Text, nullable=False),
    Column("entity", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("before", JSONB),
    Column("after", JSONB),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index(None, "entity", "entity_id"),
)

app_meta = Table(
    "app_meta",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", JSONB, nullable=False),
)

# --- Partners, sources and mappings ------------------------------------------

partners = Table(
    "partners",
    metadata,
    _id(),
    Column("name", Text, nullable=False, unique=True),
    Column("kind", Text, nullable=False),
    Column("settlement_cycle_days", Integer, nullable=False, server_default=text("7")),
    Column("tolerance_paise", Integer, nullable=False, server_default=text("1000")),
    Column("return_window_days", Integer, nullable=False, server_default=text("3")),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    _one_of("kind", PartnerKind),
    CheckConstraint("tolerance_paise >= 0", name="tolerance_non_negative"),
)

sources = Table(
    "sources",
    metadata,
    _id(),
    Column("partner_id", BigInteger, ForeignKey("partners.id"), nullable=False),
    Column("file_kind", Text, nullable=False),
    Column("name", Text, nullable=False, unique=True),
    UniqueConstraint("partner_id", "file_kind"),
    _one_of("file_kind", FileKind),
)

mapping_versions = Table(
    "mapping_versions",
    metadata,
    _id(),
    Column("source_id", BigInteger, ForeignKey("sources.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("header_fingerprint", Text, nullable=False),
    Column("mapping", JSONB, nullable=False),
    Column("personal_data_columns", ARRAY(Text), nullable=False, server_default="{}"),
    Column("suggested_by", Text, nullable=False),
    Column("approved_by", BigInteger, ForeignKey("users.id")),
    Column("approved_at", DateTime(timezone=True)),
    Column("is_active", Boolean, nullable=False, server_default=text("false")),
    _created_at(),
    UniqueConstraint("source_id", "version"),
    _one_of("suggested_by", MappingSource),
    # At most one active mapping per source.
    Index(
        "uq_mapping_versions_active",
        "source_id",
        unique=True,
        postgresql_where=text("is_active"),
    ),
)

# --- Uploads and the rows they carry -----------------------------------------

uploads = Table(
    "uploads",
    metadata,
    _id(),
    Column("source_id", BigInteger, ForeignKey("sources.id"), nullable=False),
    Column("mapping_version_id", BigInteger, ForeignKey("mapping_versions.id")),
    Column("filename", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("row_count", Integer),
    Column("error_count", Integer),
    Column("status", Text, nullable=False, server_default=UploadStatus.QUEUED.value),
    Column("error_message", Text),
    Column("stage_timings", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    # Counts from the load: rows loaded, lines created, lines matched to an order, duplicates.
    Column("stats", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    # Header row found in the file; used to suggest a mapping when the format is new.
    Column("detected", JSONB),
    Column("uploaded_by", BigInteger, ForeignKey("users.id")),
    _created_at(),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    _one_of("status", UploadStatus),
    Index(None, "status", "created_at"),
    # The same file can't be loaded twice; a failed or voided upload can be retried.
    Index(
        "uq_uploads_sha256_live",
        "sha256",
        unique=True,
        postgresql_where=text("status NOT IN ('failed', 'voided')"),
    ),
)

# The three tables below take bulk loads of up to a few lakh rows per file. They have no foreign
# keys: Postgres checks a foreign key with a trigger per row, which made a load 4x slower. The
# loader (app/ingest/pipeline.py) is their only writer and takes every ID it stores from the
# database, and tests/test_pipeline.py checks that no line points at a missing order.

source_rows = Table(
    "source_rows",
    metadata,
    Column("upload_id", BigInteger, nullable=False),
    Column("row_no", Integer, nullable=False),
    Column("raw", JSONB, nullable=False),  # original cells, personal-data columns removed
    Column("error", Text),
    PrimaryKeyConstraint("upload_id", "row_no"),
)

orders = Table(
    "orders",
    metadata,
    _id(),
    Column("order_id", ID_TEXT, nullable=False, unique=True),
    Column("channel_partner_id", BigInteger, ForeignKey("partners.id"), nullable=False),
    Column("courier_partner_id", BigInteger, ForeignKey("partners.id")),
    Column("awb", ID_TEXT),
    Column("zone", Text),
    Column("order_date", Date, nullable=False),
    Column("value_paise", BigInteger, nullable=False),
    Column("units", Integer, nullable=False, server_default=text("1")),
    Column("payment_mode", Text, nullable=False),
    Column("declared_weight_g", Integer),
    Column("delivery_status", Text, nullable=False),
    Column("delivered_at", Date),
    Column("returned_at", Date),
    Column("upload_id", BigInteger, ForeignKey("uploads.id")),
    Column("row_no", Integer),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    _one_of("payment_mode", PaymentMode),
    _one_of("delivery_status", DeliveryStatus),
    Index(None, "awb"),
    Index(None, "channel_partner_id", "order_date"),
)

partner_lines = Table(
    "partner_lines",
    metadata,
    _id(),
    Column("upload_id", BigInteger, nullable=False),
    Column("row_no", Integer, nullable=False),
    Column("partner_id", BigInteger, nullable=False),
    Column("order_ref", ID_TEXT),  # order ID as normalised from the partner's file
    Column("awb", ID_TEXT),
    Column("order_id", BigInteger),  # set once matched to an order
    Column("component", Text, nullable=False),
    Column("amount_paise", BigInteger, nullable=False),
    Column("txn_date", Date, nullable=False),
    Column("reference", Text),  # UTR, invoice number, payout ID
    Column("billed_weight_g", Integer),
    Column("zone", Text),
    # Hash of the line's natural key, so re-uploading overlapping exports never double-counts.
    Column("line_key", UUID, nullable=False, unique=True),
    _one_of("component", Component),
    Index(None, "order_id"),
    Index(None, "upload_id"),
    # Only unmatched lines are looked up by their keys, so only they are indexed.
    Index(
        "ix_partner_lines_unmatched_order_ref",
        "order_ref",
        postgresql_where=text("order_id IS NULL"),
    ),
    Index("ix_partner_lines_unmatched_awb", "awb", postgresql_where=text("order_id IS NULL")),
)

return_scans = Table(
    "return_scans",
    metadata,
    _id(),
    Column("upload_id", BigInteger, nullable=False),
    Column("row_no", Integer, nullable=False),
    Column("awb", ID_TEXT),
    Column("order_ref", ID_TEXT),
    Column("order_id", BigInteger),
    Column("scanned_at", DateTime(timezone=True), nullable=False),
    Column("condition", Text),
    Column("line_key", UUID, nullable=False, unique=True),
    CheckConstraint("awb IS NOT NULL OR order_ref IS NOT NULL", name="has_key"),
    Index(None, "order_id"),
    Index(None, "upload_id"),
    Index(
        "ix_return_scans_unmatched_order_ref",
        "order_ref",
        postgresql_where=text("order_id IS NULL"),
    ),
    Index("ix_return_scans_unmatched_awb", "awb", postgresql_where=text("order_id IS NULL")),
)

# --- Expected amounts and results --------------------------------------------

rate_cards = Table(
    "rate_cards",
    metadata,
    _id(),
    Column("partner_id", BigInteger, ForeignKey("partners.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("effective_from", Date, nullable=False),
    Column("effective_to", Date),  # null = still in force
    Column("rules", JSONB, nullable=False),
    Column("note", Text),
    Column("created_by", BigInteger, ForeignKey("users.id")),
    _created_at(),
    UniqueConstraint("partner_id", "version"),
    CheckConstraint(
        "effective_to IS NULL OR effective_to >= effective_from", name="effective_range"
    ),
)

# Rewritten in bulk by the matching engine, its only writer; no foreign keys, as for partner_lines.
order_recon = Table(
    "order_recon",
    metadata,
    Column("order_id", BigInteger, primary_key=True),
    Column("expected_paise", BigInteger, nullable=False),
    Column("received_paise", BigInteger, nullable=False),
    Column("diff_paise", BigInteger, nullable=False),
    Column("tcs_paise", BigInteger, nullable=False, server_default=text("0")),
    Column("tds_paise", BigInteger, nullable=False, server_default=text("0")),
    Column("status", Text, nullable=False),
    Column("issues", ARRAY(Text), nullable=False, server_default="{}"),
    Column("due_date", Date),
    Column("breakdown", JSONB, nullable=False),
    Column("channel_rate_card_id", BigInteger),
    Column("courier_rate_card_id", BigInteger),
    Column("auto_matched", Boolean, nullable=False, server_default=text("true")),
    Column("computed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    _one_of("status", ReconStatus),
    Index(None, "status"),
)

# Gaps between ₹1 and the tolerance, per component: no claim each, but checked for a pattern
# (TDD §8.5). Rewritten by the matching engine.
small_gaps = Table(
    "small_gaps",
    metadata,
    Column("order_id", BigInteger, nullable=False),
    Column("component", Text, nullable=False),
    Column("partner_id", BigInteger, nullable=False),
    Column("month", Date, nullable=False),
    Column("gap_paise", BigInteger, nullable=False),
    PrimaryKeyConstraint("order_id", "component"),
    Index(None, "partner_id", "component", "month"),
)

# --- Claims ------------------------------------------------------------------

claims = Table(
    "claims",
    metadata,
    _id(),
    Column("partner_id", BigInteger, ForeignKey("partners.id"), nullable=False),
    Column("order_id", BigInteger, ForeignKey("orders.id")),  # null for a systematic-gap claim
    Column("type", Text, nullable=False),
    Column("amount_paise", BigInteger, nullable=False),
    Column("status", Text, nullable=False, server_default=ClaimStatus.OPEN.value),
    Column("recovered_paise", BigInteger, nullable=False, server_default=text("0")),
    Column("partner_ref", Text),
    Column("notes", Text),
    # Systematic-gap claims cover one component of one partner for one month.
    Column("component", Text),
    Column("period_month", Date),
    Column("aging_from", Date, nullable=False),
    Column("opened_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("closed_at", DateTime(timezone=True)),
    Column("closed_by", BigInteger, ForeignKey("users.id")),
    _one_of("type", ClaimType),
    _one_of("status", ClaimStatus),
    _one_of("component", Component, nullable=True),
    CheckConstraint("amount_paise > 0", name="amount_positive"),
    Index(None, "partner_id", "status"),
    Index(None, "order_id"),
    # At most one live claim per order and problem, and per partner, component and month.
    Index(
        "uq_claims_live_order",
        "order_id",
        "type",
        unique=True,
        postgresql_where=text("status IN ('open', 'raised', 'partner_rejected')"),
    ),
    Index(
        "uq_claims_live_systematic",
        "partner_id",
        "component",
        "period_month",
        unique=True,
        postgresql_where=text(
            "type = 'systematic_gap' AND status IN ('open', 'raised', 'partner_rejected')"
        ),
    ),
)

# The orders behind a systematic-gap claim (an ordinary claim names its order directly).
claim_orders = Table(
    "claim_orders",
    metadata,
    Column("claim_id", BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
    Column("order_id", BigInteger, ForeignKey("orders.id"), nullable=False),
    Column("amount_paise", BigInteger, nullable=False),
    PrimaryKeyConstraint("claim_id", "order_id"),
    Index(None, "order_id"),
)

claim_events = Table(
    "claim_events",
    metadata,
    _id(),
    Column("claim_id", BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
    Column("actor_id", BigInteger, ForeignKey("users.id")),  # null = the system
    Column("from_status", Text),
    Column("to_status", Text, nullable=False),
    Column("note", Text),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index(None, "claim_id"),
)
