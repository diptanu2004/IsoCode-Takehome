"""The co-founder's Overview: money at risk by partner and age, and the targets (TDD §7.4)."""

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Connection, Date, case, exists, func, literal, select

from app.auth.deps import require
from app.auth.permissions import Permission
from app.claims.service import AGE_BUCKETS, LIVE
from app.db import get_conn
from app.enums import ClaimStatus, ClaimType, ReconStatus, UploadStatus
from app.recon.engine import today
from app.schema import claims, order_recon, orders, partner_lines, partners, uploads

router = APIRouter(prefix="/api/overview", tags=["overview"])

RECOVERY_TARGET_PAISE = 30_00_000 * 100  # ₹30 L in year one (TDD §2)
AUTO_MATCH_TARGET = 0.98


class PartnerRisk(BaseModel):
    partner_id: int
    partner: str
    by_age: list[int]  # paise, one per age bucket
    claims_by_age: list[int]
    total_paise: int
    claims: int


class MoneyAtRisk(BaseModel):
    buckets: list[str]
    partners: list[PartnerRisk]
    by_age: list[int]
    total_paise: int
    claims: int
    not_raised_paise: int


class Recovery(BaseModel):
    recovered_paise: int
    target_paise: int
    written_off_paise: int
    paid_late_paise: int  # claims closed because the partner paid after all


class AutoMatch(BaseModel):
    rate: float | None  # None until a partner file is loaded
    target: float
    matched_orders: int
    unmatched_rows: int  # partner rows that match no order


class WithinTolerance(BaseModel):
    orders: int
    gap_paise: int
    patterns: int  # live systematic-gap claims


class PartnerTaxes(BaseModel):
    partner_id: int
    partner: str
    tcs_paise: int
    tds_paise: int


class Overview(BaseModel):
    as_of: date
    last_file_at: datetime | None
    orders: int
    at_risk: MoneyAtRisk
    recovery: Recovery
    auto_match: AutoMatch
    within_tolerance: WithinTolerance
    taxes: list[PartnerTaxes]


def _bucket_index(as_of: date):
    age = literal(as_of, Date) - claims.c.aging_from
    whens = [(age <= last, i) for i, (_, _, last) in enumerate(AGE_BUCKETS) if last is not None]
    return case(*whens, else_=len(AGE_BUCKETS) - 1)


def _money_at_risk(conn: Connection, as_of: date) -> MoneyAtRisk:
    bucket = _bucket_index(as_of).label("bucket")
    outstanding = claims.c.amount_paise - claims.c.recovered_paise
    rows = conn.execute(
        select(
            claims.c.partner_id,
            partners.c.name,
            bucket,
            func.count().label("claims"),
            func.sum(outstanding).label("paise"),
            func.sum(case((claims.c.status == ClaimStatus.OPEN.value, outstanding), else_=0)).label(
                "not_raised"
            ),
        )
        .join(partners, partners.c.id == claims.c.partner_id)
        .where(claims.c.status.in_(LIVE))
        .group_by(claims.c.partner_id, partners.c.name, bucket)
    ).all()

    found: dict[int, PartnerRisk] = {}
    width = len(AGE_BUCKETS)
    not_raised = 0
    for r in rows:
        p = found.setdefault(
            r.partner_id,
            PartnerRisk(
                partner_id=r.partner_id,
                partner=r.name,
                by_age=[0] * width,
                claims_by_age=[0] * width,
                total_paise=0,
                claims=0,
            ),
        )
        paise = int(r.paise)  # SUM comes back as a Decimal
        p.by_age[r.bucket] += paise
        p.claims_by_age[r.bucket] += r.claims
        p.total_paise += paise
        p.claims += r.claims
        not_raised += int(r.not_raised)
    ranked = sorted(found.values(), key=lambda p: -p.total_paise)
    return MoneyAtRisk(
        buckets=[key for key, _, _ in AGE_BUCKETS],
        partners=ranked,
        by_age=[sum(p.by_age[i] for p in ranked) for i in range(width)],
        total_paise=sum(p.total_paise for p in ranked),
        claims=sum(p.claims for p in ranked),
        not_raised_paise=not_raised,
    )


def _recovery(conn: Connection) -> Recovery:
    r = conn.execute(
        select(
            func.coalesce(func.sum(claims.c.recovered_paise), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            claims.c.status == ClaimStatus.WRITTEN_OFF.value,
                            claims.c.amount_paise - claims.c.recovered_paise,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (claims.c.status == ClaimStatus.AUTO_RESOLVED.value, claims.c.amount_paise),
                        else_=0,
                    )
                ),
                0,
            ),
        )
    ).one()
    return Recovery(
        recovered_paise=int(r[0]),
        target_paise=RECOVERY_TARGET_PAISE,
        written_off_paise=int(r[1]),
        paid_late_paise=int(r[2]),
    )


def _auto_match(conn: Connection) -> AutoMatch:
    """[A1]: orders linked to their partner rows by key, against those plus rows with no order."""
    matched = conn.execute(
        select(func.count())
        .select_from(orders)
        .where(
            exists().where(partner_lines.c.order_id == orders.c.id),
            exists().where(
                order_recon.c.order_id == orders.c.id, order_recon.c.auto_matched.is_(True)
            ),
        )
    ).scalar_one()
    # Rows, not lines: one settlement row becomes several lines (sale, commission, fees...).
    unmatched_rows = (
        select(partner_lines.c.upload_id, partner_lines.c.row_no)
        .where(partner_lines.c.order_id.is_(None))
        .distinct()
        .subquery()
    )
    unmatched = conn.execute(select(func.count()).select_from(unmatched_rows)).scalar_one()
    seen = matched + unmatched
    return AutoMatch(
        rate=matched / seen if seen else None,
        target=AUTO_MATCH_TARGET,
        matched_orders=matched,
        unmatched_rows=unmatched,
    )


def _within_tolerance(conn: Connection) -> WithinTolerance:
    r = conn.execute(
        select(func.count(), func.coalesce(func.sum(order_recon.c.diff_paise), 0)).where(
            order_recon.c.status == ReconStatus.WITHIN_TOLERANCE.value
        )
    ).one()
    patterns = conn.execute(
        select(func.count()).where(
            claims.c.type == ClaimType.SYSTEMATIC_GAP.value, claims.c.status.in_(LIVE)
        )
    ).scalar_one()
    return WithinTolerance(orders=r[0], gap_paise=int(r[1]), patterns=patterns)


def _taxes(conn: Connection) -> list[PartnerTaxes]:
    """TCS and TDS as each partner reported them; positive = withheld from Company A."""
    rows = conn.execute(
        select(
            partner_lines.c.partner_id,
            partners.c.name,
            func.sum(
                case((partner_lines.c.component == "tcs", partner_lines.c.amount_paise), else_=0)
            ).label("tcs"),
            func.sum(
                case((partner_lines.c.component == "tds", partner_lines.c.amount_paise), else_=0)
            ).label("tds"),
        )
        .join(partners, partners.c.id == partner_lines.c.partner_id)
        .where(partner_lines.c.component.in_(["tcs", "tds"]))
        .group_by(partner_lines.c.partner_id, partners.c.name)
    ).all()
    found = [
        PartnerTaxes(
            partner_id=r.partner_id, partner=r.name, tcs_paise=-int(r.tcs), tds_paise=-int(r.tds)
        )
        for r in rows
    ]
    return sorted(found, key=lambda t: -(t.tcs_paise + t.tds_paise))


@router.get("", dependencies=[Depends(require(Permission.DASHBOARD_VIEW))])
def overview(conn: Connection = Depends(get_conn, scope="function")) -> Overview:
    as_of = today()
    return Overview(
        as_of=as_of,
        last_file_at=conn.execute(
            select(func.max(uploads.c.finished_at)).where(
                uploads.c.status == UploadStatus.DONE.value
            )
        ).scalar_one(),
        orders=conn.execute(select(func.count()).select_from(order_recon)).scalar_one(),
        at_risk=_money_at_risk(conn, as_of),
        recovery=_recovery(conn),
        auto_match=_auto_match(conn),
        within_tolerance=_within_tolerance(conn),
        taxes=_taxes(conn),
    )
