from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import Connection, select

from app.auth import lockout, sessions
from app.auth.deps import CSRF_COOKIE, SESSION_COOKIE, current_user
from app.auth.passwords import verify_password
from app.auth.permissions import Permission
from app.auth.sessions import CurrentUser
from app.config import get_settings
from app.db import get_conn
from app.enums import Role
from app.schema import users

router = APIRouter(prefix="/api", tags=["auth"])


Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+$",
    ),
]


class LoginIn(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=256)


class MeOut(BaseModel):
    id: int
    email: str
    name: str
    role: Role
    permissions: list[Permission]


def _me(user: CurrentUser) -> MeOut:
    return MeOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        permissions=sorted(user.permissions),
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/auth/login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    conn: Connection = Depends(get_conn, scope="function"),
) -> MeOut:
    email = body.email
    ip = _client_ip(request)
    if lockout.is_locked(conn, email, ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed attempts. Try again in 15 minutes.",
        )

    row = conn.execute(select(users).where(users.c.email == email)).one_or_none()
    ok = verify_password(row.password_hash if row else None, body.password)
    ok = ok and row is not None and row.is_active
    lockout.record(conn, email, ip, ok)
    if not ok:
        conn.commit()  # keep the failed attempt even though the request errors
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect.")

    token = sessions.create(conn, row.id)
    settings = get_settings()
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    # Readable by the frontend, which echoes it in the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE,
        sessions.csrf_token_for(token),
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return _me(CurrentUser(id=row.id, email=row.email, name=row.name, role=Role(row.role)))


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    _: CurrentUser = Depends(current_user),
    conn: Connection = Depends(get_conn, scope="function"),
) -> None:
    sessions.revoke(conn, request.cookies[SESSION_COOKIE])
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)) -> MeOut:
    return _me(user)
