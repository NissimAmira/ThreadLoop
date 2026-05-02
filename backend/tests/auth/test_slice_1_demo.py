"""Slice-1 demo integration test (Epic #11 vertical-slicing pivot).

This test drives the full backend half of slice 1 end-to-end:

    Google callback  →  /api/me  →  /api/auth/refresh  →  /api/me
                    →  /api/auth/logout  →  refresh again returns 401

If this passes, the slice-1 demo is functionally ready on the backend.
The FE half (#19) drives the same surfaces from a browser; this test is
the closest the BE can get to "the demo works" without a UI.

Why a single multi-step test rather than separate per-step ones: the
per-step coverage already exists (`test_google_callback_integration.py`,
`test_me_route.py`, `test_refresh_route.py`, `test_logout_route.py`). What
this test pins specifically is **continuity** — that the artifacts produced
by step N (access JWT in body, refresh cookie in header) actually drive
step N+1. A regression where any pair of steps drift apart at the wire
level wouldn't show up in the per-step suites.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from alembic import command
from app import db as db_module
from app.config import get_settings
from app.db import Base, get_db
from app.main import app
from tests.auth._test_settings import make_test_settings

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
GOOGLE_AUD = "test-google-client-id.apps.googleusercontent.com"


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


@pytest.fixture
def auth_client(pg_url: str) -> Iterator[TestClient]:
    cfg = _alembic_config(pg_url)
    command.upgrade(cfg, "head")

    engine = create_engine(pg_url, future=True)
    test_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as conn:
        for table in ("refresh_tokens", "users"):
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))

    def override_get_db() -> Iterator[DbSession]:
        session = test_session_local()
        try:
            yield session
        finally:
            session.close()

    test_settings = make_test_settings(
        database_url=pg_url,
        google_client_id=GOOGLE_AUD,
        refresh_cookie_secure=False,
    )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: test_settings
    db_module.engine = engine
    db_module.SessionLocal = test_session_local
    Base.metadata.bind = engine

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_settings, None)
        engine.dispose()


def _set_cookie_value(response_headers: Any, name: str) -> str | None:
    """Pull the bare value of `Set-Cookie: name=value; ...` from a response.

    httpx's cookie-jar `client.cookies.get(name)` raises CookieConflict when
    multiple cookies share a name (e.g. one we sent + one the server set
    with a different path). Reading the header directly avoids the
    ambiguity entirely.
    """
    raw = response_headers.raw if hasattr(response_headers, "raw") else None
    items = (
        [(k.decode().lower(), v.decode()) for k, v in raw]
        if raw is not None
        else (
            response_headers.multi_items()
            if hasattr(response_headers, "multi_items")
            else response_headers.items()
        )
    )
    for key, value in items:
        if key.lower() == "set-cookie" and value.startswith(f"{name}="):
            return value[len(name) + 1 :].split(";", 1)[0]
    return None


def test_slice_1_demo_signin_to_logout_full_loop(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    # Step 1: sign in with Google. The client-mocked JWKS in
    # `tests/auth/conftest.py` autouse fixture lets the verifier accept
    # this token without hitting Google live.
    token = google_id_token(
        sub="google-sub-slice1",
        aud=GOOGLE_AUD,
        email="slice1@example.com",
        name="Slice One",
    )
    signin = auth_client.post("/api/auth/google/callback", json={"id_token": token})
    assert signin.status_code == 200, signin.text
    body = signin.json()
    assert body["link_required"] is False
    initial_access_token: str = body["access_token"]
    user_id: str = body["user"]["id"]
    initial_refresh_cookie = _set_cookie_value(signin.headers, "refresh_token")
    assert initial_refresh_cookie is not None, "callback must set the refresh cookie"

    # Step 2: hit /api/me with the access JWT — confirms `require_user`
    # accepts what `mint_access_token` produced.
    me1 = auth_client.get("/api/me", headers={"Authorization": f"Bearer {initial_access_token}"})
    assert me1.status_code == 200, me1.text
    assert me1.json()["id"] == user_id
    assert me1.json()["display_name"] == "Slice One"

    # Step 3: rotate via /api/auth/refresh. Cookie comes back fresh and
    # the body has a new access JWT. We send the cookie explicitly per
    # request so we don't depend on httpx's cookie-jar merge semantics.
    refresh = auth_client.post(
        "/api/auth/refresh",
        cookies={"refresh_token": initial_refresh_cookie},
    )
    assert refresh.status_code == 200, refresh.text
    new_access_token: str = refresh.json()["access_token"]
    # `jti` (a fresh UUID per mint) makes the two access JWTs byte-distinct
    # even within the same wall-clock second, so we can assert rotation
    # directly across the refresh boundary.
    assert new_access_token != initial_access_token, (
        "refresh must rotate the access JWT (jti makes them byte-distinct)"
    )
    new_refresh_cookie = _set_cookie_value(refresh.headers, "refresh_token")
    assert new_refresh_cookie is not None
    assert new_refresh_cookie != initial_refresh_cookie, (
        "refresh must rotate the refresh-token cookie value"
    )

    # Step 4: /api/me with the rotated access JWT still works.
    me2 = auth_client.get("/api/me", headers={"Authorization": f"Bearer {new_access_token}"})
    assert me2.status_code == 200
    assert me2.json()["id"] == user_id

    # Step 5: log out. Server revokes the active row, clears the cookie.
    logout = auth_client.post(
        "/api/auth/logout",
        cookies={"refresh_token": new_refresh_cookie},
    )
    assert logout.status_code == 204

    # Step 6: subsequent refresh attempts return 401 — the active refresh
    # row is now revoked. We replay the cookie the server told us to clear
    # (closest to "the malicious party kept the cookie at logout time").
    after_logout = auth_client.post(
        "/api/auth/refresh",
        cookies={"refresh_token": new_refresh_cookie},
    )
    assert after_logout.status_code == 401
    assert after_logout.json()["detail"]["code"] == "invalid_refresh_token"

    # Sanity: the access JWT is still cryptographically valid until its
    # 15-minute expiry, but the practical demo expectation is "the user
    # is logged out". Confirm via the refresh path being closed; the JWT
    # itself is by-design valid until expiry per RFC 0001 § Session model
    # (we don't maintain a server-side denylist — that's the trade-off
    # JWT-based auth makes).
    engine = create_engine(pg_url, future=True)
    try:
        with engine.begin() as conn:
            active_count = conn.execute(
                text(
                    "SELECT count(*) FROM refresh_tokens "
                    "WHERE user_id = :uid AND revoked_at IS NULL"
                ),
                {"uid": user_id},
            ).scalar_one()
        assert active_count == 0, "after logout, no active refresh tokens must remain for the user"
    finally:
        engine.dispose()
