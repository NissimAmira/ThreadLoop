"""Unit tests for the discriminated-union invariants on `Session`.

`Session` serves both the happy path (`link_required=False` → access JWT +
user) and the pending-link path (`link_required=True` → link_token +
link_provider). The wrong combination of fields is a bug the route would
otherwise serialize without complaint.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.auth.schemas import Session, UserOut


def _user_out() -> UserOut:
    now = datetime.now(UTC)
    return UserOut(
        id=uuid.uuid4(),
        provider="google",
        email="a@example.com",
        email_verified=True,
        display_name="Alice",
        avatar_url=None,
        can_sell=False,
        can_purchase=True,
        seller_rating=None,
        created_at=now,
        updated_at=now,
    )


# ----- happy-path branch (link_required=False) ------------------------------


def test_happy_path_requires_access_token_and_user() -> None:
    now = datetime.now(UTC)
    session = Session(
        link_required=False,
        access_token="jwt",
        expires_at=now,
        user=_user_out(),
    )
    assert session.access_token == "jwt"
    assert session.link_token is None


def test_happy_path_missing_access_token_is_rejected() -> None:
    with pytest.raises(ValidationError, match="access_token"):
        Session(
            link_required=False,
            expires_at=datetime.now(UTC),
            user=_user_out(),
        )


def test_happy_path_missing_user_is_rejected() -> None:
    with pytest.raises(ValidationError, match="user"):
        Session(
            link_required=False,
            access_token="jwt",
            expires_at=datetime.now(UTC),
        )


def test_happy_path_with_link_token_is_rejected() -> None:
    """`link_required=False` + `link_token` is a programming bug — the two
    branches should be mutually exclusive."""
    with pytest.raises(ValidationError, match="link_token"):
        Session(
            link_required=False,
            access_token="jwt",
            expires_at=datetime.now(UTC),
            user=_user_out(),
            link_token="should-not-be-here",
        )


def test_happy_path_with_link_provider_is_rejected() -> None:
    with pytest.raises(ValidationError, match="link_provider"):
        Session(
            link_required=False,
            access_token="jwt",
            expires_at=datetime.now(UTC),
            user=_user_out(),
            link_provider="apple",
        )


# ----- link-required branch -------------------------------------------------


def test_link_required_requires_link_provider_and_link_token() -> None:
    session = Session(
        link_required=True,
        link_provider="apple",
        link_token="opaque",
    )
    assert session.link_provider == "apple"
    assert session.access_token is None


def test_link_required_missing_link_token_is_rejected() -> None:
    with pytest.raises(ValidationError, match="link_token"):
        Session(link_required=True, link_provider="apple")


def test_link_required_missing_link_provider_is_rejected() -> None:
    with pytest.raises(ValidationError, match="link_provider"):
        Session(link_required=True, link_token="opaque")


def test_link_required_with_access_token_is_rejected() -> None:
    """`link_required=True` + `access_token` would mean the server issued a
    session AND asked the client to link — incoherent."""
    with pytest.raises(ValidationError, match="access_token"):
        Session(
            link_required=True,
            link_provider="apple",
            link_token="opaque",
            access_token="jwt",
        )


def test_link_required_with_user_is_rejected() -> None:
    with pytest.raises(ValidationError, match="user"):
        Session(
            link_required=True,
            link_provider="apple",
            link_token="opaque",
            user=_user_out(),
        )


def test_link_required_with_expires_at_is_rejected() -> None:
    with pytest.raises(ValidationError, match="expires_at"):
        Session(
            link_required=True,
            link_provider="apple",
            link_token="opaque",
            expires_at=datetime.now(UTC),
        )
