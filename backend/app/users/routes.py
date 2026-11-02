from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Connection, select

from app.auth.deps import require
from app.auth.permissions import Permission
from app.db import get_conn
from app.enums import Role
from app.schema import users

router = APIRouter(prefix="/api/users", tags=["users"])


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: Role
    is_active: bool
    created_at: datetime


@router.get("", dependencies=[Depends(require(Permission.USER_MANAGE))])
def list_users(conn: Connection = Depends(get_conn)) -> list[UserOut]:
    rows = conn.execute(
        select(
            users.c.id,
            users.c.email,
            users.c.name,
            users.c.role,
            users.c.is_active,
            users.c.created_at,
        ).order_by(users.c.name)
    ).all()
    return [UserOut.model_validate(row, from_attributes=True) for row in rows]
