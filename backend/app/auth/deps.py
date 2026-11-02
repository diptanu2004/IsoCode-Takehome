"""FastAPI dependencies that authenticate the request and check permissions."""

import hmac
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import Connection

from app.auth import sessions
from app.auth.permissions import Permission
from app.auth.sessions import CurrentUser
from app.db import get_conn

SESSION_COOKIE = "settle_session"
CSRF_COOKIE = "settle_csrf"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def current_user(request: Request, conn: Connection = Depends(get_conn)) -> CurrentUser:
    token = request.cookies.get(SESSION_COOKIE)
    user = sessions.lookup(conn, token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")

    if request.method not in _SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent, sessions.csrf_token_for(token)):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token.")
    return user


def require(permission: Permission) -> Callable[..., CurrentUser]:
    def dependency(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not user.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role can't do this.")
        return user

    # Read by the access-control tests to build the route × role matrix.
    dependency.permission = permission  # type: ignore[attr-defined]
    return dependency
