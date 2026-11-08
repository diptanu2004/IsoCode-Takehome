"""People who can sign in. Only the co-founder manages them (TDD §12.1)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app import audit
from app.auth.deps import require
from app.auth.passwords import hash_password
from app.auth.permissions import Permission
from app.auth.sessions import CurrentUser
from app.db import get_conn
from app.enums import Role
from app.schema import sessions, users

router = APIRouter(prefix="/api/users", tags=["users"])
_can_manage = require(Permission.USER_MANAGE)


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: Role
    is_active: bool
    created_at: datetime


class NewUser(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    name: str = Field(min_length=1, max_length=80)
    role: Role
    password: str = Field(min_length=12, max_length=200)


class UserChange(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


_COLUMNS = (
    users.c.id,
    users.c.email,
    users.c.name,
    users.c.role,
    users.c.is_active,
    users.c.created_at,
)


@router.get("", dependencies=[Depends(_can_manage)])
def list_users(conn: Connection = Depends(get_conn, scope="function")) -> list[UserOut]:
    rows = conn.execute(select(*_COLUMNS).order_by(users.c.name)).all()
    return [UserOut.model_validate(row, from_attributes=True) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    body: NewUser,
    actor: CurrentUser = Depends(_can_manage),
    conn: Connection = Depends(get_conn, scope="function"),
) -> UserOut:
    email = body.email.strip().lower()
    try:
        with conn.begin_nested():
            row = conn.execute(
                insert(users)
                .values(
                    email=email,
                    name=body.name.strip(),
                    role=body.role.value,
                    password_hash=hash_password(body.password),
                )
                .returning(*_COLUMNS)
            ).one()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Someone already uses that email.") from exc
    # Never the password, not even hashed.
    audit.record(
        conn, actor.id, "user.create", "user", row.id, after={"email": email, "role": body.role}
    )
    return UserOut.model_validate(row, from_attributes=True)


@router.patch("/{user_id}")
def change_user(
    user_id: int,
    body: UserChange,
    actor: CurrentUser = Depends(_can_manage),
    conn: Connection = Depends(get_conn, scope="function"),
) -> UserOut:
    if user_id == actor.id:
        # Otherwise the last co-founder could lock everyone out of user management.
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't change your own role or access.")
    before = conn.execute(
        select(users.c.role, users.c.is_active).where(users.c.id == user_id).with_for_update()
    ).one_or_none()
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    changes = body.model_dump(exclude_none=True, mode="json")
    if changes:
        conn.execute(update(users).where(users.c.id == user_id).values(**changes))
        if changes.get("is_active") is False:
            conn.execute(delete(sessions).where(sessions.c.user_id == user_id))  # signed out now
        audit.record(
            conn,
            actor.id,
            "user.update",
            "user",
            user_id,
            before={k: getattr(before, k) for k in changes},
            after=changes,
        )
    row = conn.execute(select(*_COLUMNS).where(users.c.id == user_id)).one()
    return UserOut.model_validate(row, from_attributes=True)
