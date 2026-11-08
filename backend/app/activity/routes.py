"""The audit log: who changed what. Everyone sees their own actions; the co-founder sees all."""

from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Connection, func, select

from app.auth.deps import current_user
from app.auth.permissions import Permission
from app.auth.sessions import CurrentUser
from app.claims.service import TYPE_LABELS
from app.db import get_conn
from app.enums import ClaimType
from app.schema import audit_log, claims, orders, partners, rate_cards, sources, users

router = APIRouter(prefix="/api/audit", tags=["audit"])


class Entry(BaseModel):
    id: int
    at: datetime
    actor: str | None  # None = Settle itself
    action: str
    entity: str
    entity_id: str
    subject: str | None  # what was changed, in words: "Courier A", "ORD-M-80001 (Short-paid)"
    before: dict | None
    after: dict | None


class EntryPage(BaseModel):
    rows: list[Entry]
    total: int
    page: int
    page_size: int
    everyone: bool  # False: only the signed-in user's own actions


def _subjects(conn: Connection, rows) -> dict[tuple[str, str], str]:
    """A readable name for each changed thing, looked up per kind in one query each."""
    ids: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        if r.entity_id.isdigit():
            ids[r.entity].add(int(r.entity_id))
    found: dict[tuple[str, str], str] = {}

    def add(entity: str, query) -> None:
        if ids.get(entity):
            for key, name in conn.execute(query):
                found[(entity, str(key))] = name

    add("partner", select(partners.c.id, partners.c.name).where(partners.c.id.in_(ids["partner"])))
    add("source", select(sources.c.id, sources.c.name).where(sources.c.id.in_(ids["source"])))
    add("user", select(users.c.id, users.c.email).where(users.c.id.in_(ids["user"])))
    if ids.get("rate_card"):
        for r in conn.execute(
            select(rate_cards.c.id, partners.c.name, rate_cards.c.version)
            .join(partners, partners.c.id == rate_cards.c.partner_id)
            .where(rate_cards.c.id.in_(ids["rate_card"]))
        ):
            found[("rate_card", str(r.id))] = f"{r.name}, version {r.version}"
    if ids.get("claim"):
        for r in conn.execute(
            select(claims.c.id, claims.c.type, orders.c.order_id, partners.c.name)
            .join(partners, partners.c.id == claims.c.partner_id)
            .outerjoin(orders, orders.c.id == claims.c.order_id)
            .where(claims.c.id.in_(ids["claim"]))
        ):
            label = TYPE_LABELS[ClaimType(r.type)]
            found[("claim", str(r.id))] = f"{r.order_id or r.name} ({label})"
    return found


@router.get("")
def audit_entries(
    area: str | None = Query(None, pattern=r"^[a-z_]{2,30}$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(current_user),
    conn: Connection = Depends(get_conn, scope="function"),
) -> EntryPage:
    everyone = user.can(Permission.AUDIT_VIEW_ALL)
    conditions = [] if everyone else [audit_log.c.actor_id == user.id]
    if area:
        conditions.append(audit_log.c.entity == area)
    total = conn.execute(
        select(func.count()).select_from(audit_log).where(*conditions)
    ).scalar_one()
    rows = conn.execute(
        select(
            audit_log.c.id,
            audit_log.c.at,
            users.c.name.label("actor"),
            audit_log.c.action,
            audit_log.c.entity,
            audit_log.c.entity_id,
            audit_log.c.before,
            audit_log.c.after,
        )
        .outerjoin(users, users.c.id == audit_log.c.actor_id)
        .where(*conditions)
        .order_by(audit_log.c.at.desc(), audit_log.c.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    subjects = _subjects(conn, rows)
    return EntryPage(
        rows=[Entry(**r._mapping, subject=subjects.get((r.entity, r.entity_id))) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        everyone=everyone,
    )
