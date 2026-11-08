"""Rate cards: what each partner should charge, as dated versions (TDD §7.5, §8.2).

Everyone with access can read them; only the co-founder adds a version. A version is never
edited in place: a new one starts on a date, the previous one ends the day before, and the
partner's orders from that date are matched again against it.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, func, insert, select

from app import audit
from app.auth.deps import require
from app.auth.permissions import Permission
from app.auth.sessions import CurrentUser
from app.claims.service import LIVE
from app.db import get_conn
from app.enums import ClaimType, PartnerKind
from app.recon.engine import match_partner
from app.schema import claims, order_recon, partners, rate_cards, users

router = APIRouter(prefix="/api/rate-cards", tags=["rate cards"])
_can_edit = require(Permission.RATE_CARD_EDIT)

Rupees = Annotated[float, Field(ge=0, le=100_000)]
Percent = Annotated[float, Field(ge=0, le=100)]


class Shipping(BaseModel):
    slab_g: int = Field(gt=0, le=50_000)
    first: Rupees
    additional: Rupees


class OnReturn(BaseModel):
    refund_commission: bool = True
    refund_fixed_fee: bool = True
    return_shipping_pct_of_forward: Percent = 100


class MarketplaceRules(BaseModel):
    """A marketplace or quick-commerce channel: commission, a fixed fee, optional shipping."""

    type: Literal["marketplace"]
    commission_pct: Percent
    commission_rounding: Literal["paise", "rupee"] = "paise"
    fixed_fee: Rupees
    shipping: Shipping | None = None
    on_return: OnReturn = OnReturn()


class ZoneRate(BaseModel):
    first: Rupees
    additional: Rupees


class CodFee(BaseModel):
    pct: Percent
    min: Rupees


class CourierRules(BaseModel):
    """A courier: freight per weight slab and zone, return freight, and a COD fee."""

    type: Literal["courier"]
    slab_g: int = Field(gt=0, le=50_000)
    forward: dict[Literal["A", "B", "C", "D", "E"], ZoneRate] = Field(min_length=1)
    return_pct_of_forward: Annotated[float, Field(ge=0, le=200)] = 100
    cod_fee: CodFee


Rules = Annotated[MarketplaceRules | CourierRules, Field(discriminator="type")]

_RULE_TYPE = {
    PartnerKind.MARKETPLACE: "marketplace",
    PartnerKind.QUICK_COMMERCE: "marketplace",
    PartnerKind.COURIER: "courier",
}


class Version(BaseModel):
    id: int
    version: int
    effective_from: date
    effective_to: date | None  # the day before the next version; None = in force now
    rules: dict
    note: str | None
    created_by: str | None
    created_at: datetime
    orders_priced: int


class PartnerCards(BaseModel):
    partner_id: int
    partner: str
    kind: PartnerKind
    versions: list[Version]  # newest first
    suspected: list[dict]  # live systematic-gap claims: a rate may have changed unannounced


class NewVersion(BaseModel):
    effective_from: date
    rules: Rules
    note: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def no_far_future(self):
        if self.effective_from.year > date.today().year + 1:
            raise ValueError("The start date is too far in the future.")
        return self


@router.get("", dependencies=[Depends(require(Permission.RATE_CARD_VIEW))])
def list_rate_cards(conn: Connection = Depends(get_conn)) -> list[PartnerCards]:
    priced = {
        r.card: r.orders
        for r in conn.execute(
            select(order_recon.c.channel_rate_card_id.label("card"), func.count().label("orders"))
            .where(order_recon.c.channel_rate_card_id.is_not(None))
            .group_by(order_recon.c.channel_rate_card_id)
            .union_all(
                select(order_recon.c.courier_rate_card_id, func.count())
                .where(order_recon.c.courier_rate_card_id.is_not(None))
                .group_by(order_recon.c.courier_rate_card_id)
            )
        )
    }
    cards = conn.execute(
        select(
            rate_cards.c.id,
            rate_cards.c.partner_id,
            rate_cards.c.version,
            rate_cards.c.effective_from,
            rate_cards.c.effective_to,
            rate_cards.c.rules,
            rate_cards.c.note,
            rate_cards.c.created_at,
            users.c.name.label("created_by"),
        )
        .outerjoin(users, users.c.id == rate_cards.c.created_by)
        .order_by(
            rate_cards.c.partner_id, rate_cards.c.effective_from.desc(), rate_cards.c.version.desc()
        )
    ).all()
    suspected: dict[int, list[dict]] = {}
    for r in conn.execute(
        select(
            claims.c.id,
            claims.c.partner_id,
            claims.c.component,
            claims.c.period_month,
            claims.c.amount_paise,
        )
        .where(claims.c.type == ClaimType.SYSTEMATIC_GAP.value, claims.c.status.in_(LIVE))
        .order_by(claims.c.period_month)
    ):
        suspected.setdefault(r.partner_id, []).append(
            {
                "claim_id": r.id,
                "component": r.component,
                "period_month": r.period_month,
                "amount_paise": r.amount_paise,
            }
        )

    by_partner: dict[int, list[Version]] = {}
    for c in cards:
        versions = by_partner.setdefault(c.partner_id, [])
        # Newest first, so the last one added is the version that follows this one: this one
        # ends the day before it starts (the same rule the matching engine applies).
        effective_to = c.effective_to
        if versions:
            day_before_next = versions[-1].effective_from - timedelta(days=1)
            effective_to = min(effective_to or day_before_next, day_before_next)
        versions.append(
            Version(
                id=c.id,
                version=c.version,
                effective_from=c.effective_from,
                effective_to=effective_to,
                rules=c.rules,
                note=c.note,
                created_by=c.created_by,
                created_at=c.created_at,
                orders_priced=priced.get(c.id, 0),
            )
        )

    found = conn.execute(
        select(partners.c.id, partners.c.name, partners.c.kind)
        .where(partners.c.kind.in_(list(_RULE_TYPE)))
        .order_by(partners.c.id)
    ).all()
    return [
        PartnerCards(
            partner_id=p.id,
            partner=p.name,
            kind=p.kind,
            versions=by_partner.get(p.id, []),
            suspected=suspected.get(p.id, []),
        )
        for p in found
    ]


@router.post("/{partner_id}", status_code=status.HTTP_201_CREATED)
def add_version(
    partner_id: int,
    body: NewVersion,
    user: CurrentUser = Depends(_can_edit),
    conn: Connection = Depends(get_conn),
) -> dict:
    partner = conn.execute(
        select(partners.c.name, partners.c.kind).where(partners.c.id == partner_id)
    ).one_or_none()
    if partner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Partner not found.")
    expected_type = _RULE_TYPE.get(PartnerKind(partner.kind))
    if expected_type is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{partner.name} has no rate card."
        )
    if body.rules.type != expected_type:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{partner.name} needs a {expected_type} rate card.",
        )

    # Serialise versions for this partner, so two people can't both create "version 3".
    latest = conn.execute(
        select(rate_cards.c.version, rate_cards.c.effective_from)
        .where(rate_cards.c.partner_id == partner_id)
        .order_by(rate_cards.c.version.desc())
        .limit(1)
        .with_for_update()
    ).one_or_none()
    if latest and body.effective_from <= latest.effective_from:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The new version must start after version {latest.version}, which starts on "
            f"{latest.effective_from:%d %b %Y}.",
        )
    version = (latest.version if latest else 0) + 1
    rules = body.rules.model_dump(mode="json")
    card_id = conn.execute(
        insert(rate_cards)
        .values(
            partner_id=partner_id,
            version=version,
            effective_from=body.effective_from,
            rules=rules,
            note=body.note.strip(),
            created_by=user.id,
        )
        .returning(rate_cards.c.id)
    ).scalar_one()
    audit.record(
        conn,
        user.id,
        "rate_card.create",
        "rate_card",
        card_id,
        after={
            "partner": partner.name,
            "version": version,
            "effective_from": str(body.effective_from),
            "rules": rules,
        },
    )
    result = match_partner(conn, partner_id, since=body.effective_from)
    return {"id": card_id, "version": version, **result}
