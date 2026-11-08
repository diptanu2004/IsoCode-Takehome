"""What a person does with a claim, and the evidence pack sent to a partner (TDD §7.3).

Matching opens claims and closes them when money arrives. People raise them with the partner,
record what came back, and (co-founder only) write them off.
"""

import io
import zipfile
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Connection, and_, func, insert, select, update

from app import audit
from app.enums import ClaimStatus, ClaimType
from app.exports import to_csv
from app.schema import (
    claim_events,
    claim_orders,
    claims,
    order_recon,
    orders,
    partner_lines,
    partners,
    rate_cards,
    source_rows,
    uploads,
)

LIVE = [ClaimStatus.OPEN.value, ClaimStatus.RAISED.value, ClaimStatus.PARTNER_REJECTED.value]

# Days since the claim became actionable (TDD §8.4): key, first day, last day (None = no end).
AGE_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0-7", 0, 7),
    ("8-15", 8, 15),
    ("16-30", 16, 30),
    ("31-60", 31, 60),
    ("61+", 61, None),
)


def age_condition(bucket: str, as_of: date):
    """Claims whose age falls in the bucket. A claim not yet a day old counts as 0-7."""
    _, first, last = next(b for b in AGE_BUCKETS if b[0] == bucket)
    conditions = []
    if first > 0:
        conditions.append(claims.c.aging_from <= as_of - timedelta(days=first))
    if last is not None:
        conditions.append(claims.c.aging_from >= as_of - timedelta(days=last))
    return and_(*conditions)


TYPE_LABELS = {
    ClaimType.RETURN_NOT_RECEIVED: "Return not received",
    ClaimType.UNPAID_PAST_DUE: "Unpaid past due",
    ClaimType.SHORT_PAID: "Short-paid",
    ClaimType.FREIGHT_OVERCHARGED: "Freight overcharged",
    ClaimType.SYSTEMATIC_GAP: "Systematic small gap",
}

# Which actions each status allows, and where they lead.
_MOVES: dict[str, tuple[set[str], str]] = {
    "raise": ({"open", "partner_rejected"}, ClaimStatus.RAISED),
    "recover": (set(LIVE), ClaimStatus.RECOVERED),
    "reject": ({"raised"}, ClaimStatus.PARTNER_REJECTED),
    "write_off": (set(LIVE), ClaimStatus.WRITTEN_OFF),
}


_PAST_TENSE = {
    "raise": "raised",
    "recover": "marked recovered",
    "reject": "marked rejected",
    "write_off": "written off",
}


class ClaimActionError(ValueError):
    """The action doesn't fit the claim's state. The message is shown to the user."""


def rupees(paise: int | None) -> str:
    if paise is None:
        return ""
    sign = "-" if paise < 0 else ""
    return f"{sign}{abs(paise) // 100}.{abs(paise) % 100:02d}"


def act(
    conn: Connection,
    claim_id: int,
    action: str,
    user_id: int,
    partner_ref: str | None = None,
    recovered_paise: int | None = None,
    note: str | None = None,
) -> dict:
    claim = conn.execute(
        select(claims).where(claims.c.id == claim_id).with_for_update()
    ).one_or_none()
    if claim is None:
        raise LookupError("Claim not found.")
    allowed_from, to_status = _MOVES[action]
    if claim.status not in allowed_from:
        state = claim.status.replace("_", " ")
        raise ClaimActionError(f"A claim that is {state} can't be {_PAST_TENSE[action]}.")
    values: dict = {"status": to_status.value, "updated_at": datetime.now(UTC)}
    if action == "raise":
        values["partner_ref"] = partner_ref or claim.partner_ref
    if action == "recover":
        if recovered_paise is None or recovered_paise <= 0:
            raise ClaimActionError("Enter the amount recovered.")
        if recovered_paise > claim.amount_paise:
            raise ClaimActionError("The amount recovered is more than the claim.")
        values["recovered_paise"] = recovered_paise
    if to_status in (ClaimStatus.RECOVERED, ClaimStatus.WRITTEN_OFF):
        values |= {"closed_at": datetime.now(UTC), "closed_by": user_id}

    conn.execute(update(claims).where(claims.c.id == claim_id).values(**values))
    default_notes = {
        "raise": "Raised with the partner (sending is simulated in this build)",
        "recover": f"Recovered Rs {rupees(recovered_paise)} of Rs {rupees(claim.amount_paise)}",
        "reject": "The partner rejected the claim; it stays in money at risk",
        "write_off": "Written off: accepted as a loss",
    }
    conn.execute(
        insert(claim_events).values(
            claim_id=claim_id,
            actor_id=user_id,
            from_status=claim.status,
            to_status=to_status.value,
            note=note or default_notes[action],
        )
    )
    audit.record(
        conn,
        user_id,
        f"claim.{action}",
        "claim",
        claim_id,
        before={"status": claim.status, "recovered_paise": claim.recovered_paise},
        after={k: v for k, v in values.items() if k not in ("updated_at", "closed_at")},
    )
    return {"id": claim_id, "status": to_status.value}


def raise_all_open(conn: Connection, partner_id: int, user_id: int, partner_ref: str | None) -> int:
    ids = (
        conn.execute(
            select(claims.c.id).where(
                claims.c.partner_id == partner_id, claims.c.status == ClaimStatus.OPEN.value
            )
        )
        .scalars()
        .all()
    )
    for claim_id in ids:
        act(conn, claim_id, "raise", user_id, partner_ref=partner_ref)
    return len(ids)


def _claim_rows(conn: Connection, partner_id: int, statuses: list[str]):
    return conn.execute(
        select(
            claims.c.id,
            claims.c.type,
            claims.c.amount_paise,
            claims.c.recovered_paise,
            claims.c.status,
            claims.c.partner_ref,
            claims.c.aging_from,
            claims.c.component,
            claims.c.period_month,
            claims.c.order_id.label("order_pk"),
            orders.c.order_id,
            orders.c.order_date,
            orders.c.value_paise,
            orders.c.awb,
            order_recon.c.expected_paise,
            order_recon.c.received_paise,
            order_recon.c.diff_paise,
            order_recon.c.breakdown,
            order_recon.c.due_date,
            order_recon.c.channel_rate_card_id,
            order_recon.c.courier_rate_card_id,
        )
        .outerjoin(orders, orders.c.id == claims.c.order_id)
        .outerjoin(order_recon, order_recon.c.order_id == claims.c.order_id)
        .where(claims.c.partner_id == partner_id, claims.c.status.in_(statuses))
        .order_by(claims.c.type, claims.c.aging_from, claims.c.id)
    ).all()


def _breakdown_text(breakdown: dict | None) -> str:
    if not breakdown:
        return ""
    return "; ".join(
        f"{name.replace('_', ' ')}: expected {rupees(pair[0])}, got {rupees(pair[1])}"
        for name, pair in breakdown.items()
        if pair[0] != pair[1]
    )


def evidence_pack(
    conn: Connection, partner_id: int, statuses: list[str], today: date
) -> tuple[str, bytes]:
    """A ZIP a partner's dispute team can check line by line: each claim, how the expected
    amount was worked out, and the partner's own report rows behind it."""
    partner = conn.execute(select(partners.c.name).where(partners.c.id == partner_id)).scalar()
    if partner is None:
        raise LookupError("Partner not found.")
    rows = _claim_rows(conn, partner_id, statuses)
    card_versions = dict(conn.execute(select(rate_cards.c.id, rate_cards.c.version)).all())

    claim_csv = to_csv(
        [
            "Claim",
            "Problem",
            "Order ID",
            "AWB",
            "Order date",
            "Order value (Rs)",
            "Expected (Rs)",
            "Received (Rs)",
            "Difference (Rs)",
            "Claim amount (Rs)",
            "Due or found on",
            "Days open",
            "Status",
            "Your reference",
            "Where it differs",
            "Rate card version",
            "Month (systematic gaps)",
            "Component (systematic gaps)",
        ],
        (
            [
                r.id,
                TYPE_LABELS[ClaimType(r.type)],
                r.order_id,
                r.awb,
                r.order_date,
                rupees(r.value_paise),
                rupees(r.expected_paise),
                rupees(r.received_paise),
                rupees(r.diff_paise),
                rupees(r.amount_paise),
                r.aging_from,
                max(0, (today - r.aging_from).days),
                r.status.replace("_", " "),
                r.partner_ref,
                _breakdown_text(r.breakdown),
                ", ".join(
                    f"v{card_versions[c]}"
                    for c in (r.channel_rate_card_id, r.courier_rate_card_id)
                    if c in card_versions
                ),
                r.period_month.strftime("%b %Y") if r.period_month else "",
                (r.component or "").replace("_", " "),
            ]
            for r in rows
        ),
    )

    # The partner's own report lines behind each claim, with file and row to find them by.
    order_ids = {r.order_pk: r.id for r in rows if r.order_pk}
    systematic = {r.id for r in rows if r.type == ClaimType.SYSTEMATIC_GAP}
    if systematic:
        for claim_id, order_pk in conn.execute(
            select(claim_orders.c.claim_id, claim_orders.c.order_id).where(
                claim_orders.c.claim_id.in_(systematic)
            )
        ):
            order_ids.setdefault(order_pk, claim_id)
    lines = (
        conn.execute(
            select(
                partner_lines.c.order_id,
                orders.c.order_id.label("order_ref"),
                partner_lines.c.component,
                partner_lines.c.amount_paise,
                partner_lines.c.txn_date,
                partner_lines.c.reference,
                uploads.c.filename,
                partner_lines.c.row_no,
                source_rows.c.raw,
            )
            .join(orders, orders.c.id == partner_lines.c.order_id)
            .outerjoin(uploads, uploads.c.id == partner_lines.c.upload_id)
            .outerjoin(
                source_rows,
                (source_rows.c.upload_id == partner_lines.c.upload_id)
                & (source_rows.c.row_no == partner_lines.c.row_no),
            )
            .where(
                partner_lines.c.order_id.in_(list(order_ids)),
                partner_lines.c.partner_id == partner_id,
            )
            .order_by(orders.c.order_id, partner_lines.c.txn_date, partner_lines.c.row_no)
        ).all()
        if order_ids
        else []
    )
    evidence_csv = to_csv(
        [
            "Claim",
            "Order ID",
            "Component",
            "Amount (Rs)",
            "Date",
            "Reference",
            "Your file",
            "Row in your file",
            "Your row as received",
        ],
        (
            [
                order_ids[line.order_id],
                line.order_ref,
                line.component.replace("_", " "),
                rupees(line.amount_paise),
                line.txn_date,
                line.reference,
                line.filename,
                line.row_no,
                " | ".join(f"{k}: {v}" for k, v in (line.raw or {}).items()),
            ]
            for line in lines
        ),
    )

    total = sum(r.amount_paise - r.recovered_paise for r in rows)
    readme = (
        f"Claims from Company A to {partner}, prepared {today:%d %b %Y}.\n\n"
        f"{len(rows)} claims, Rs {rupees(total)} outstanding.\n\n"
        "claims.csv   one row per claim: what we expected, what we received, the difference,\n"
        "             and where it differs, component by component.\n"
        "evidence.csv the rows of your own reports behind each claim, with the file name and\n"
        "             row number, so each line can be found in your system.\n\n"
        "Amounts are in rupees. Expected amounts come from the rate card agreed with you and in\n"
        "force on the order date. TCS and TDS are statutory deductions: they are excluded from\n"
        "every difference and are not claimed.\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", readme)
        archive.writestr("claims.csv", claim_csv)
        archive.writestr("evidence.csv", evidence_csv)
    slug = partner.lower().replace(" ", "-")
    return f"claims-{slug}-{today:%Y%m%d}.zip", buffer.getvalue()


def claim_counts(conn: Connection) -> list:
    return conn.execute(
        select(
            claims.c.partner_id,
            partners.c.name.label("partner"),
            claims.c.status,
            func.count().label("claims"),
            func.sum(claims.c.amount_paise - claims.c.recovered_paise).label("outstanding_paise"),
            func.sum(claims.c.recovered_paise).label("recovered_paise"),
        )
        .join(partners, partners.c.id == claims.c.partner_id)
        .group_by(claims.c.partner_id, partners.c.name, claims.c.status)
        .order_by(partners.c.name)
    ).all()
