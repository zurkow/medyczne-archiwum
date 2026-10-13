from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from medical_archive.auth.dependencies import (
    get_authenticated_session,
    get_current_user,
    require_resource_owner,
)
from medical_archive.auth.grants import (
    GrantIndexSyncError,
    create,
    revoke,
)
from medical_archive.auth.service import verify_csrf
from medical_archive.db import get_session
from medical_archive.models import AccessGrant, LoginSession, User

OwnerLookup = Callable[[Session, str], int | None]
GrantRouteHook = Callable[[Request, Session, AccessGrant], None]


class GrantRequest(BaseModel):
    grantee_user_id: int = Field(gt=0)


def _require_csrf(login_session: LoginSession, request: Request) -> None:
    if not verify_csrf(login_session, request.headers.get("x-csrf-token")):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")


def create_grant_router(
    *,
    resource_type: str,
    get_owner_id: OwnerLookup,
    on_grant: GrantRouteHook | None = None,
    on_revoke: GrantRouteHook | None = None,
) -> APIRouter:
    """Build grant-management routes for a domain using its trusted owner lookup."""
    if not resource_type or len(resource_type) > 100:
        raise ValueError("Resource type must contain between 1 and 100 characters.")

    router = APIRouter(tags=[f"{resource_type}-grants"])
    require_owner = require_resource_owner(
        resource_type=resource_type,
        get_owner_id=get_owner_id,
    )

    @router.post(
        "/{resource_id}/grants",
        status_code=status.HTTP_201_CREATED,
    )
    def create_resource_grant(
        resource_id: str,
        body: GrantRequest,
        request: Request,
        user: Annotated[User, Depends(get_current_user)],
        login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
        session: Annotated[Session, Depends(get_session)],
        _owner_user_id: Annotated[int, Depends(require_owner)],
    ) -> dict[str, int | str]:
        _require_csrf(login_session, request)
        try:
            grant = create(
                session,
                owner_user_id=user.id,
                grantee_user_id=body.grantee_user_id,
                resource_type=resource_type,
                resource_id=resource_id,
                verify_owner=lambda candidate_id, candidate_user_id: (
                    get_owner_id(session, candidate_id) == candidate_user_id
                ),
                on_grant=(
                    (lambda active_grant: on_grant(request, session, active_grant))
                    if on_grant is not None
                    else None
                ),
            )
        except GrantIndexSyncError as error:
            raise HTTPException(
                status_code=503,
                detail="The resource grant could not be applied. Please try again.",
            ) from error
        return {
            "id": grant.id,
            "resource_id": grant.resource_id,
            "grantee_user_id": grant.grantee_user_id,
        }

    @router.delete(
        "/{resource_id}/grants/{grantee_user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def revoke_resource_grant(
        resource_id: str,
        grantee_user_id: int,
        request: Request,
        login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
        session: Annotated[Session, Depends(get_session)],
        owner_user_id: Annotated[int, Depends(require_owner)],
    ) -> None:
        _require_csrf(login_session, request)
        grant = session.scalar(
            select(AccessGrant).where(
                AccessGrant.owner_user_id == owner_user_id,
                AccessGrant.grantee_user_id == grantee_user_id,
                AccessGrant.resource_type == resource_type,
                AccessGrant.resource_id == resource_id,
            )
        )
        if grant is None:
            raise HTTPException(status_code=404, detail="Resource grant not found.")
        try:
            revoke(
                session,
                grant_id=grant.id,
                owner_user_id=owner_user_id,
                on_revoke=(
                    (lambda active_grant: on_revoke(request, session, active_grant))
                    if on_revoke is not None
                    else None
                ),
            )
        except GrantIndexSyncError as error:
            raise HTTPException(
                status_code=503,
                detail="The resource grant could not be revoked. Please try again.",
            ) from error

    return router
