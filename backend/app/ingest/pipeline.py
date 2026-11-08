"""Load one uploaded file: read, check the format, map and clean, bulk-load, link to orders.

See TDD §7.1.

Rows go to Postgres with COPY into temporary staging tables, then one INSERT … SELECT per target
table, so a 1 lakh-row file never makes 1 lakh round trips. The whole load is one transaction:
it lands completely or not at all.
"""

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
from sqlalchemy import Connection, insert, select, text, update

from app.config import get_settings
from app.db import get_engine
from app.enums import Component, FileKind, UploadStatus
from app.ingest.apply import ERROR, ROW_NO, MappingError, apply_mapping, drop_empty_rows
from app.ingest.reader import FileReadError, RawTable, read_table
from app.mapping.fields import FIELDS
from app.mapping.fingerprint import header_fingerprint, normalise_header
from app.recon.engine import LOCAL_TZ, match_upload
from app.schema import mapping_versions, partners, sources, uploads

log = logging.getLogger(__name__)

MAX_ERROR_SHARE = 0.05  # more failed rows than this usually means a wrong mapping [A3]


class UploadRejected(ValueError):
    """The upload can't be accepted as sent. The message is shown to the user."""


@dataclass(frozen=True)
class Job:
    upload_id: int
    source_id: int
    partner_id: int
    file_kind: FileKind
    path: Path


# --- Registering an upload ------------------------------------------------------


def upload_path(upload_id: int, filename: str) -> Path:
    return get_settings().upload_dir / f"{upload_id}{Path(filename).suffix.lower()}"


def register_upload(
    conn: Connection,
    source_id: int,
    filename: str,
    content: bytes,
    user_id: int | None,
    status: UploadStatus = UploadStatus.QUEUED,
) -> int:
    """Store the file and queue it. Refuses a file that was already loaded.

    A caller that will process the file itself passes status=LOADING, so a running worker can't
    pick up the same upload.
    """
    digest = hashlib.sha256(content).hexdigest()
    earlier = conn.execute(
        select(uploads.c.filename, uploads.c.created_at).where(
            uploads.c.sha256 == digest, uploads.c.status.notin_(["failed", "voided"])
        )
    ).first()
    if earlier:
        # The user's calendar date (IST), not UTC: 01:11 IST on the 9th is the 8th in UTC.
        uploaded_on = earlier.created_at.astimezone(LOCAL_TZ)
        raise UploadRejected(
            f"This file was already uploaded as “{earlier.filename}” on "
            f"{uploaded_on:%d %b %Y}. Nothing was loaded twice."
        )
    upload_id = conn.execute(
        insert(uploads)
        .values(
            source_id=source_id,
            filename=filename,
            sha256=digest,
            size_bytes=len(content),
            uploaded_by=user_id,
            status=status.value,
            started_at=datetime.now(UTC) if status != UploadStatus.QUEUED else None,
        )
        .returning(uploads.c.id)
    ).scalar_one()
    path = upload_path(upload_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return upload_id


# --- Status updates (committed straight away so the screen can show progress) -----


def _set(upload_id: int, **values) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(uploads).where(uploads.c.id == upload_id).values(**values))


def _fail(upload_id: int, message: str, timings: dict | None = None) -> None:
    _set(
        upload_id,
        status=UploadStatus.FAILED.value,
        error_message=message,
        finished_at=datetime.now(UTC),
        **({"stage_timings": timings} if timings else {}),
    )


# --- Processing ---------------------------------------------------------------


class _Timer:
    def __init__(self) -> None:
        self.timings: dict[str, float] = {}
        self._start = time.perf_counter()

    def lap(self, stage: str) -> None:
        now = time.perf_counter()
        self.timings[stage] = round(now - self._start, 3)
        self._start = now


def _job(conn: Connection, upload_id: int) -> Job:
    row = conn.execute(
        select(uploads.c.filename, sources.c.id, sources.c.partner_id, sources.c.file_kind)
        .join(sources, sources.c.id == uploads.c.source_id)
        .where(uploads.c.id == upload_id)
    ).one()
    return Job(
        upload_id,
        row.id,
        row.partner_id,
        FileKind(row.file_kind),
        upload_path(upload_id, row.filename),
    )


def process_upload(upload_id: int, as_of: date | None = None) -> str:
    """Run one queued upload to the end. Returns the final status.

    as_of is the date matching treats as today; it only differs from today in seeds and tests.
    """
    timer = _Timer()
    with get_engine().begin() as conn:
        job = _job(conn, upload_id)
        active = conn.execute(
            select(mapping_versions).where(
                mapping_versions.c.source_id == job.source_id, mapping_versions.c.is_active
            )
        ).one_or_none()

    try:
        table = read_table(job.path)
    except FileNotFoundError:
        _fail(upload_id, "The uploaded file is no longer on the server. Upload it again.")
        return UploadStatus.FAILED
    except FileReadError as exc:
        _fail(upload_id, str(exc))
        return UploadStatus.FAILED
    timer.lap("read")

    limit = get_settings().max_upload_rows
    if table.data.height > limit:
        _fail(
            upload_id,
            f"This file has {table.data.height:,} rows; the limit is {limit:,}. Split it and "
            "upload the parts.",
            timer.timings,
        )
        return UploadStatus.FAILED

    fingerprint = header_fingerprint(table.headers)
    detected = {
        "headers": table.headers,
        "header_row": table.header_row,
        "fingerprint": fingerprint,
    }
    if active is None or active.header_fingerprint != fingerprint:
        # A new or changed format: a person maps it before anything is loaded (TDD §9).
        _set(
            upload_id,
            status=UploadStatus.NEEDS_MAPPING.value,
            detected=detected,
            stage_timings=timer.timings,
        )
        return UploadStatus.NEEDS_MAPPING

    try:
        mapped = apply_mapping(table, job.file_kind, active.mapping)
    except MappingError as exc:
        _fail(upload_id, str(exc), timer.timings)
        return UploadStatus.FAILED
    with get_engine().begin() as conn:
        mapped = _check_partners(conn, job.file_kind, mapped)
    timer.lap("map")

    bad = mapped.filter(pl.col(ERROR).is_not_null()).height
    total = mapped.height
    too_many_errors = total > 0 and bad / total > MAX_ERROR_SHARE

    with get_engine().begin() as conn:
        raw = _raw_rows(table, active.personal_data_columns, mapped)
        _copy(conn, "source_rows", raw.with_columns(pl.lit(upload_id).alias("upload_id")))
        timer.lap("store_rows")
        if too_many_errors:
            status, stats = UploadStatus.FAILED, {"rows": total, "rejected": bad}
        else:
            good = mapped.filter(pl.col(ERROR).is_null())
            stats = _LOADERS[job.file_kind](conn, job, good)
            stats |= {"rows": total, "rejected": bad}
            timer.lap("load")
            if job.file_kind == FileKind.ORDERS:
                stats |= _link_to_orders(conn)
            timer.lap("link")
            # Autovacuum can't keep up with a bulk load; matching needs real row counts.
            conn.execute(text("ANALYZE orders, partner_lines, return_scans"))
            _set(upload_id, status=UploadStatus.MATCHING.value)
            stats |= match_upload(conn, upload_id, job.file_kind, job.partner_id, as_of)
            timer.lap("match")
            status = UploadStatus.DONE
        conn.execute(
            update(uploads)
            .where(uploads.c.id == upload_id)
            .values(
                status=status.value,
                mapping_version_id=active.id,
                row_count=total,
                error_count=bad,
                stats=stats,
                detected=detected,
                stage_timings=timer.timings,
                finished_at=datetime.now(UTC),
                error_message=(
                    f"{bad:,} of {total:,} rows couldn't be read ({bad / total:.0%}). That "
                    "usually means a column is mapped wrongly. Download the rejected rows to see "
                    "why, fix the mapping, and upload the file again."
                    if too_many_errors
                    else None
                ),
            )
        )
    job.path.unlink(missing_ok=True)  # data minimisation: the file isn't kept once loaded
    return status


def _check_partners(conn: Connection, kind: FileKind, mapped: pl.DataFrame) -> pl.DataFrame:
    """Orders name their channel and courier as text; resolve them to partners or reject the row."""
    if kind != FileKind.ORDERS:
        return mapped
    names = {n.lower(): i for n, i in conn.execute(select(partners.c.name, partners.c.id)).all()}
    resolved = mapped.with_columns(
        pl.col("channel")
        .str.to_lowercase()
        .replace_strict(names, default=None, return_dtype=pl.Int64)
        .alias("channel_partner_id"),
        pl.col("courier")
        .str.to_lowercase()
        .replace_strict(names, default=None, return_dtype=pl.Int64)
        .alias("courier_partner_id")
        if "courier" in mapped.columns
        else pl.lit(None, pl.Int64).alias("courier_partner_id"),
    )
    unknown = pl.when(
        pl.col("channel").is_not_null() & pl.col("channel_partner_id").is_null()
    ).then(pl.format("Channel “{}” isn't a known partner", pl.col("channel")))
    if "courier" in mapped.columns:
        unknown = unknown.when(
            pl.col("courier").is_not_null() & pl.col("courier_partner_id").is_null()
        ).then(pl.format("Courier “{}” isn't a known partner", pl.col("courier")))
    return resolved.with_columns(pl.coalesce(pl.col(ERROR), unknown).alias(ERROR))


def _raw_rows(table: RawTable, personal: list[str], mapped: pl.DataFrame) -> pl.DataFrame:
    """Original cells per row, minus personal-data columns, with the row's error if any."""
    dropped = {normalise_header(h) for h in personal}
    keep = [h for h in table.headers if normalise_header(h) not in dropped]
    raw = drop_empty_rows(table).select(
        pl.col(ROW_NO).alias("row_no"),
        pl.struct(keep).struct.json_encode().alias("raw") if keep else pl.lit("{}").alias("raw"),
    )
    errors = mapped.select(pl.col(ROW_NO).alias("row_no"), pl.col(ERROR).alias("error"))
    return raw.join(errors, on="row_no", how="left")


# --- Bulk loading -----------------------------------------------------------------


def _copy(conn: Connection, table: str, frame: pl.DataFrame) -> None:
    """COPY a frame into a table over the request's own connection (same transaction)."""
    if frame.is_empty():
        return
    columns = ", ".join(frame.columns)
    payload = frame.write_csv(include_header=False, datetime_format="%Y-%m-%dT%H:%M:%S%.f%z")
    cursor = conn.connection.driver_connection.cursor()
    with cursor.copy(f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv)") as copy:
        copy.write(payload)


def _stage(conn: Connection, name: str, columns: str) -> None:
    conn.execute(text(f"CREATE TEMP TABLE {name} ({columns}) ON COMMIT DROP"))


def _optional(frame: pl.DataFrame, name: str, dtype: pl.DataType, alias: str | None = None):
    """An optional mapped field, or nulls when the partner's file doesn't have it."""
    expr = pl.col(name) if name in frame.columns else pl.lit(None, dtype)
    return expr.alias(alias or name)


_ORDER_FIELDS = (
    "channel_partner_id",
    "courier_partner_id",
    "awb",
    "zone",
    "order_date",
    "value_paise",
    "units",
    "payment_mode",
    "declared_weight_g",
    "delivery_status",
    "delivered_at",
    "returned_at",
)


def _cols(prefix: str = "") -> str:
    return ", ".join(f"{prefix}{c}" for c in _ORDER_FIELDS)


def _load_orders(conn: Connection, job: Job, good: pl.DataFrame) -> dict:
    latest = good.unique(subset="order_id", keep="last", maintain_order=True)
    frame = latest.select(
        "order_id",
        "channel_partner_id",
        "courier_partner_id",
        _optional(latest, "awb", pl.String),
        _optional(latest, "zone", pl.String),
        "order_date",
        pl.col("value").alias("value_paise"),
        _optional(latest, "units", pl.Int64).fill_null(1),
        "payment_mode",
        _optional(latest, "declared_weight", pl.Int64, "declared_weight_g"),
        "delivery_status",
        _optional(latest, "delivered_at", pl.Date),
        _optional(latest, "returned_at", pl.Date),
        pl.col(ROW_NO).alias("row_no"),
    )
    _stage(
        conn,
        "stage_orders",
        'order_id text COLLATE "C", channel_partner_id bigint, courier_partner_id bigint, '
        'awb text COLLATE "C", zone text, order_date date, value_paise bigint, units int, '
        "payment_mode text, declared_weight_g int, delivery_status text, delivered_at date, "
        "returned_at date, row_no int",
    )
    _copy(conn, "stage_orders", frame)
    conn.execute(text("ANALYZE stage_orders"))
    params = {"upload_id": job.upload_id}
    # Daily exports repeat most orders unchanged; only orders that moved are rewritten.
    updated = conn.execute(
        text(
            f"""
            UPDATE orders o SET ({_cols()}, upload_id, row_no, updated_at)
                = ({_cols("s.")}, :upload_id, s.row_no, now())
            FROM stage_orders s
            WHERE o.order_id = s.order_id
              AND ({_cols("o.")}) IS DISTINCT FROM ({_cols("s.")})
            """
        ),
        params,
    ).rowcount
    # Known orders leave staging first, so the INSERT never reads its own target (_insert_new).
    conn.execute(text("DELETE FROM stage_orders s USING orders o WHERE o.order_id = s.order_id"))
    created = conn.execute(
        text(
            f"""
            INSERT INTO orders (order_id, {_cols()}, upload_id, row_no)
            SELECT s.order_id, {_cols("s.")}, :upload_id, s.row_no
            FROM stage_orders s
            """
        ),
        params,
    ).rowcount
    return {
        "orders_created": created,
        "orders_updated": updated,
        "orders_unchanged": frame.height - created - updated,
        "duplicate_rows": good.height - frame.height,
    }


def _ledger_lines(kind: FileKind, good: pl.DataFrame) -> pl.DataFrame:
    """One row per money component per partner line, with the deduction signs already applied."""
    keys = ("order_ref", "awb", "txn_date", "reference", "billed_weight", "zone")
    base = good.select(pl.col(ROW_NO), *[c for c in keys if c in good.columns])
    if kind == FileKind.FREIGHT_INVOICE:
        lines = good.select(
            pl.col(ROW_NO),
            pl.when(pl.col("charge_type") == "return")
            .then(pl.lit(Component.FREIGHT_RETURN.value))
            .otherwise(pl.lit(Component.FREIGHT_FORWARD.value))
            .alias("component"),
            pl.col("amount").alias("amount_paise"),
        )
    else:
        components = {f.name: f.component.value for f in FIELDS[kind] if f.component}
        money = [name for name in components if name in good.columns]
        lines = good.unpivot(
            on=money, index=ROW_NO, variable_name="component", value_name="amount_paise"
        ).with_columns(pl.col("component").replace_strict(components, return_dtype=pl.String))
    lines = lines.filter(pl.col("amount_paise").is_not_null() & (pl.col("amount_paise") != 0))
    lines = pl.concat([lines, _unexplained(lines, good)])
    joined = lines.join(base, on=ROW_NO)
    return joined.select(
        pl.col(ROW_NO).alias("row_no"),
        _optional(joined, "order_ref", pl.String),
        _optional(joined, "awb", pl.String),
        "component",
        "amount_paise",
        "txn_date",
        _optional(joined, "reference", pl.String),
        _optional(joined, "billed_weight", pl.Int64, "billed_weight_g"),
        _optional(joined, "zone", pl.String),
    )


def _unexplained(lines: pl.DataFrame, good: pl.DataFrame) -> pl.DataFrame:
    """Where a file states a net amount, money the listed parts don't explain is an adjustment.

    A partner can short-pay without a column for it: the deduction then shows only in the net.
    """
    if "net" not in good.columns:
        return lines.clear()
    parts = lines.group_by(ROW_NO).agg(pl.col("amount_paise").sum().alias("parts"))
    return (
        good.select(ROW_NO, "net")
        .filter(pl.col("net").is_not_null())
        .join(parts, on=ROW_NO, how="left")
        .with_columns((pl.col("net") - pl.col("parts").fill_null(0)).alias("amount_paise"))
        .filter(pl.col("amount_paise") != 0)
        .select(
            pl.col(ROW_NO),
            pl.lit(Component.ADJUSTMENT.value).alias("component"),
            pl.col("amount_paise"),
        )
    )


# Each line is matched to its order on the way in: by order ID, else by AWB.
_MATCH_ORDER = """
    LEFT JOIN orders by_ref ON by_ref.order_id = s.order_ref
    LEFT JOIN orders by_awb ON by_awb.awb = s.awb AND by_ref.id IS NULL
"""


def _insert_new(conn: Connection, table: str, columns: str, upload_id: int) -> tuple[int, int]:
    """Insert rows from the temp table `keyed` whose line_key isn't stored yet.

    Known keys leave staging first, so the INSERT never reads its own target. Checking inside the
    INSERT took 114 s for 1.1 lakh lines: planned against a table that looked empty, it scanned a
    table that kept growing. Returns how many rows were created, and how many have an order.
    """
    conn.execute(text("ANALYZE keyed"))
    conn.execute(text(f"DELETE FROM keyed k USING {table} t WHERE t.line_key = k.line_key"))
    created, matched = conn.execute(text("SELECT count(*), count(order_id) FROM keyed")).one()
    conn.execute(
        text(
            f"""
            INSERT INTO {table} (upload_id, {columns}, order_id, line_key)
            SELECT :upload_id, {columns}, order_id, line_key FROM keyed
            """
        ),
        {"upload_id": upload_id},
    )
    return created, matched


_LINE_COLUMNS = (
    "row_no, partner_id, order_ref, awb, component, amount_paise, txn_date, reference, "
    "billed_weight_g, zone"
)


def _load_partner_lines(conn: Connection, job: Job, good: pl.DataFrame) -> dict:
    lines = _ledger_lines(job.file_kind, good)
    _stage(
        conn,
        "stage_lines",
        'row_no int, order_ref text COLLATE "C", awb text COLLATE "C", component text, '
        "amount_paise bigint, txn_date date, reference text, billed_weight_g int, zone text",
    )
    _copy(conn, "stage_lines", lines)
    conn.execute(text("ANALYZE stage_lines"))
    # The key leaves out the row number, so the same line in an overlapping export is skipped;
    # repeats inside one file are numbered, so a genuine double charge is kept.
    conn.execute(
        text(
            f"""
            CREATE TEMP TABLE keyed ON COMMIT DROP AS
            SELECT s.*, CAST(:partner_id AS bigint) AS partner_id,
                coalesce(by_ref.id, by_awb.id) AS order_id,
                md5(concat_ws('|', :partner_id, coalesce(s.order_ref, ''), coalesce(s.awb, ''),
                    s.component, coalesce(s.reference, ''), s.txn_date,
                    row_number() OVER (PARTITION BY s.order_ref, s.awb, s.component, s.reference,
                                       s.txn_date ORDER BY s.row_no)))::uuid AS line_key
            FROM stage_lines s
            {_MATCH_ORDER}
            """
        ),
        {"partner_id": job.partner_id},
    )
    created, matched = _insert_new(conn, "partner_lines", _LINE_COLUMNS, job.upload_id)
    return {
        "lines": lines.height,
        "lines_created": created,
        "lines_already_loaded": lines.height - created,
        "lines_matched": matched,
    }


def _load_return_scans(conn: Connection, job: Job, good: pl.DataFrame) -> dict:
    frame = good.select(
        pl.col(ROW_NO).alias("row_no"),
        _optional(good, "awb", pl.String),
        _optional(good, "order_ref", pl.String),
        _optional(good, "condition", pl.String),
        "scanned_at",
    )
    _stage(
        conn,
        "stage_scans",
        'row_no int, awb text COLLATE "C", order_ref text COLLATE "C", condition text, '
        "scanned_at timestamptz",
    )
    _copy(conn, "stage_scans", frame)
    conn.execute(text("ANALYZE stage_scans"))
    conn.execute(
        text(
            f"""
            CREATE TEMP TABLE keyed ON COMMIT DROP AS
            SELECT s.*, coalesce(by_ref.id, by_awb.id) AS order_id,
                md5(concat_ws('|', coalesce(s.awb, ''), coalesce(s.order_ref, ''), s.scanned_at,
                    row_number() OVER (PARTITION BY s.awb, s.order_ref, s.scanned_at
                                       ORDER BY s.row_no)))::uuid AS line_key
            FROM stage_scans s
            {_MATCH_ORDER}
            """
        )
    )
    created, matched = _insert_new(
        conn, "return_scans", "row_no, awb, order_ref, condition, scanned_at", job.upload_id
    )
    return {
        "scans": frame.height,
        "scans_created": created,
        "scans_already_loaded": frame.height - created,
        "scans_matched": matched,
    }


_LOADERS = {
    FileKind.ORDERS: _load_orders,
    FileKind.SETTLEMENT: _load_partner_lines,
    FileKind.COD_REMITTANCE: _load_partner_lines,
    FileKind.FREIGHT_INVOICE: _load_partner_lines,
    FileKind.RETURN_SCANS: _load_return_scans,
}


def _link_to_orders(conn: Connection) -> dict:
    """Match lines and scans that arrived before their orders: by order ID, then by AWB.

    Lines are matched as they load, so this only touches the few still unmatched (and only those
    are indexed). It lets files arrive in any order.
    """
    linked = 0
    for table in ("partner_lines", "return_scans"):
        for line_key, order_key in (("order_ref", "order_id"), ("awb", "awb")):
            linked += conn.execute(
                text(
                    f"UPDATE {table} t SET order_id = o.id FROM orders o "
                    f"WHERE t.order_id IS NULL AND t.{line_key} = o.{order_key}"
                )
            ).rowcount
    unlinked = conn.execute(
        text("SELECT count(*) FROM partner_lines WHERE order_id IS NULL")
    ).scalar_one()
    return {"newly_linked": linked, "unlinked_lines_total": unlinked}
