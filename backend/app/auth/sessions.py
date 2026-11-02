"""Server-side sessions and the CSRF token derived from each one."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, delete, insert, select

from app.auth.permissions import ROLE_PERMISSIONS, Permission
from app.config import get_settings
from app.enums import Role
from app.schema import sessions, users


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    name: str
    role: Role

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self.role]

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(conn: Connection, user_id: int) -> str:
    """Start a session and return its token. Only the token's hash is stored."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(hours=get_settings().session_ttl_hours)
    conn.execute(
        insert(sessions).values(user_id=user_id, token_hash=_hash(token), expires_at=expires_at)
    )
    return token


def lookup(conn: Connection, token: str) -> CurrentUser | None:
    row = conn.execute(
        select(users.c.id, users.c.email, users.c.name, users.c.role)
        .join(sessions, sessions.c.user_id == users.c.id)
        .where(
            sessions.c.token_hash == _hash(token),
            sessions.c.expires_at > datetime.now(UTC),
            users.c.is_active,
        )
    ).one_or_none()
    if row is None:
        return None
    return CurrentUser(id=row.id, email=row.email, name=row.name, role=Role(row.role))


def revoke(conn: Connection, token: str) -> None:
    conn.execute(delete(sessions).where(sessions.c.token_hash == _hash(token)))


def csrf_token_for(token: str) -> str:
    """Double-submit token bound to the session, so it can't be planted from another site."""
    secret = get_settings().session_secret.encode()
    return hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()
