import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import insert, literal, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from medical_archive.auth.passwords import hash_password, verify_password
from medical_archive.models import Invitation, LoginSession, User

SESSION_LIFETIME = timedelta(hours=12)
PREAUTH_LIFETIME = timedelta(minutes=10)
INVITATION_LIFETIME = timedelta(days=1)
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-account-password")


class AuthorizationError(Exception):
    """Raised when an account lacks permission for an operation."""


class InvalidInvitationError(Exception):
    """Raised when an invitation is invalid, expired, or already consumed."""


class DuplicateEmailError(Exception):
    """Raised when an account already exists for an email address."""


class BootstrapAlreadyComplete(Exception):
    """Raised when an initial administrator already exists."""


def token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def validate_email(email: str) -> str:
    normalized = email.strip().lower()
    if len(normalized) > 320 or not _EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Enter a valid email address.")
    return normalized


def validate_password(password: str) -> None:
    if not 12 <= len(password) <= 1024:
        raise ValueError("Password must contain between 12 and 1024 characters.")


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def create_preauth_session(
    database: Session,
    *,
    purpose: str,
    now: datetime | None = None,
) -> tuple[str, str, LoginSession]:
    if purpose not in {"login", "registration"}:
        raise ValueError("Pre-authentication sessions require a form purpose.")
    issued = now or datetime.now(UTC)
    session_token = secrets.token_urlsafe(32)
    csrf_token = new_csrf_token()
    record = LoginSession(
        token_hash=token_hash(session_token),
        user_id=None,
        purpose=purpose,
        expires_at=issued + PREAUTH_LIFETIME,
        csrf_token_hash=token_hash(csrf_token),
    )
    database.add(record)
    database.commit()
    return session_token, csrf_token, record


def find_valid_session(
    database: Session,
    raw_token: str | None,
    *,
    allow_preauth: bool = False,
    purpose: str | None = None,
    now: datetime | None = None,
) -> LoginSession | None:
    if not raw_token:
        return None
    conditions = [
        LoginSession.token_hash == token_hash(raw_token),
        LoginSession.expires_at > (now or datetime.now(UTC)),
    ]
    if purpose is not None:
        conditions.append(LoginSession.purpose == purpose)
    record = database.scalar(select(LoginSession).where(*conditions))
    if record is None:
        return None
    if record.user_id is None and not allow_preauth:
        return None
    return record


def verify_csrf(record: LoginSession, submitted_token: str | None) -> bool:
    if not submitted_token:
        return False
    return hmac.compare_digest(record.csrf_token_hash, token_hash(submitted_token))


def rotate_csrf(record: LoginSession) -> str:
    csrf_token = new_csrf_token()
    record.csrf_token_hash = token_hash(csrf_token)
    return csrf_token


def authenticate_user(database: Session, email: str, password: str) -> User | None:
    try:
        normalized_email = validate_email(email)
    except ValueError:
        verify_password(_DUMMY_PASSWORD_HASH, password)
        return None
    user = database.scalar(select(User).where(User.email == normalized_email))
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    if not verify_password(password_hash, password):
        return None
    return user


def start_authenticated_session(
    database: Session,
    *,
    preauth_session: LoginSession,
    user: User,
    now: datetime | None = None,
) -> tuple[str, str]:
    issued = now or datetime.now(UTC)
    raw_token = secrets.token_urlsafe(32)
    csrf_token = new_csrf_token()
    database.delete(preauth_session)
    database.add(
        LoginSession(
            token_hash=token_hash(raw_token),
            user_id=user.id,
            purpose="authenticated",
            expires_at=issued + SESSION_LIFETIME,
            csrf_token_hash=token_hash(csrf_token),
        )
    )
    database.commit()
    return raw_token, csrf_token


def revoke_session(database: Session, record: LoginSession) -> None:
    database.delete(record)
    database.commit()


def create_invitation(
    database: Session,
    *,
    creator: User,
    now: datetime | None = None,
) -> tuple[str, Invitation]:
    if not creator.is_admin:
        raise AuthorizationError
    issued = now or datetime.now(UTC)
    raw_token = secrets.token_urlsafe(32)
    invitation = Invitation(
        token_hash=token_hash(raw_token),
        expires_at=issued + INVITATION_LIFETIME,
        created_by_user_id=creator.id,
    )
    database.add(invitation)
    database.commit()
    return raw_token, invitation


def find_valid_invitation(
    database: Session,
    raw_token: str | None,
    *,
    now: datetime | None = None,
) -> Invitation | None:
    if not raw_token:
        return None
    return database.scalar(
        select(Invitation).where(
            Invitation.token_hash == token_hash(raw_token),
            Invitation.used_at.is_(None),
            Invitation.expires_at > (now or datetime.now(UTC)),
        )
    )


def register_with_invitation(
    database: Session,
    *,
    raw_token: str,
    email: str,
    password: str,
    now: datetime | None = None,
) -> User:
    normalized_email = validate_email(email)
    validate_password(password)
    issued = now or datetime.now(UTC)
    password_hash = hash_password(password)
    try:
        result = database.execute(
            update(Invitation)
            .where(
                Invitation.token_hash == token_hash(raw_token),
                Invitation.used_at.is_(None),
                Invitation.expires_at > issued,
            )
            .values(used_at=issued)
        )
        if result.rowcount != 1:
            database.rollback()
            raise InvalidInvitationError
        user = User(
            email=normalized_email,
            password_hash=password_hash,
            is_admin=False,
        )
        database.add(user)
        database.flush()
        database.commit()
        return user
    except IntegrityError as error:
        database.rollback()
        if "users.email" in str(error).lower() or "ix_users_email_lower" in str(error):
            raise DuplicateEmailError from error
        raise
    except Exception:
        database.rollback()
        raise


def create_first_admin(database: Session, *, email: str, password: str) -> User:
    normalized_email = validate_email(email)
    validate_password(password)
    password_hash = hash_password(password)
    inserted = database.execute(
        insert(User).from_select(
            ["email", "password_hash", "is_admin"],
            select(literal(normalized_email), literal(password_hash), literal(True)).where(
                ~select(User.id).exists()
            ),
        )
    )
    if inserted.rowcount != 1:
        database.rollback()
        raise BootstrapAlreadyComplete
    user = database.scalar(select(User).where(User.email == normalized_email))
    if user is None:
        database.rollback()
        raise RuntimeError("Administrator bootstrap did not persist the new user.")
    database.commit()
    return user
