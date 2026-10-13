from collections.abc import Callable, Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from medical_archive.models import AccessGrant, User

OwnerVerification = Callable[[str, int], bool]


def visible_resource_ids(
    session: Session,
    *,
    user_id: int,
    resource_type: str,
    owned_resource_ids: Iterable[str | int],
) -> set[str]:
    """Return a user's owned resource IDs together with resources shared with them."""
    visible_ids = {str(resource_id) for resource_id in owned_resource_ids}
    granted_ids = session.scalars(
        select(AccessGrant.resource_id).where(
            AccessGrant.grantee_user_id == user_id,
            AccessGrant.resource_type == resource_type,
        )
    )
    visible_ids.update(granted_ids)
    return visible_ids


def can_access(
    session: Session,
    *,
    user_id: int,
    resource_type: str,
    resource_id: str | int,
    owner_user_id: int | None,
) -> bool:
    """Check owner or active recipient access using the trusted domain owner ID."""
    if owner_user_id is None:
        return False
    if user_id == owner_user_id:
        return True
    return (
        session.scalar(
            select(AccessGrant.id).where(
                AccessGrant.owner_user_id == owner_user_id,
                AccessGrant.grantee_user_id == user_id,
                AccessGrant.resource_type == resource_type,
                AccessGrant.resource_id == str(resource_id),
            )
        )
        is not None
    )


def require_owner(
    session: Session,
    *,
    user_id: int,
    resource_type: str,
    resource_id: str | int,
    owner_user_id: int | None,
) -> None:
    """Allow only the persisted resource owner; hide missing and unauthorized resources."""
    owner_exists = owner_user_id is not None and session.get(User, owner_user_id) is not None
    if not owner_exists or user_id != owner_user_id:
        raise HTTPException(status_code=404, detail="Resource not found.")


def create(
    session: Session,
    *,
    owner_user_id: int,
    grantee_user_id: int,
    resource_type: str,
    resource_id: str | int,
    verify_owner: OwnerVerification,
) -> AccessGrant:
    """Create an explicit grant only after the domain confirms the owner's resource."""
    normalized_resource_id = str(resource_id)
    if not resource_type or len(resource_type) > 100:
        raise HTTPException(status_code=422, detail="Invalid resource type.")
    if not normalized_resource_id or len(normalized_resource_id) > 255:
        raise HTTPException(status_code=422, detail="Invalid resource identifier.")
    if not verify_owner(normalized_resource_id, owner_user_id):
        raise HTTPException(status_code=404, detail="Resource not found.")
    if owner_user_id == grantee_user_id:
        raise HTTPException(status_code=422, detail="A resource cannot be shared with its owner.")
    if session.get(User, grantee_user_id) is None:
        raise HTTPException(status_code=404, detail="Recipient not found.")

    existing_grant = session.scalar(
        select(AccessGrant).where(
            AccessGrant.owner_user_id == owner_user_id,
            AccessGrant.grantee_user_id == grantee_user_id,
            AccessGrant.resource_type == resource_type,
            AccessGrant.resource_id == normalized_resource_id,
        )
    )
    if existing_grant is not None:
        return existing_grant

    grant = AccessGrant(
        owner_user_id=owner_user_id,
        grantee_user_id=grantee_user_id,
        resource_type=resource_type,
        resource_id=normalized_resource_id,
    )
    session.add(grant)
    session.commit()
    return grant


def revoke(session: Session, *, grant_id: int, owner_user_id: int) -> None:
    """Immediately remove a grant when requested by its original resource owner."""
    grant = session.get(AccessGrant, grant_id)
    if grant is None or grant.owner_user_id != owner_user_id:
        raise HTTPException(status_code=404, detail="Resource grant not found.")
    session.delete(grant)
    session.commit()
