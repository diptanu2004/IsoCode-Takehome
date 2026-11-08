from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Connection, and_, func, select

from app.auth.deps import require
from app.auth.permissions import Permission
from app.auth.sessions import CurrentUser
from app.claims import service
from app.claims.service import LIVE, TYPE_LABELS, ClaimActionError
from app.db import get_conn
from app.enums import ClaimStatus, ClaimType
from app.recon.engine import today
from app.schema import claim_events, claim_orders, claims, orders, partners, users

router = APIRouter(prefix="/api/claims", tags=["claims"])
_can_view = require(Permission.RECON_VIEW)
_can_work = require(Permission.CLAIM_WORK)
_can_export = require(Permission.CLAIM_EXPORT)

StatusGroup = Literal["live", "open", "raised", "partner_rejected", "closed"]
_GROUPS = {
    "live": LIVE,
    "open": [ClaimStatus.OPEN.value],
    "raised": [ClaimStatus.RAISED.value],
    "partner_rejected": [ClaimStatus.PARTNER_REJECTED.value],
    "closed": [
        ClaimStatus.RECOVERED.value,
        ClaimStatus.WRITTEN_OFF.value,
        ClaimStatus.AUTO_RESOLVED.value,
    ],
}


class ClaimRow(BaseModel):
    id: int
    partner_id: int
    partner: str
    type: ClaimType
    type_label: str
    order_pk: int | None
    order_id: str | None
    component: str | None
    period_month: date | None
    amount_paise: int
    recovered_paise: int
    status: ClaimStatus
    partner_ref: str | None
    aging_from: date
    age_days: int
    opened_at: datetime


class ClaimPage(BaseModel):
    rows: list[ClaimRow]
    total: int
    total_paise: int
    page: int
    page_size: int


class PartnerTotal(BaseModel):
    partner_id: int
    partner: str
    live_claims: int
    open_claims: int
    outstanding_paise: int
    recovered_paise: int


class ClaimAction(BaseModel):
    action: Literal["raise", "recover", "reject", "write_off"]
    partner_ref: str | None = Field(default=None, max_length=80)
    recovered_paise: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=500)


class RaiseAll(BaseModel):
    partner_id: int
    partner_ref: str | None = Field(default=None, max_length=80)


def _rows_query():
    return (
        select(
            claims.c.id,
            claims.c.partner_id,
            partners.c.name.label("partner"),
            claims.c.type,
            claims.c.order_id.label("order_pk"),
            orders.c.order_id,
            claims.c.component,
            claims.c.period_month,
            claims.c.amount_paise,
            claims.c.recovered_paise,
            claims.c.status,
            claims.c.partner_ref,
            claims.c.aging_from,
            claims.c.opened_at,
        )
        .join(partners, partners.c.id == claims.c.partner_id)
        .outerjoin(orders, orders.c.id == claims.c.order_id)
    )


def _row(r, as_of: date) -> ClaimRow:
    return ClaimRow(
        **r._mapping,
        type_label=TYPE_LABELS[ClaimType(r.type)],
        age_days=max(0, (as_of - r.aging_from).days),
    )


@router.get("", dependencies=[Depends(_can_view)])
def list_claims(
    group: StatusGroup = "live",
    partner_id: int | None = None,
    type_: ClaimType | None = Query(None, alias="type"),
    q: str | None = Query(None, max_length=40),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    conn: Connection = Depends(get_conn),
) -> ClaimPage:
    conditions = [claims.c.status.in_(_GROUPS[group])]
    if partner_id is not None:
        conditions.append(claims.c.partner_id == partner_id)
    if type_ is not None:
        conditions.append(claims.c.type == type_.value)
    if q:
        conditions.append(orders.c.order_id.startswith(q.strip().upper()))
    where = and_(*conditions)
    counted = conn.execute(
        select(
            func.count(),
            func.coalesce(func.sum(claims.c.amount_paise - claims.c.recovered_paise), 0),
        )
        .select_from(claims.outerjoin(orders, orders.c.id == claims.c.order_id))
        .where(where)
    ).one()
    rows = conn.execute(
        _rows_query()
        .where(where)
        .order_by(claims.c.aging_from, claims.c.amount_paise.desc(), claims.c.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    as_of = today()
    return ClaimPage(
        rows=[_row(r, as_of) for r in rows],
        total=counted[0],
        total_paise=counted[1],
        page=page,
        page_size=page_size,
    )


@router.get("/partners", dependencies=[Depends(_can_view)])
def partner_totals(conn: Connection = Depends(get_conn)) -> list[PartnerTotal]:
    """Live claims and money per partner, largest first."""
    totals: dict[int, dict] = {}
    for r in service.claim_counts(conn):
        t = totals.setdefault(
            r.partner_id,
            {
                "partner_id": r.partner_id,
                "partner": r.partner,
                "live_claims": 0,
                "open_claims": 0,
                "outstanding_paise": 0,
                "recovered_paise": 0,
            },
        )
        if r.status in LIVE:
            t["live_claims"] += r.claims
            t["outstanding_paise"] += r.outstanding_paise
        if r.status == ClaimStatus.OPEN.value:
            t["open_claims"] += r.claims
        t["recovered_paise"] += r.recovered_paise or 0
    found = [PartnerTotal(**t) for t in totals.values() if t["live_claims"] or t["recovered_paise"]]
    return sorted(found, key=lambda t: -t.outstanding_paise)


@router.get("/{claim_id}", dependencies=[Depends(_can_view)])
def claim_detail(claim_id: int, conn: Connection = Depends(get_conn)) -> dict:
    row = conn.execute(_rows_query().where(claims.c.id == claim_id)).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found.")
    events = conn.execute(
        select(
            claim_events.c.from_status,
            claim_events.c.to_status,
            claim_events.c.note,
            claim_events.c.at,
            users.c.name.label("by"),
        )
        .outerjoin(users, users.c.id == claim_events.c.actor_id)
        .where(claim_events.c.claim_id == claim_id)
        .order_by(claim_events.c.at, claim_events.c.id)
    ).all()
    members = conn.execute(
        select(orders.c.id, orders.c.order_id, claim_orders.c.amount_paise)
        .join(orders, orders.c.id == claim_orders.c.order_id)
        .where(claim_orders.c.claim_id == claim_id)
        .order_by(claim_orders.c.amount_paise.desc(), orders.c.order_id)
        .limit(200)
    ).all()
    order_count = conn.execute(
        select(func.count()).where(claim_orders.c.claim_id == claim_id)
    ).scalar_one()
    return {
        **_row(row, today()).model_dump(),
        "events": [dict(e._mapping) for e in events],
        "orders": [dict(m._mapping) for m in members],  # the 200 largest
        "order_count": order_count,
    }


@router.post("/{claim_id}/actions")
def act_on_claim(
    claim_id: int,
    body: ClaimAction,
    user=Depends(_can_work),
    conn: Connection = Depends(get_conn),
) -> dict:
    if body.action == "write_off" and not user.can(Permission.CLAIM_WRITE_OFF):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the co-founder can write off a claim.")
    try:
        return service.act(
            conn, claim_id, body.action, user.id, body.partner_ref, body.recovered_paise, body.note
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ClaimActionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/raise-all")
def raise_all(
    body: RaiseAll, user: CurrentUser = Depends(_can_work), conn: Connection = Depends(get_conn)
) -> dict:
    """Raise every open claim with one partner at once, for example with one evidence pack."""
    return {"raised": service.raise_all_open(conn, body.partner_id, user.id, body.partner_ref)}


@router.get("/evidence/{partner_id}.zip", dependencies=[Depends(_can_export)])
def evidence(
    partner_id: int, group: StatusGroup = "live", conn: Connection = Depends(get_conn)
) -> Response:
    try:
        filename, content = service.evidence_pack(conn, partner_id, _GROUPS[group], today())
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
