"""faster bulk loads

Measured on the 60k-order seed: inserting 1.1 lakh ledger lines took 13 s, of which ~10 s were
per-row foreign-key triggers and most of the rest the unique index on a text hash compared with
locale rules. This drops the foreign keys on the bulk-loaded tables (the loader is their only
writer), stores line keys as uuid, compares ID columns byte by byte, and indexes the lookup keys
only for lines that are still unmatched.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03 19:29:00.476205

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID_COLUMNS = [
    ("orders", "order_id"),
    ("orders", "awb"),
    ("partner_lines", "order_ref"),
    ("partner_lines", "awb"),
    ("return_scans", "order_ref"),
    ("return_scans", "awb"),
]
_FOREIGN_KEYS = [
    ("fk_partner_lines_upload_id_uploads", "partner_lines", "uploads", "upload_id", "CASCADE"),
    ("fk_partner_lines_partner_id_partners", "partner_lines", "partners", "partner_id", None),
    ("fk_partner_lines_order_id_orders", "partner_lines", "orders", "order_id", None),
    ("fk_return_scans_order_id_orders", "return_scans", "orders", "order_id", None),
    ("fk_return_scans_upload_id_uploads", "return_scans", "uploads", "upload_id", "CASCADE"),
    ("fk_source_rows_upload_id_uploads", "source_rows", "uploads", "upload_id", "CASCADE"),
]
_UNMATCHED = "order_id IS NULL"


def upgrade() -> None:
    for name, table, *_ in _FOREIGN_KEYS:
        op.drop_constraint(name, table, type_="foreignkey")

    for table, column in _ID_COLUMNS:
        op.execute(f'ALTER TABLE {table} ALTER COLUMN {column} TYPE text COLLATE "C"')
    for table in ("partner_lines", "return_scans"):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN line_key TYPE uuid USING line_key::uuid")

    op.drop_index("ix_partner_lines_awb", table_name="partner_lines")
    op.drop_index("ix_partner_lines_order_ref", table_name="partner_lines")
    for table in ("partner_lines", "return_scans"):
        for column in ("order_ref", "awb"):
            op.create_index(
                f"ix_{table}_unmatched_{column}",
                table,
                [column],
                postgresql_where=sa.text(_UNMATCHED),
            )
    op.create_index("ix_return_scans_upload_id", "return_scans", ["upload_id"])


def downgrade() -> None:
    op.drop_index("ix_return_scans_upload_id", table_name="return_scans")
    for table in ("partner_lines", "return_scans"):
        for column in ("order_ref", "awb"):
            op.drop_index(f"ix_{table}_unmatched_{column}", table_name=table)
    op.create_index("ix_partner_lines_order_ref", "partner_lines", ["order_ref"])
    op.create_index("ix_partner_lines_awb", "partner_lines", ["awb"])

    for table in ("partner_lines", "return_scans"):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN line_key TYPE text USING line_key::text")
    for table, column in _ID_COLUMNS:
        op.execute(f'ALTER TABLE {table} ALTER COLUMN {column} TYPE text COLLATE "default"')

    for name, table, target, column, ondelete in _FOREIGN_KEYS:
        op.create_foreign_key(name, table, target, [column], ["id"], ondelete=ondelete)
