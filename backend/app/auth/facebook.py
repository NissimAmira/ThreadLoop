"""Facebook access-token verification via the Graph API.

Unlike the Google and Apple verifiers, Facebook does NOT issue an ID token —
the client hands us a user access token, and we resolve it to a profile by
calling the Graph API server-side. There is therefore no JWKS, no key cache,
and no JWT cryptography in this module: the security guarantee comes from
Facebook's response to two HTTPS calls.

Flow:

1. **`/debug_token`** — call `https://graph.facebook.com/debug_token` with
   `input_token=<user_access_token>` and `access_token=<app_access_token>`.
   Validates that the token is current (not expired, not revoked) AND that
   it was issued for OUR app (`data.app_id == FACEBOOK_APP_ID`). The app
   access token is the literal string `"{APP_ID}|{APP_SECRET}"` per Meta's
   docs — no Graph round-trip needed to obtain it. We also extract
   `data.user_id` and `data.expires_at` for the cross-check / belt-and-braces
   expiry guard described below.

2. **`/me?fields=id,name,email,picture`** — fetch the user profile, with
   `Authorization: Bearer <user_access_token>`. Returns the stable
   `(provider='facebook', provider_user_id=id)` identity plus optional
   email/name/picture.

Why `/debug_token` first: a malicious Facebook app could obtain a user's
access token and replay it against ours; `/me` is user-scoped (not
app-scoped) per Graph semantics, so it would happily return the user's
profile to whoever holds the token. `/debug_token` closes that gap by
asserting the token's `app_id` matches FACEBOOK_APP_ID. Cost is one extra
HTTP call inside the same `httpx.Client` session.

Belt-and-braces checks layered on top of the `app_id` guarantee:

- **Explicit `expires_at` check.** Graph normally flips `is_valid=false` on
  expiry, but a buggy or cached response could lie. We additionally reject
  when `expires_at` is set to a non-zero value in the past. Per Graph
  semantics, `expires_at: 0` means "never expires" (e.g. page tokens) — we
  treat 0 as "not applicable", NOT as "expired in 1970".
- **`/debug_token` ↔ `/me` cross-check.** `data.user_id` from the
  `/debug_token` response must match `id` from `/me`. A mismatch is a strong
  signal of a swapped or cached response; we raise rather than guess which
  end is authoritative.

Email handling: Facebook's `email` permission is optional — users can
decline it, in which case `/me` omits the `email` field entirely. We treat
any returned email as **unverified** because Facebook does not expose
`email_verified` in the Graph response and #18's account-linking logic
depends on the verified-email guarantee. Result: a Facebook sign-in never
provokes the cross-provider collision check (no verified email to match)
which is the documented intentional behaviour, not an oversight.

Failure semantics map to the OpenAPI contract:
    - Graph API unreachable / 5xx           -> GraphApiUnavailableError -> 503
    - Token invalid / expired / wrong app   -> InvalidFacebookTokenError -> 401
    - Malformed Graph API JSON              -> InvalidFacebookTokenError -> 401
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

GRAPH_API_BASE = "https://graph.facebook.com"
DEBUG_TOKEN_URL = f"{GRAPH_API_BASE}/debug_token"
ME_URL = f"{GRAPH_API_BASE}/me"
_HTTP_TIMEOUT_SECONDS = 5.0  # mirrors the Google / Apple JWKS fetch timeout


class GraphApiUnavailableError(Exception):
    """Graph API endpoint could not be reached or returned 5xx. Maps to HTTP 503."""


class InvalidFacebookTokenError(Exception):
    """Access token failed validation: rejected by `/debug_token`, `/me` returned
    401, or the response shape didn't match the documented Graph contract."""


@dataclass(frozen=True)
class _DebugTokenData:
    """The fields we extract from a `/debug_token` response, after structural
    validation. Held briefly inside `verify_facebook_access_token` so the
    cross-check against `/me` can compare `user_id` ↔ `id`.

    `expires_at` follows Graph's convention: `0` means "never expires" (page
    tokens, long-lived app tokens), any positive value is a Unix timestamp.
    """

    app_id: str
    user_id: str
    expires_at: int


@dataclass(frozen=True)
class FacebookIdentity:
    """Verified profile fields from a Facebook user access token, narrowed to
    what the route layer needs.

    `email_verified` is hard-coded to `False` because Facebook's Graph API
    does not return a verified-email flag — we cannot make the same guarantee
    Google and Apple's ID tokens give us. The route layer relies on this to
    skip cross-provider collision detection on Facebook sign-ins (matching
    against an unverified email would be an account-takeover vector).
    """

    sub: str
    email: str | None
    email_verified: bool
    name: str | None
    picture: str | None


def _build_app_access_token(app_id: str, app_secret: str) -> str:
    """Per Meta docs, the app access token is the literal string
    `"{APP_ID}|{APP_SECRET}"` — no Graph call needed. We rebuild it per
    verification rather than caching because the cost is a string format and
    avoiding a process-wide secret-shaped cache simplifies the threat model.
    """
    return f"{app_id}|{app_secret}"


def _validate_debug_token_response(payload: Any, *, expected_app_id: str) -> _DebugTokenData:
    """Inspect a `/debug_token` response and raise `InvalidFacebookTokenError`
    if it doesn't pass: token must be valid, not expired, and issued for the
    expected `app_id`. Per Graph API docs, the response shape is
    `{"data": {"app_id": str, "is_valid": bool, "user_id": str,
    "expires_at": int, ...}}`.

    Returns the extracted `(app_id, user_id, expires_at)` for downstream
    cross-check against `/me`. The expiry guard treats `expires_at: 0` as
    "never expires" per Graph convention, NOT as "expired in 1970".
    """
    if not isinstance(payload, dict):
        raise InvalidFacebookTokenError("debug_token response is not a JSON object")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise InvalidFacebookTokenError("debug_token response missing `data` object")

    if not bool(data.get("is_valid")):
        # Facebook also surfaces a nested `error.message` here on some failure
        # modes (token revoked, expired, etc.). Don't echo it upward — it can
        # carry token contents.
        raise InvalidFacebookTokenError("access token is not valid per debug_token")

    app_id = data.get("app_id")
    if not isinstance(app_id, str) or app_id != expected_app_id:
        # Token was issued for a different Facebook app. THIS is the attack
        # `/debug_token` defends against — `/me` alone would have let it
        # through.
        # See module docstring § "Why /debug_token first" for the threat model.
        raise InvalidFacebookTokenError("access token was issued for a different app")

    user_id = data.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise InvalidFacebookTokenError("debug_token response missing `user_id`")

    expires_at_raw = data.get("expires_at", 0)
    # Graph returns this as an int; tolerate `bool` not being one of them
    # (`isinstance(True, int)` is True so check int after bool exclusion).
    if isinstance(expires_at_raw, bool) or not isinstance(expires_at_raw, int):
        raise InvalidFacebookTokenError("debug_token `expires_at` is not an integer")
    if expires_at_raw != 0 and expires_at_raw < int(time.time()):
        # Defence-in-depth: even though `is_valid=true` already implies
        # not-expired, a buggy / cached upstream response could disagree with
        # `expires_at`. `0` per Graph means "never expires" (page tokens) and
        # must NOT be treated as "expired at the epoch".
        raise InvalidFacebookTokenError("access token is expired per debug_token")

    return _DebugTokenData(app_id=app_id, user_id=user_id, expires_at=expires_at_raw)


def _parse_me_response(payload: Any) -> FacebookIdentity:
    """Parse a `/me` response into a `FacebookIdentity`. Raises
    `InvalidFacebookTokenError` on missing/malformed `id`.
    """
    if not isinstance(payload, dict):
        raise InvalidFacebookTokenError("/me response is not a JSON object")

    sub_raw = payload.get("id")
    if not isinstance(sub_raw, str) or not sub_raw:
        raise InvalidFacebookTokenError("/me response missing required `id` field")

    email_raw = payload.get("email")
    email = email_raw if isinstance(email_raw, str) and email_raw else None

    name_raw = payload.get("name")
    name = name_raw if isinstance(name_raw, str) and name_raw else None

    picture_raw = payload.get("picture")
    picture: str | None = None
    # `/me?fields=...,picture` returns a nested object: {"picture": {"data":
    # {"url": "...", "is_silhouette": bool}}}. Tolerate both the nested form
    # and the flat string in case Graph behaviour shifts under us.
    if isinstance(picture_raw, dict):
        data = picture_raw.get("data")
        if isinstance(data, dict):
            url = data.get("url")
            if isinstance(url, str) and url:
                picture = url
    elif isinstance(picture_raw, str) and picture_raw:
        picture = picture_raw

    return FacebookIdentity(
        sub=sub_raw,
        email=email,
        # Facebook does not expose `email_verified`. Documented in the module
        # docstring; the route layer relies on this to skip collision
        # detection.
        email_verified=False,
        name=name,
        picture=picture,
    )


def verify_facebook_access_token(
    access_token: str,
    *,
    app_id: str,
    app_secret: str,
    transport: httpx.BaseTransport | None = None,
) -> FacebookIdentity:
    """Validate a Facebook user access token and return the resolved profile.

    Two Graph API calls back-to-back: `/debug_token` to verify the token was
    issued for our app, then `/me?fields=id,name,email,picture` for the
    profile. Tests inject `transport` as an `httpx.MockTransport` so live
    Graph is never hit in CI.

    Raises:
        InvalidFacebookTokenError: token rejected by Graph API, issued for a
            different app, or returned in an unparseable shape.
        GraphApiUnavailableError: Graph API unreachable or returned 5xx.
    """
    if not app_id or not app_secret:
        # Same defense-in-depth posture as the Google / Apple verifiers: refuse
        # to "verify" against missing credentials. Settings already validates
        # this when `auth_enabled=True`, but a misconfigured deploy deserves a
        # loud failure rather than a silent 401.
        raise InvalidFacebookTokenError("Facebook app credentials are not configured")
    if not access_token:
        raise InvalidFacebookTokenError("access token is empty")

    app_access_token = _build_app_access_token(app_id, app_secret)

    client_kwargs: dict[str, Any] = {"timeout": _HTTP_TIMEOUT_SECONDS}
    if transport is not None:
        client_kwargs["transport"] = transport

    try:
        with httpx.Client(**client_kwargs) as client:
            debug_resp = client.get(
                DEBUG_TOKEN_URL,
                params={
                    "input_token": access_token,
                    "access_token": app_access_token,
                },
            )
            # /debug_token returns 200 even for invalid tokens (the verdict is
            # in `data.is_valid`). 4xx here means the request itself was
            # malformed (bad app token, etc.); 5xx is a real Graph outage.
            if debug_resp.status_code >= 500:
                raise GraphApiUnavailableError(
                    f"debug_token returned HTTP {debug_resp.status_code}"
                )
            if debug_resp.status_code >= 400:
                # Treat 4xx on /debug_token as token-rejected rather than
                # outage — most often this means the app access token is
                # wrong, but we don't want to map that to 503 (which would
                # invite the client to retry).
                raise InvalidFacebookTokenError(
                    f"debug_token returned HTTP {debug_resp.status_code}"
                )
            try:
                debug_payload = debug_resp.json()
            except ValueError as exc:
                raise InvalidFacebookTokenError("debug_token returned non-JSON body") from exc

            debug_data = _validate_debug_token_response(debug_payload, expected_app_id=app_id)

            me_resp = client.get(
                ME_URL,
                params={"fields": "id,name,email,picture"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if me_resp.status_code == 401:
                # Token rejected by /me even though /debug_token called it
                # valid. Treat as invalid_token rather than outage.
                raise InvalidFacebookTokenError("/me rejected the access token")
            if me_resp.status_code >= 500:
                raise GraphApiUnavailableError(f"/me returned HTTP {me_resp.status_code}")
            if me_resp.status_code >= 400:
                raise InvalidFacebookTokenError(f"/me returned HTTP {me_resp.status_code}")

            try:
                me_payload = me_resp.json()
            except ValueError as exc:
                raise InvalidFacebookTokenError("/me returned non-JSON body") from exc

    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        raise GraphApiUnavailableError(f"Graph API unreachable: {exc}") from exc
    except httpx.HTTPError as exc:
        # Catch-all for any other transport-level error (e.g. protocol error,
        # unexpected response). Map to 503 since the request never made it
        # to a verdict.
        raise GraphApiUnavailableError(f"Graph API transport error: {exc}") from exc

    identity = _parse_me_response(me_payload)
    if debug_data.user_id != identity.sub:
        # Cross-provider belt-and-braces: `/debug_token` asserted the token's
        # `user_id`, `/me` returned the profile's `id`. They MUST agree —
        # disagreement signals a swapped or cached response. Use a generic
        # message so we don't echo either user id back to the caller.
        raise InvalidFacebookTokenError("debug_token user_id does not match /me id")
    return identity
