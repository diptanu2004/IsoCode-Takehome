from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Connection, select

from app.auth.deps import require
from app.auth.permissions import Permission
from app.db import get_conn
from app.enums import PartnerKind
from app.schema import partners

router = APIRouter(prefix="/api/partners", tags=["partners"])


class PartnerOut(BaseModel):
    id: int
    name: str
    kind: PartnerKind
    settlement_cycle_days: int
    tolerance_paise: int
    return_window_days: int


@router.get("", dependencies=[Depends(require(Permission.RECON_VIEW))])
def list_partners(conn: Connection = Depends(get_conn)) -> list[PartnerOut]:
    rows = conn.execute(
        select(
            partners.c.id,
            partners.c.name,
            partners.c.kind,
            partners.c.settlement_cycle_days,
            partners.c.tolerance_paise,
            partners.c.return_window_days,
        ).order_by(partners.c.id)
    ).all()
    return [PartnerOut.model_validate(r, from_attributes=True) for r in rows]
