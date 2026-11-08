"""Reconciliation results: the order list, its status counts, and one order in full (TDD §7.2)."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Connection, and_, case, func, or_, select
from sqlalchemy.orm import aliased

from app.auth.deps import require
from app.auth.permissions import Permission
from app.db import get_conn
from app.enums import ReconStatus
from app.schema import (
    claim_orders,
    claims,
    order_recon,
    orders,
    partner_lines,
    partners,
    rate_cards,
    return_scans,
    source_rows,
    sources,
    uploads,
)

router = APIRouter(prefix="/api/recon", tags=["reconciliation"])
_can_view = require(Permission.RECON_VIEW)

PROBLEMS = [
    ReconStatus.RETURN_NOT_RECEIVED,
    ReconStatus.UNPAID_PAST_DUE,
    ReconStatus.SHORT_PAID,
    ReconStatus.FREIGHT_OVERCHARGED,
]
_SEVERITY = {s.value: i for i, s in enumerate(ReconStatus)}
COMPONENT_LABELS = {
    "sale": "Sale",
    "commission": "Commission",
    "fixed_fee": "Fixed fee",
    "shipping_fee": "Shipping fee",
    "return_shipping_fee": "Return shipping fee",
    "adjustment": "Adjustments",
    "freight_forward": "Forward freight",
    "freight_return": "Return freight",
    "cod_fee": "COD fee",
    "tcs": "TCS",
    "tds": "TDS",
}

channel = aliased(partners, name="channel")
courier = aliased(partners, name="courier")


class OrderRow(BaseModel):
    id: int
    order_id: str
    channel: str
    courier: str | None
    order_date: date
    value_paise: int
    payment_mode: str
    delivery_status: str
    expected_paise: int
    received_paise: int
    diff_paise: int
    tcs_paise: int
    tds_paise: int
    status: ReconStatus
    issues: list[str]
    due_date: date | None


class OrderPage(BaseModel):
    rows: list[OrderRow]
    total: int
    page: int
    page_size: int


class StatusCount(BaseModel):
    status: ReconStatus
    orders: int
    diff_paise: int


def _filters(
    partner_id: int | None, date_from: date | None, date_to: date | None, q: str | None
) -> list:
    conditions = []
    if partner_id is not None:
        conditions.append(
            or_(
                orders.c.channel_partner_id == partner_id, orders.c.courier_partner_id == partner_id
            )
        )
    if date_from is not None:
        conditions.append(orders.c.order_date >= date_from)
    if date_to is not None:
        conditions.append(orders.c.order_date <= date_to)
    if q:
        term = q.strip().upper()
        conditions.append(or_(orders.c.order_id.startswith(term), orders.c.awb == term))
    return conditions


def _status_filter(status_: str | None):
    if status_ == "problems":
        return order_recon.c.status.in_([s.value for s in PROBLEMS])
    if status_:
        return order_recon.c.status == status_
    return None


_ROW_COLUMNS = (
    orders.c.id,
    orders.c.order_id,
    channel.c.name.label("channel"),
    courier.c.name.label("courier"),
    orders.c.order_date,
    orders.c.value_paise,
    orders.c.payment_mode,
    orders.c.delivery_status,
    order_recon.c.expected_paise,
    order_recon.c.received_paise,
    order_recon.c.diff_paise,
    order_recon.c.tcs_paise,
    order_recon.c.tds_paise,
    order_recon.c.status,
    order_recon.c.issues,
    order_recon.c.due_date,
)


def _from_clause():
    return (
        order_recon.join(orders, orders.c.id == order_recon.c.order_id)
        .join(channel, channel.c.id == orders.c.channel_partner_id)
        .outerjoin(courier, courier.c.id == orders.c.courier_partner_id)
    )


@router.get("/orders", dependencies=[Depends(_can_view)])
def list_orders(
    status_: str | None = Query(None, alias="status", pattern=r"^[a-z_]{3,30}$"),
    partner_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(None, max_length=40),
    sort: Literal["severity", "difference", "date", "value"] = "severity",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    conn: Connection = Depends(get_conn, scope="function"),
) -> OrderPage:
    if status_ and status_ != "problems" and status_ not in _SEVERITY:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown status.")
    conditions = _filters(partner_id, date_from, date_to, q)
    if (by_status := _status_filter(status_)) is not None:
        conditions.append(by_status)
    where = and_(*conditions) if conditions else None

    severity = case(_SEVERITY, value=order_recon.c.status)
    order_by = {
        "severity": (severity, order_recon.c.diff_paise, orders.c.order_date.desc()),
        "difference": (order_recon.c.diff_paise, orders.c.id),
        "date": (orders.c.order_date.desc(), orders.c.id.desc()),
        "value": (orders.c.value_paise.desc(), orders.c.id),
    }[sort]

    query = select(*_ROW_COLUMNS).select_from(_from_clause())
    count = select(func.count()).select_from(_from_clause())
    if where is not None:
        query, count = query.where(where), count.where(where)
    total = conn.execute(count).scalar_one()
    rows = conn.execute(
        query.order_by(*order_by).limit(page_size).offset((page - 1) * page_size)
    ).all()
    return OrderPage(
        rows=[OrderRow.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/summary", dependencies=[Depends(_can_view)])
def status_counts(
    partner_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(None, max_length=40),
    conn: Connection = Depends(get_conn, scope="function"),
) -> list[StatusCount]:
    """Orders and money per status, for the filter chips. Ignores the status filter itself."""
    conditions = _filters(partner_id, date_from, date_to, q)
    query = (
        select(
            order_recon.c.status,
            func.count().label("orders"),
            func.coalesce(func.sum(order_recon.c.diff_paise), 0).label("diff_paise"),
        )
        .select_from(order_recon.join(orders, orders.c.id == order_recon.c.order_id))
        .group_by(order_recon.c.status)
    )
    if conditions:
        query = query.where(and_(*conditions))
    rows = conn.execute(query).all()
    found = sorted(rows, key=lambda r: _SEVERITY[r.status])
    return [StatusCount.model_validate(r, from_attributes=True) for r in found]


@router.get("/orders/{order_pk}", dependencies=[Depends(_can_view)])
def order_detail(order_pk: int, conn: Connection = Depends(get_conn, scope="function")) -> dict:
    """One order in full: expected against actual per component, and where each number came from."""
    row = conn.execute(
        select(
            *_ROW_COLUMNS,
            orders.c.awb,
            orders.c.zone,
            orders.c.declared_weight_g,
            orders.c.units,
            orders.c.delivered_at,
            orders.c.returned_at,
            order_recon.c.breakdown,
            order_recon.c.channel_rate_card_id,
            order_recon.c.courier_rate_card_id,
            order_recon.c.computed_at,
        )
        .select_from(_from_clause())
        .where(orders.c.id == order_pk)
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found.")

    components = [
        {
            "component": name,
            "label": COMPONENT_LABELS.get(name, name),
            "expected_paise": pair[0],
            "actual_paise": pair[1],
            "gap_paise": pair[1] - pair[0],
        }
        for name, pair in sorted(
            row.breakdown.items(), key=lambda kv: list(COMPONENT_LABELS).index(kv[0])
        )
    ]
    lines = conn.execute(
        select(
            partner_lines.c.component,
            partner_lines.c.amount_paise,
            partner_lines.c.txn_date,
            partner_lines.c.reference,
            partner_lines.c.billed_weight_g,
            partner_lines.c.zone,
            partner_lines.c.upload_id,
            partner_lines.c.row_no,
            partners.c.name.label("partner"),
            uploads.c.filename,
        )
        .join(partners, partners.c.id == partner_lines.c.partner_id)
        .outerjoin(uploads, uploads.c.id == partner_lines.c.upload_id)
        .where(partner_lines.c.order_id == order_pk)
        .order_by(partner_lines.c.txn_date, partner_lines.c.upload_id, partner_lines.c.row_no)
    ).all()
    scans = conn.execute(
        select(
            return_scans.c.scanned_at,
            return_scans.c.condition,
            return_scans.c.upload_id,
            return_scans.c.row_no,
            uploads.c.filename,
        )
        .outerjoin(uploads, uploads.c.id == return_scans.c.upload_id)
        .where(return_scans.c.order_id == order_pk)
    ).all()
    order_claims = conn.execute(
        select(
            claims.c.id,
            claims.c.type,
            claims.c.amount_paise,
            claims.c.status,
            claims.c.aging_from,
            partners.c.name.label("partner"),
        )
        .join(partners, partners.c.id == claims.c.partner_id)
        .where(
            or_(
                claims.c.order_id == order_pk,
                claims.c.id.in_(
                    select(claim_orders.c.claim_id).where(claim_orders.c.order_id == order_pk)
                ),
            )
        )
        .order_by(claims.c.id)
    ).all()
    cards = {
        r.id: {"partner": r.name, "version": r.version, "effective_from": r.effective_from}
        for r in conn.execute(
            select(
                rate_cards.c.id, rate_cards.c.version, rate_cards.c.effective_from, partners.c.name
            )
            .join(partners, partners.c.id == rate_cards.c.partner_id)
            .where(rate_cards.c.id.in_([row.channel_rate_card_id, row.courier_rate_card_id]))
        )
    }
    base = OrderRow.model_validate(row, from_attributes=True).model_dump()
    return {
        **base,
        "awb": row.awb,
        "zone": row.zone,
        "declared_weight_g": row.declared_weight_g,
        "units": row.units,
        "delivered_at": row.delivered_at,
        "returned_at": row.returned_at,
        "computed_at": row.computed_at,
        "components": components,
        "lines": [dict(r._mapping) for r in lines],
        "scans": [dict(r._mapping) for r in scans],
        "claims": [dict(r._mapping) for r in order_claims],
        "rate_cards": [
            cards[i] for i in (row.channel_rate_card_id, row.courier_rate_card_id) if i in cards
        ],
    }


@router.get("/lines/{upload_id}/{row_no}", dependencies=[Depends(_can_view)])
def source_row(
    upload_id: int, row_no: int, conn: Connection = Depends(get_conn, scope="function")
) -> dict:
    """The partner's own row, as it was in their file (minus personal data)."""
    row = conn.execute(
        select(
            source_rows.c.raw,
            uploads.c.filename,
            uploads.c.detected,
            sources.c.name.label("source"),
        )
        .join(uploads, uploads.c.id == source_rows.c.upload_id)
        .join(sources, sources.c.id == uploads.c.source_id)
        .where(source_rows.c.upload_id == upload_id, source_rows.c.row_no == row_no)
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Row not found.")
    order = [h for h in (row.detected or {}).get("headers", []) if h in row.raw]
    order += [h for h in row.raw if h not in order]
    return {
        "source": row.source,
        "filename": row.filename,
        "row_no": row_no,
        "cells": [{"column": h, "value": row.raw[h]} for h in order],
    }
