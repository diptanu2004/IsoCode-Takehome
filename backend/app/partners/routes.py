"""Partners and their settings: settlement cycle, tolerance and return window (TDD §8.4-8.5)."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Connection, select, update

from app import audit
from app.auth.deps import require
from app.auth.permissions import Permission
from app.auth.sessions import CurrentUser
from app.db import get_conn
from app.enums import PartnerKind
from app.recon.engine import match_partner
from app.schema import partners

router = APIRouter(prefix="/api/partners", tags=["partners"])
_can_edit = require(Permission.SETTINGS_EDIT)


class PartnerOut(BaseModel):
    id: int
    name: str
    kind: PartnerKind
    settlement_cycle_days: int
    tolerance_paise: int
    return_window_days: int


class PartnerChange(BaseModel):
    settlement_cycle_days: int | None = Field(default=None, ge=1, le=90)
    tolerance_paise: int | None = Field(default=None, ge=0, le=100_000)  # up to ₹1,000
    return_window_days: int | None = Field(default=None, ge=0, le=60)


_COLUMNS = (
    partners.c.id,
    partners.c.name,
    partners.c.kind,
    partners.c.settlement_cycle_days,
    partners.c.tolerance_paise,
    partners.c.return_window_days,
)


@router.get("", dependencies=[Depends(require(Permission.RECON_VIEW))])
def list_partners(conn: Connection = Depends(get_conn, scope="function")) -> list[PartnerOut]:
    rows = conn.execute(select(*_COLUMNS).order_by(partners.c.id)).all()
    return [PartnerOut.model_validate(r, from_attributes=True) for r in rows]


@router.patch("/{partner_id}")
def change_partner(
    partner_id: int,
    body: PartnerChange,
    actor: CurrentUser = Depends(_can_edit),
    conn: Connection = Depends(get_conn, scope="function"),
) -> dict:
    """Change a partner's settings, then match its orders again so statuses and claims follow."""
    before = conn.execute(
        select(*_COLUMNS).where(partners.c.id == partner_id).with_for_update()
    ).one_or_none()
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Partner not found.")
    changed = {
        k: v for k, v in body.model_dump(exclude_none=True).items() if getattr(before, k) != v
    }
    result = {"orders_matched": 0, "claims_opened": 0, "claims_updated": 0, "claims_resolved": 0}
    if changed:
        conn.execute(update(partners).where(partners.c.id == partner_id).values(**changed))
        audit.record(
            conn,
            actor.id,
            "partner.update",
            "partner",
            partner_id,
            before={k: getattr(before, k) for k in changed},
            after=changed,
        )
        result = match_partner(conn, partner_id)
    partner = conn.execute(select(*_COLUMNS).where(partners.c.id == partner_id)).one()
    return {"partner": PartnerOut.model_validate(partner, from_attributes=True), **result}
