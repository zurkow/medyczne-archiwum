from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from medical_archive.auth.grants import (
    can_access,
    create,
    require_owner,
    revoke,
    visible_resource_ids,
)
from medical_archive.models import AccessGrant, User


@pytest.fixture
def owner_user(auth_db: Session) -> User:
    owner = User(
        email="owner@example.test",
        password_hash="not-used-in-this-test",
        is_admin=False,
    )
    auth_db.add(owner)
    auth_db.commit()
    return owner


@pytest.fixture
def recipient_user(auth_db: Session) -> User:
    recipient = User(
        email="recipient@example.test",
        password_hash="not-used-in-this-test",
        is_admin=False,
    )
    auth_db.add(recipient)
    auth_db.commit()
    return recipient


@pytest.fixture
def private_resource(owner_user: User):
    return SimpleNamespace(id="private-report-42", owner_user_id=owner_user.id)


def test_only_trusted_owner_can_access_private_resource(
    auth_db: Session,
    owner_user: User,
    admin_user: User,
    member_user: User,
    private_resource,
):
    assert can_access(
        auth_db,
        user_id=owner_user.id,
        resource_type="test",
        resource_id=private_resource.id,
        owner_user_id=private_resource.owner_user_id,
    )
    assert not can_access(
        auth_db,
        user_id=admin_user.id,
        resource_type="test",
        resource_id=private_resource.id,
        owner_user_id=private_resource.owner_user_id,
    )
    assert not can_access(
        auth_db,
        user_id=member_user.id,
        resource_type="test",
        resource_id=private_resource.id,
        owner_user_id=private_resource.owner_user_id,
    )
    assert not can_access(
        auth_db,
        user_id=owner_user.id,
        resource_type="test",
        resource_id="missing-resource",
        owner_user_id=None,
    )

    require_owner(
        auth_db,
        user_id=owner_user.id,
        resource_type="test",
        resource_id=private_resource.id,
        owner_user_id=private_resource.owner_user_id,
    )
    for unauthorized_user in (admin_user, member_user):
        with pytest.raises(HTTPException) as error:
            require_owner(
                auth_db,
                user_id=unauthorized_user.id,
                resource_type="test",
                resource_id=private_resource.id,
                owner_user_id=private_resource.owner_user_id,
            )
        assert error.value.status_code == 404

    with pytest.raises(HTTPException) as missing_resource_error:
        require_owner(
            auth_db,
            user_id=owner_user.id,
            resource_type="test",
            resource_id="missing-resource",
            owner_user_id=None,
        )
    assert missing_resource_error.value.status_code == 404


def test_owner_can_grant_access_and_revoke_it_immediately(
    auth_db: Session,
    owner_user: User,
    recipient_user: User,
    private_resource,
):
    assert (
        visible_resource_ids(
            auth_db,
            user_id=recipient_user.id,
            resource_type="test",
            owned_resource_ids=[],
        )
        == set()
    )

    grant = create(
        auth_db,
        owner_user_id=owner_user.id,
        grantee_user_id=recipient_user.id,
        resource_type="test",
        resource_id=private_resource.id,
        verify_owner=lambda resource_id, user_id: (
            resource_id == private_resource.id and user_id == owner_user.id
        ),
    )

    assert grant.id is not None
    assert can_access(
        auth_db,
        user_id=recipient_user.id,
        resource_type="test",
        resource_id=private_resource.id,
        owner_user_id=owner_user.id,
    )
    assert visible_resource_ids(
        auth_db,
        user_id=recipient_user.id,
        resource_type="test",
        owned_resource_ids=[],
    ) == {private_resource.id}
    assert visible_resource_ids(
        auth_db,
        user_id=owner_user.id,
        resource_type="test",
        owned_resource_ids=[private_resource.id, "another-owned-resource"],
    ) == {private_resource.id, "another-owned-resource"}

    revoke(auth_db, grant_id=grant.id, owner_user_id=owner_user.id)

    assert not can_access(
        auth_db,
        user_id=recipient_user.id,
        resource_type="test",
        resource_id=private_resource.id,
        owner_user_id=owner_user.id,
    )
    assert (
        visible_resource_ids(
            auth_db,
            user_id=recipient_user.id,
            resource_type="test",
            owned_resource_ids=[],
        )
        == set()
    )


def test_grant_creation_rechecks_owner_and_recipient_cannot_reshare(
    auth_db: Session,
    owner_user: User,
    recipient_user: User,
    private_resource,
):
    with pytest.raises(HTTPException) as error:
        create(
            auth_db,
            owner_user_id=owner_user.id,
            grantee_user_id=recipient_user.id,
            resource_type="test",
            resource_id=private_resource.id,
            verify_owner=lambda _resource_id, _user_id: False,
        )
    assert error.value.status_code == 404
    assert auth_db.scalar(select(AccessGrant)) is None

    grant = create(
        auth_db,
        owner_user_id=owner_user.id,
        grantee_user_id=recipient_user.id,
        resource_type="test",
        resource_id=private_resource.id,
        verify_owner=lambda resource_id, user_id: (
            resource_id == private_resource.id and user_id == owner_user.id
        ),
    )

    with pytest.raises(HTTPException) as error:
        create(
            auth_db,
            owner_user_id=recipient_user.id,
            grantee_user_id=owner_user.id,
            resource_type="test",
            resource_id=private_resource.id,
            verify_owner=lambda resource_id, user_id: (
                resource_id == private_resource.id and user_id == owner_user.id
            ),
        )
    assert error.value.status_code == 404

    with pytest.raises(HTTPException) as error:
        revoke(auth_db, grant_id=grant.id, owner_user_id=recipient_user.id)
    assert error.value.status_code == 404
