from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.auth import lockout
from app.auth.deps import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from app.db import get_engine
from app.enums import Role
from app.schema import sessions


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_login_sets_cookies_and_returns_permissions(client, make_user):
    user = make_user(Role.EXEC)
    response = _login(client, user["email"], user["password"])

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "exec"
    assert "claim.work" in body["permissions"]
    assert "claim.write_off" not in body["permissions"]
    assert SESSION_COOKIE in client.cookies
    assert CSRF_COOKIE in client.cookies
    assert "httponly" in response.headers["set-cookie"].lower()


def test_me_needs_a_session(client):
    assert client.get("/api/me").status_code == 401


def test_me_after_login(client, make_user):
    user = make_user(Role.COFOUNDER)
    _login(client, user["email"], user["password"])
    assert client.get("/api/me").json()["email"] == user["email"]


def test_email_is_case_insensitive(client, make_user):
    user = make_user(Role.EXEC, email="priya@example.test")
    assert _login(client, "  Priya@Example.TEST ", user["password"]).status_code == 200


def test_wrong_password_and_unknown_email_look_the_same(client, make_user):
    user = make_user(Role.EXEC)
    wrong = _login(client, user["email"], "nope")
    unknown = _login(client, "nobody@example.test", "nope")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_inactive_user_cannot_sign_in(client, make_user):
    user = make_user(Role.EXEC, active=False)
    assert _login(client, user["email"], user["password"]).status_code == 401


def test_lockout_after_repeated_failures(client, make_user):
    user = make_user(Role.EXEC)
    for _ in range(lockout.MAX_FAILURES):
        assert _login(client, user["email"], "wrong").status_code == 401
    # Even the right password is refused while locked.
    assert _login(client, user["email"], user["password"]).status_code == 429


def test_success_resets_the_failure_count(client, make_user):
    user = make_user(Role.EXEC)
    for _ in range(lockout.MAX_FAILURES - 1):
        _login(client, user["email"], "wrong")
    assert _login(client, user["email"], user["password"]).status_code == 200
    for _ in range(lockout.MAX_FAILURES - 1):
        _login(client, user["email"], "wrong")
    assert _login(client, user["email"], user["password"]).status_code == 200


def test_state_changing_requests_need_the_csrf_header(client, make_user):
    user = make_user(Role.EXEC)
    _login(client, user["email"], user["password"])

    assert client.post("/api/auth/logout").status_code == 403
    client.headers[CSRF_HEADER] = "forged"
    assert client.post("/api/auth/logout").status_code == 403


def test_logout_revokes_the_session(client, make_user):
    user = make_user(Role.EXEC)
    _login(client, user["email"], user["password"])
    token = client.cookies[SESSION_COOKIE]
    client.headers[CSRF_HEADER] = client.cookies[CSRF_COOKIE]

    assert client.post("/api/auth/logout").status_code == 204
    # Replaying the old cookie must not work.
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/me").status_code == 401


def test_expired_session_is_refused(client, make_user):
    user = make_user(Role.EXEC)
    _login(client, user["email"], user["password"])
    with get_engine().begin() as conn:
        conn.execute(update(sessions).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    assert client.get("/api/me").status_code == 401


def test_login_input_is_validated(client):
    assert _login(client, "not-an-email", "x").status_code == 422
    assert _login(client, "a@b.test", "").status_code == 422
    assert _login(client, "a@b.test", "x" * 300).status_code == 422
