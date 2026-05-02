"""Unit tests for `app.auth.deps.require_user`.

The dep is exercised end-to-end against a real Postgres in
`test_me_route.py`; these tests pin the failure-mapping contract without
needing a DB by passing an in-memory fake session.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from authlib.jose import jwt
from fastapi import HTTPException
from starlette.requests import Request

from app.auth.deps import require_user
from app.auth.session import mint_access_token
from app.config import Settings
from app.models import User
from tests.auth._test_settings import make_test_settings


class _FakeSession:
    """Minimal in-memory `Session.get` shim. We exercise the JWT branches
    here; the real DB lookup is covered in `test_me_route.py`."""

    def __init__(self, users: dict[uuid.UUID, User] | None = None) -> None:
        self._users = users or {}

    def get(self, model: Any, key: Any) -> User | None:
        del model  # only one model is queried
        if isinstance(key, uuid.UUID):
            return self._users.get(key)
        return None


def _request_with_authorization(value: str | None) -> Request:
    """Build a Starlette Request with (or without) an Authorization header."""
    headers: list[tuple[bytes, bytes]] = []
    if value is not None:
        headers.append((b"authorization", value.encode("latin-1")))
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/me",
        "headers": headers,
    }
    return Request(scope)


def _user(user_id: uuid.UUID | None = None) -> User:
    return User(
        id=user_id or uuid.uuid4(),
        provider="google",
        provider_user_id="google-sub-1",
        email="alice@example.com",
        email_verified=True,
        display_name="Alice",
        avatar_url=None,
        can_sell=False,
        can_purchase=True,
    )


@pytest.fixture
def settings() -> Settings:
    return make_test_settings()


def _expect_401(exc: pytest.ExceptionInfo[HTTPException], code: str) -> None:
    assert exc.value.status_code == 401
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == code


# ----- happy path -----------------------------------------------------------


def test_returns_user_for_valid_bearer_token(settings: Settings) -> None:
    user = _user()
    fake_db = _FakeSession({user.id: user})
    token, _ = mint_access_token(user, settings=settings)
    request = _request_with_authorization(f"Bearer {token}")

    resolved = require_user(request, fake_db, settings)  # type: ignore[arg-type]

    assert resolved is user


# ----- header / scheme failures ---------------------------------------------


def test_missing_authorization_header_returns_401(settings: Settings) -> None:
    request = _request_with_authorization(None)
    fake_db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        require_user(request, fake_db, settings)  # type: ignore[arg-type]
    _expect_401(exc, "not_authenticated")


def test_non_bearer_scheme_returns_401(settings: Settings) -> None:
    request = _request_with_authorization("Basic Zm9vOmJhcg==")
    fake_db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        require_user(request, fake_db, settings)  # type: ignore[arg-type]
    _expect_401(exc, "invalid_authorization_scheme")


def test_bare_bearer_no_token_returns_401(settings: Settings) -> None:
    """`Authorization: Bearer` with no value at all — `split(None, 1)`
    returns a single element (whitespace-only suffixes get stripped by
    `split` with no separator), so the "not 2 parts" guard fires and we
    surface as `invalid_authorization_scheme`."""
    request = _request_with_authorization("Bearer")
    fake_db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        require_user(request, fake_db, settings)  # type: ignore[arg-type]
    _expect_401(exc, "invalid_authorization_scheme")


# ----- token validation failures --------------------------------------------


def test_garbage_token_returns_401(settings: Settings) -> None:
    request = _request_with_authorization("Bearer not.a.jwt")
    fake_db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        require_user(request, fake_db, settings)  # type: ignore[arg-type]
    _expect_401(exc, "invalid_token")


def test_token_signed_with_wrong_key_returns_401(settings: Settings) -> None:
    user = _user()
    other_settings = make_test_settings(jwt_signing_key="totally-different-key")
    forged_token, _ = mint_access_token(user, settings=other_settings)
    request = _request_with_authorization(f"Bearer {forged_token}")
    fake_db = _FakeSession({user.id: user})
    with pytest.raises(HTTPException) as exc:
        require_user(request, fake_db, settings)  # type: ignore[arg-type]
    _expect_401(exc, "invalid_token")


def test_expired_token_returns_401(settings: Settings) -> None:
    user = _user()
    # Sign by hand with `exp` already past — `mint_access_token` doesn't
    # let us pass a custom exp, so we craft directly.
    now = int(time.time())
    payload = {
        "sub": str(user.id),
        "iat": now - 3600,
        "exp": now - 60,
        "typ": "access",
    }
    encoded = jwt.encode({"alg": "HS256"}, payload, settings.jwt_signing_key)
    if isinstance(encoded, bytes):
        encoded = encoded.decode("ascii")
    request = _request_with_authorization(f"Bearer {encoded}")
    fake_db = _FakeSession({user.id: user})
    with pytest.raises(HTTPException) as exc:
        require_user(request, fake_db, settings)  # type: ignore[arg-type]
    _expect_401(exc, "invalid_token")


def test_link_token_typ_rejected_as_access(settings: Settings) -> None:
    """A link token signed with the same key would otherwise pass JOSE
    validation. The `typ=access` check is what keeps them apart."""
    user = _user()
    now = int(time.time())
    payload = {
        "sub": str(user.id),
        "iat": now,
        "exp": now + 3600,
        "typ": "link",  # the discriminating claim
    }
    encoded = jwt.encode({"alg": "HS256"}, payload, settings.jwt_signing_key)
    if isinstance(encoded, bytes):
        encoded = encoded.decode("ascii")
    request = _request_with_authorization(f"Bearer {encoded}")
    fake_db = _FakeSession({user.id: user})
    with pytest.raises(HTTPException) as exc:
        require_user(request, fake_db, settings)  # type: ignore[arg-type]
    _expect_401(exc, "invalid_token")


def test_token_without_typ_rejected(settings: Settings) -> None:
    user = _user()
    now = int(time.time())
    payload = {"sub": str(user.id), "iat": now, "exp": now + 3600}
    encoded = jwt.encode({"alg": "HS256"}, payload, settings.jwt_signing_key)
    if isinstance(encoded, bytes):
        encoded = encoded.decode("ascii")
    request = _request_with_authorization(f"Bearer {encoded}")
    fake_db = _FakeSession({user.id: user})
    with pytest.raises(HTTPException) as exc:
        require_user(request, fake_db, settings)  # type: ignore[arg-type]
    _expect_401(exc, "invalid_token")


def test_token_with_non_uuid_sub_rejected(settings: Settings) -> None:
    now = int(time.time())
    payload = {
        "sub": "not-a-uuid",
        "iat": now,
        "exp": now + 3600,
        "typ": "access",
    }
    encoded = jwt.encode({"alg": "HS256"}, payload, settings.jwt_signing_key)
    if isinstance(encoded, bytes):
        encoded = encoded.decode("ascii")
    request = _request_with_authorization(f"Bearer {encoded}")
    fake_db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        require_user(request, fake_db, settings)  # type: ignore[arg-type]
    _expect_401(exc, "invalid_token")


def test_user_not_found_returns_401(settings: Settings) -> None:
    """Token cryptographically valid but no `users` row matches.

    Models the 'account deleted between issuance and use' race. We collapse
    to the same 401 envelope as other invalid-token cases — leaking 'this
    user used to exist' to a probe is a small but real info disclosure.
    """
    ghost_user = _user()  # not added to fake_db
    token, _ = mint_access_token(ghost_user, settings=settings)
    request = _request_with_authorization(f"Bearer {token}")
    fake_db = _FakeSession()  # empty — user lookup returns None
    with pytest.raises(HTTPException) as exc:
        require_user(request, fake_db, settings)  # type: ignore[arg-type]
    _expect_401(exc, "invalid_token")


def _drain_iterator(it: Iterator[Any]) -> None:
    """Drain a generator-style FastAPI dep; we don't call any of these
    directly so this stub keeps mypy happy on an otherwise-unused import."""
    for _ in it:
        return
