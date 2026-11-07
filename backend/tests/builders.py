"""Builders for hand-made orders, partner lines and scans, shared by the matching tests."""

import uuid
from datetime import date, timedelta

from sqlalchemy import insert, select

from app.schema import claims, order_recon, orders, partner_lines, partners, return_scans

AS_OF = date(2026, 11, 3)
DELIVERED = AS_OF - timedelta(days=20)  # long past every partner's settlement cycle


def partner_id(conn, name: str) -> int:
    return conn.execute(select(partners.c.id).where(partners.c.name == name)).scalar_one()


def add_order(
    conn,
    order_id: str,
    channel: str = "Marketplace 1",
    value: int = 119900,
    status: str = "delivered",
    mode: str = "prepaid",
    weight: int = 400,
    courier: str | None = None,
    zone: str | None = None,
    order_date: date = DELIVERED - timedelta(days=3),
    delivered: date | None = DELIVERED,
    returned: date | None = None,
) -> int:
    return conn.execute(
        insert(orders)
        .values(
            order_id=order_id,
            channel_partner_id=partner_id(conn, channel),
            courier_partner_id=partner_id(conn, courier) if courier else None,
            awb=f"AWB-{order_id}" if courier else None,
            zone=zone,
            order_date=order_date,
            value_paise=value,
            payment_mode=mode,
            declared_weight_g=weight,
            delivery_status=status,
            delivered_at=delivered,
            returned_at=returned,
        )
        .returning(orders.c.id)
    ).scalar_one()


def add_lines(conn, order: int | None, partner: str, on: date = DELIVERED, **amounts) -> None:
    for component, amount in amounts.items():
        conn.execute(
            insert(partner_lines).values(
                upload_id=0,
                row_no=1,
                partner_id=partner_id(conn, partner),
                order_id=order,
                component=component,
                amount_paise=amount,
                txn_date=on,
                line_key=str(uuid.uuid4()),
            )
        )


def m1_paid(conn, order: int, gap: int = 0, **extra) -> None:
    """Marketplace 1's settlement of the ₹1,199 order: ₹899 expected, plus or minus a gap."""
    add_lines(
        conn,
        order,
        "Marketplace 1",
        sale=119900,
        commission=-18000 + gap,
        fixed_fee=-3000,
        shipping_fee=-9000,
        **extra,
    )


def result(conn, order: int):
    return conn.execute(select(order_recon).where(order_recon.c.order_id == order)).one()


def live_claims(conn, order: int) -> dict[str, int]:
    rows = conn.execute(
        select(claims.c.type, claims.c.amount_paise).where(
            claims.c.order_id == order, claims.c.status.in_(["open", "raised", "partner_rejected"])
        )
    ).all()
    return {r.type: r.amount_paise for r in rows}


def add_scan(conn, order: int | None, on: date) -> None:
    conn.execute(
        insert(return_scans).values(
            upload_id=0,
            row_no=1,
            order_id=order,
            order_ref="X",
            scanned_at=f"{on.isoformat()}T12:00:00+05:30",
            line_key=str(uuid.uuid4()),
        )
    )
