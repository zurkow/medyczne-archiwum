import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from fastapi.testclient import TestClient
from sqlalchemy import select

from medical_archive.auth.bootstrap import main as bootstrap_main
from medical_archive.auth.passwords import verify_password
from medical_archive.models import Invitation, LoginSession, User

PASSWORD = "correct horse battery"


def _login(client: TestClient, email: str, extract_csrf_token, password: str = PASSWORD):
    login_page = client.get("/auth/login")
    return client.post(
        "/auth/login",
        data={
            "email": email,
            "password": password,
            "csrf_token": extract_csrf_token(login_page.text),
        },
        follow_redirects=False,
    )


def test_login_sets_opaque_secure_cookie_and_logout_revokes_it(
    client, auth_db, admin_user, extract_csrf_token
):
    response = _login(client, admin_user.email, extract_csrf_token)

    assert response.status_code == 303
    session_token = response.cookies["session"]
    assert len(session_token) >= 40
    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "secure" in cookie_header
    assert "samesite=strict" in cookie_header
    assert "path=/" in cookie_header
    assert "max-age=43200" in cookie_header
    stored = auth_db.get(LoginSession, sha256(session_token.encode()).hexdigest())
    assert stored is not None
    assert stored.user_id == admin_user.id
    assert stored.csrf_token_hash

    client.cookies.update(response.cookies)
    assert client.get("/").status_code == 200
    csrf_token = extract_csrf_token(client.get("/").text)
    rejected_logout = client.post(
        "/auth/logout",
        data={"csrf_token": "invalid-token"},
        follow_redirects=False,
    )
    assert rejected_logout.status_code == 403
    still_authorized = client.get("/", follow_redirects=False)
    assert still_authorized.status_code == 200
    csrf_token = extract_csrf_token(still_authorized.text)
    logout = client.post(
        "/auth/logout",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert logout.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303
    auth_db.expire_all()
    assert auth_db.get(LoginSession, sha256(session_token.encode()).hexdigest()) is None


def test_invalid_login_uses_generic_message_and_does_not_authenticate(
    client, auth_db, admin_user, extract_csrf_token
):
    login_page = client.get("/auth/login")
    csrf_token = extract_csrf_token(login_page.text)
    wrong_password = client.post(
        "/auth/login",
        data={
            "email": admin_user.email,
            "password": "incorrect",
            "csrf_token": csrf_token,
        },
    )
    unknown_user = client.post(
        "/auth/login",
        data={
            "email": "unknown@example.test",
            "password": "incorrect",
            "csrf_token": csrf_token,
        },
    )

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.text == unknown_user.text
    assert "session" not in wrong_password.cookies
    assert auth_db.scalar(select(LoginSession).where(LoginSession.user_id.is_not(None))) is None


def test_login_rejects_missing_or_invalid_csrf_token(client, admin_user):
    client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={"email": admin_user.email, "password": PASSWORD},
    )

    assert response.status_code == 403
    assert "session" not in response.cookies


def test_password_verification_fails_closed_for_malformed_hash():
    assert not verify_password("not-an-argon2-hash", PASSWORD)


def test_protected_pages_redirect_and_openapi_remains_disabled(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    login_form = client.get("/auth/login")
    assert login_form.status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_non_admin_cannot_open_invitation_management(client, member_user, extract_csrf_token):
    login = _login(client, member_user.email, extract_csrf_token)
    assert login.status_code == 303
    client.cookies.update(login.cookies)

    response = client.get("/auth/invite")

    assert response.status_code == 403


def test_admin_invitation_registers_non_admin_once_and_persists_only_hash(
    app, client, auth_db, admin_user, extract_csrf_token
):
    login = _login(client, admin_user.email, extract_csrf_token)
    assert login.status_code == 303
    client.cookies.update(login.cookies)

    invite_page = client.get("/auth/invite")
    rejected_invite = client.post(
        "/auth/invite",
        data={"csrf_token": "invalid-token"},
        follow_redirects=False,
    )
    assert rejected_invite.status_code == 403
    invite_response = client.post(
        "/auth/invite",
        data={"csrf_token": extract_csrf_token(invite_page.text)},
        follow_redirects=False,
    )
    assert invite_response.status_code == 200
    # The one-time invitation URL is rendered separately from the CSRF form token.
    token_match = re.search(r"/auth/register\?token=([A-Za-z0-9_-]+)", invite_response.text)
    assert token_match is not None
    invitation_token = token_match.group(1)
    stored_invitation = auth_db.get(Invitation, sha256(invitation_token.encode()).hexdigest())
    assert stored_invitation is not None
    assert stored_invitation.token_hash != invitation_token

    registration_client = TestClient(app, base_url="https://testserver")
    registration_page = registration_client.get(f"/auth/register?token={invitation_token}")
    assert registration_page.status_code == 200
    assert registration_client.get("/", follow_redirects=False).status_code == 303
    registration_page = registration_client.get(f"/auth/register?token={invitation_token}")
    rejected_registration = registration_client.post(
        "/auth/register",
        data={
            "token": invitation_token,
            "email": "new-member@example.test",
            "password": "another correct horse",
            "csrf_token": "invalid-token",
        },
        follow_redirects=False,
    )
    assert rejected_registration.status_code == 403
    registration = registration_client.post(
        "/auth/register",
        data={
            "token": invitation_token,
            "email": "new-member@example.test",
            "password": "another correct horse",
            "csrf_token": extract_csrf_token(registration_page.text),
        },
        follow_redirects=False,
    )
    assert registration.status_code == 303

    new_user = auth_db.scalar(select(User).where(User.email == "new-member@example.test"))
    assert new_user is not None
    assert not new_user.is_admin
    auth_db.expire_all()
    stored_invitation = auth_db.get(Invitation, sha256(invitation_token.encode()).hexdigest())
    assert stored_invitation.used_at is not None

    reuse_page = registration_client.get(f"/auth/register?token={invitation_token}")
    assert reuse_page.status_code == 400


def test_expired_invitation_cannot_be_used(client, auth_db, admin_user, extract_csrf_token):
    login = _login(client, admin_user.email, extract_csrf_token)
    client.cookies.update(login.cookies)
    invite_page = client.get("/auth/invite")
    invitation_response = client.post(
        "/auth/invite",
        data={"csrf_token": extract_csrf_token(invite_page.text)},
    )
    token = re.search(r"/auth/register\?token=([A-Za-z0-9_-]+)", invitation_response.text).group(1)
    invitation = auth_db.get(Invitation, sha256(token.encode()).hexdigest())
    invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    auth_db.commit()

    response = client.get(f"/auth/register?token={token}")

    assert response.status_code == 400


def test_bootstrap_reads_password_without_echo_and_refuses_second_admin(
    auth_engine, auth_session_factory, monkeypatch, capsys
):
    monkeypatch.setattr("getpass.getpass", lambda _prompt: PASSWORD)
    monkeypatch.setenv(
        "MEDICAL_ARCHIVE_DATABASE_URL",
        auth_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("MEDICAL_ARCHIVE_SESSION_SECRET", "s" * 32)

    bootstrap_main(["--email", "first-admin@example.test"])
    output = capsys.readouterr().out

    assert "Administrator created" in output
    assert PASSWORD not in output
    with auth_session_factory() as database:
        user = database.scalar(select(User).where(User.email == "first-admin@example.test"))
        assert user is not None
        assert user.is_admin

    try:
        bootstrap_main(["--email", "second-admin@example.test"])
    except SystemExit as error:
        assert error.code != 0
    else:
        raise AssertionError("Bootstrap must refuse to create a second administrator.")
    with auth_session_factory() as database:
        first_admin = database.scalar(select(User).where(User.is_admin.is_(True)))
        assert first_admin is not None
        assert first_admin.email == "first-admin@example.test"
