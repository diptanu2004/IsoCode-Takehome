"""Route × role matrix, built from the routes so new endpoints are covered automatically."""

import re

import pytest
from fastapi.routing import APIRoute

from app.auth.deps import current_user
from app.auth.permissions import ROLE_PERMISSIONS, Permission
from app.enums import Role
from app.main import API_ROUTERS, app

# Routes anyone may call without signing in.
PUBLIC = {("GET", "/api/health"), ("POST", "/api/auth/login")}
CATCH_ALL = "/api/{path:path}"


def _api_routes() -> list[tuple[str, APIRoute]]:
    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    routes += [r for router in API_ROUTERS for r in router.routes if isinstance(r, APIRoute)]
    return [
        (method, route)
        for route in routes
        if route.path.startswith("/api/") and route.path != CATCH_ALL
        for method in sorted(route.methods)
    ]


def _guards(route: APIRoute) -> tuple[bool, set[Permission]]:
    """Whether the route authenticates, and which permissions it requires."""
    authenticated, permissions = False, set()
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        authenticated |= dep.call is current_user
        if permission := getattr(dep.call, "permission", None):
            permissions.add(permission)
        stack.extend(dep.dependencies)
    return authenticated, permissions


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


PROTECTED = [(method, route, perm) for method, route in _api_routes() for perm in _guards(route)[1]]


@pytest.mark.parametrize(
    ("method", "route"), _api_routes(), ids=lambda v: v if isinstance(v, str) else v.path
)
def test_every_api_route_requires_sign_in(client, method, route):
    if (method, route.path) in PUBLIC:
        return
    authenticated, _ = _guards(route)
    assert authenticated, f"{method} {route.path} does not check the session"
    assert client.request(method, _url(route.path)).status_code == 401


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(
    ("method", "route", "permission"),
    PROTECTED,
    ids=lambda v: v if isinstance(v, str) else getattr(v, "path", None),
)
def test_permission_matrix(signed_in, role, method, route, permission):
    client = signed_in(role)
    status = client.request(method, _url(route.path), json={}).status_code
    if permission in ROLE_PERMISSIONS[role]:
        assert status not in (401, 403), f"{role} should be allowed {method} {route.path}"
    else:
        assert status == 403, f"{role} should be refused {method} {route.path}"


def test_cofounder_can_do_everything_exec_can():
    assert ROLE_PERMISSIONS[Role.EXEC] < ROLE_PERMISSIONS[Role.COFOUNDER]


@pytest.mark.parametrize(
    "permission",
    [
        Permission.CLAIM_WRITE_OFF,
        Permission.RATE_CARD_EDIT,
        Permission.SETTINGS_EDIT,
        Permission.USER_MANAGE,
        Permission.UPLOAD_VOID_ANY,
        Permission.AUDIT_VIEW_ALL,
    ],
)
def test_exec_lacks_cofounder_only_permissions(permission):
    assert permission not in ROLE_PERMISSIONS[Role.EXEC]


def test_unknown_api_path_is_json_404(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found."}
