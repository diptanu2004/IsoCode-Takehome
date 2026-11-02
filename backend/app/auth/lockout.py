"""Login lockout: 5 failed attempts for an email from one IP in 15 minutes [A18]."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, func, insert, select

from app.schema import login_attempts

MAX_FAILURES = 5
WINDOW = timedelta(minutes=15)


def is_locked(conn: Connection, email: str, ip: str) -> bool:
    since = datetime.now(UTC) - WINDOW
    last_success = (
        select(func.max(login_attempts.c.at))
        .where(login_attempts.c.email == email, login_attempts.c.ip == ip)
        .where(login_attempts.c.succeeded)
        .scalar_subquery()
    )
    failures = conn.execute(
        select(func.count()).where(
            login_attempts.c.email == email,
            login_attempts.c.ip == ip,
            ~login_attempts.c.succeeded,
            login_attempts.c.at > since,
            login_attempts.c.at > func.coalesce(last_success, since),
        )
    ).scalar_one()
    return failures >= MAX_FAILURES


def record(conn: Connection, email: str, ip: str, succeeded: bool) -> None:
    conn.execute(insert(login_attempts).values(email=email, ip=ip, succeeded=succeeded))
