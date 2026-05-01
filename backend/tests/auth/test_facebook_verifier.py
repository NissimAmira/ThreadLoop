"""Unit tests for `verify_facebook_access_token`.

The Graph API is mocked via `httpx.MockTransport`; nothing in this file ever
reaches the network. The transport's handler dispatches on URL so a single
test can exercise both the `/debug_token` and `/me` legs.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.auth.facebook import (
    DEBUG_TOKEN_URL,
    ME_URL,
    GraphApiUnavailableError,
    InvalidFacebookTokenError,
    verify_facebook_access_token,
)

APP_ID = "1234567890"
APP_SECRET = "test-app-secret-shhh"
USER_ACCESS_TOKEN = "EAATEST_user_access_token"


def _expected_app_access_token() -> str:
    return f"{APP_ID}|{APP_SECRET}"


def _make_transport(
    *,
    debug_token_response: httpx.Response | None = None,
    me_response: httpx.Response | None = None,
    debug_token_raises: type[Exception] | None = None,
    me_raises: type[Exception] | None = None,
) -> httpx.MockTransport:
    """Build a transport that serves canned responses for /debug_token and /me.

    Pass `*_raises` to simulate transport-level failures (timeout, connect
    error). Defaults are 200 OK with valid bodies.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?", 1)[0]
        if url == DEBUG_TOKEN_URL:
            if debug_token_raises is not None:
                raise debug_token_raises("simulated", request=request)  # type: ignore[call-arg]
            assert debug_token_response is not None, "test forgot to set debug_token_response"
            return debug_token_response
        if url == ME_URL:
            if me_raises is not None:
                raise me_raises("simulated", request=request)  # type: ignore[call-arg]
            assert me_response is not None, "test forgot to set me_response"
            return me_response
        return httpx.Response(404, json={"error": f"unexpected url: {url}"})

    return httpx.MockTransport(handler)


def _ok_debug_token(*, app_id: str = APP_ID, is_valid: bool = True) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "app_id": app_id,
                "is_valid": is_valid,
                "user_id": "user-12345",
            }
        },
    )


def _ok_me(
    *,
    user_id: str = "user-12345",
    name: str | None = "Test User",
    email: str | None = "user@example.com",
    picture_url: str | None = "https://cdn.fb/avatar.png",
) -> httpx.Response:
    body: dict[str, Any] = {"id": user_id}
    if name is not None:
        body["name"] = name
    if email is not None:
        body["email"] = email
    if picture_url is not None:
        body["picture"] = {"data": {"url": picture_url, "is_silhouette": False}}
    return httpx.Response(200, json=body)


# ----- happy paths ----------------------------------------------------------


def test_happy_path_with_email() -> None:
    transport = _make_transport(
        debug_token_response=_ok_debug_token(),
        me_response=_ok_me(),
    )
    identity = verify_facebook_access_token(
        USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
    )
    assert identity.sub == "user-12345"
    assert identity.email == "user@example.com"
    # Facebook does not expose `email_verified`; we hard-code False.
    assert identity.email_verified is False
    assert identity.name == "Test User"
    assert identity.picture == "https://cdn.fb/avatar.png"


def test_happy_path_without_email() -> None:
    """Users can decline the email permission; /me then omits the field."""
    transport = _make_transport(
        debug_token_response=_ok_debug_token(),
        me_response=_ok_me(email=None),
    )
    identity = verify_facebook_access_token(
        USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
    )
    assert identity.sub == "user-12345"
    assert identity.email is None
    assert identity.email_verified is False
    assert identity.name == "Test User"


def test_happy_path_passes_app_access_token_to_debug_token() -> None:
    """Verify that the app access token (`{APP_ID}|{APP_SECRET}`) is what
    actually shows up on the wire — this is the security-critical input."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?", 1)[0]
        if url == DEBUG_TOKEN_URL:
            captured["input_token"] = request.url.params.get("input_token") or ""
            captured["access_token"] = request.url.params.get("access_token") or ""
            return _ok_debug_token()
        if url == ME_URL:
            captured["auth_header"] = request.headers.get("Authorization", "")
            return _ok_me()
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    verify_facebook_access_token(
        USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
    )
    assert captured["input_token"] == USER_ACCESS_TOKEN
    assert captured["access_token"] == _expected_app_access_token()
    assert captured["auth_header"] == f"Bearer {USER_ACCESS_TOKEN}"


def test_picture_flat_string_form_tolerated() -> None:
    """Defensive: if Graph ever returns `picture` as a flat URL instead of
    the documented nested object, take it verbatim rather than dropping it."""
    transport = _make_transport(
        debug_token_response=_ok_debug_token(),
        me_response=httpx.Response(
            200, json={"id": "u1", "name": "Z", "picture": "https://cdn.fb/x.png"}
        ),
    )
    identity = verify_facebook_access_token(
        USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
    )
    assert identity.picture == "https://cdn.fb/x.png"


def test_picture_nested_without_url_drops_to_none() -> None:
    transport = _make_transport(
        debug_token_response=_ok_debug_token(),
        me_response=httpx.Response(
            200,
            json={"id": "u1", "name": "Z", "picture": {"data": {"is_silhouette": True}}},
        ),
    )
    identity = verify_facebook_access_token(
        USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
    )
    assert identity.picture is None


# ----- /debug_token rejection paths -----------------------------------------


def test_debug_token_rejects_invalid_token() -> None:
    transport = _make_transport(
        debug_token_response=_ok_debug_token(is_valid=False),
        me_response=_ok_me(),
    )
    with pytest.raises(InvalidFacebookTokenError, match="not valid"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


def test_debug_token_rejects_token_for_different_app() -> None:
    """The defining /debug_token guard: a token issued for a different
    Facebook app must NOT be accepted even though /me would happily return
    the user profile."""
    transport = _make_transport(
        debug_token_response=_ok_debug_token(app_id="other-app-99999"),
        me_response=_ok_me(),
    )
    with pytest.raises(InvalidFacebookTokenError, match="different app"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


def test_debug_token_4xx_treated_as_invalid_token() -> None:
    """A 4xx on /debug_token is most often a bad app access token — surface
    as 401 (invalid_token), not 503, so the client doesn't retry forever."""
    transport = _make_transport(
        debug_token_response=httpx.Response(400, json={"error": "bad app token"}),
        me_response=_ok_me(),
    )
    with pytest.raises(InvalidFacebookTokenError, match="HTTP 400"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


def test_debug_token_response_missing_data_rejected() -> None:
    transport = _make_transport(
        debug_token_response=httpx.Response(200, json={"unexpected": "shape"}),
        me_response=_ok_me(),
    )
    with pytest.raises(InvalidFacebookTokenError, match="data"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


def test_debug_token_returns_non_json_rejected() -> None:
    transport = _make_transport(
        debug_token_response=httpx.Response(200, content=b"<html>not json</html>"),
        me_response=_ok_me(),
    )
    with pytest.raises(InvalidFacebookTokenError, match="non-JSON"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


# ----- /me failure paths -----------------------------------------------------


def test_me_returns_401_treated_as_invalid_token() -> None:
    """If /me rejects after /debug_token says the token is valid (race with a
    revocation), surface as invalid_token rather than outage."""
    transport = _make_transport(
        debug_token_response=_ok_debug_token(),
        me_response=httpx.Response(401, json={"error": "expired"}),
    )
    with pytest.raises(InvalidFacebookTokenError, match="rejected"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


def test_me_response_without_id_rejected() -> None:
    transport = _make_transport(
        debug_token_response=_ok_debug_token(),
        me_response=httpx.Response(200, json={"name": "no id field here"}),
    )
    with pytest.raises(InvalidFacebookTokenError, match="id"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


def test_me_returns_non_json_rejected() -> None:
    transport = _make_transport(
        debug_token_response=_ok_debug_token(),
        me_response=httpx.Response(200, content=b"<html>"),
    )
    with pytest.raises(InvalidFacebookTokenError, match="non-JSON"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


# ----- Graph API outage paths ------------------------------------------------


def test_debug_token_5xx_mapped_to_503() -> None:
    transport = _make_transport(
        debug_token_response=httpx.Response(503, json={"error": "outage"}),
        me_response=_ok_me(),
    )
    with pytest.raises(GraphApiUnavailableError, match="503"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


def test_me_5xx_mapped_to_503() -> None:
    transport = _make_transport(
        debug_token_response=_ok_debug_token(),
        me_response=httpx.Response(502, json={"error": "outage"}),
    )
    with pytest.raises(GraphApiUnavailableError, match="502"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


def test_debug_token_connect_error_mapped_to_unavailable() -> None:
    transport = _make_transport(
        debug_token_raises=httpx.ConnectError,
    )
    with pytest.raises(GraphApiUnavailableError, match="unreachable"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


def test_debug_token_timeout_mapped_to_unavailable() -> None:
    transport = _make_transport(
        debug_token_raises=httpx.ConnectTimeout,
    )
    with pytest.raises(GraphApiUnavailableError, match="unreachable"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret=APP_SECRET, transport=transport
        )


# ----- configuration guards -------------------------------------------------


def _refuse_handler(request: httpx.Request) -> httpx.Response:
    """Transport handler that asserts no HTTP call is made — used by the
    config-guard tests, which must short-circuit before any Graph round-trip."""
    raise AssertionError(f"unexpected HTTP call: {request.url}")


def test_empty_app_id_rejected_loudly() -> None:
    """Mirrors the Google / Apple verifiers: refuse to verify against missing
    config rather than producing opaque downstream errors."""
    transport = httpx.MockTransport(_refuse_handler)
    with pytest.raises(InvalidFacebookTokenError, match="not configured"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id="", app_secret=APP_SECRET, transport=transport
        )


def test_empty_app_secret_rejected_loudly() -> None:
    transport = httpx.MockTransport(_refuse_handler)
    with pytest.raises(InvalidFacebookTokenError, match="not configured"):
        verify_facebook_access_token(
            USER_ACCESS_TOKEN, app_id=APP_ID, app_secret="", transport=transport
        )


def test_empty_access_token_rejected() -> None:
    transport = httpx.MockTransport(_refuse_handler)
    with pytest.raises(InvalidFacebookTokenError, match="empty"):
        verify_facebook_access_token("", app_id=APP_ID, app_secret=APP_SECRET, transport=transport)
