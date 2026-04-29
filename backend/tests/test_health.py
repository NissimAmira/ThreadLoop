from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main  # noqa: F401  (ensures app is loaded)
from app.routers import health


def test_health_shape_when_all_down(client: TestClient) -> None:
    """Without real services running locally, all checks should report 'down' but the
    endpoint must still respond with a well-formed payload."""
    with (
        patch.object(health, "_check_db", return_value="down"),
        patch.object(health, "_check_redis", return_value="down"),
        patch.object(health, "_check_meili", return_value="down"),
    ):
        r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"status", "version", "db", "redis", "meili"}
    assert body["status"] == "down"


def test_health_status_ok_when_all_ok(client: TestClient) -> None:
    with (
        patch.object(health, "_check_db", return_value="ok"),
        patch.object(health, "_check_redis", return_value="ok"),
        patch.object(health, "_check_meili", return_value="ok"),
    ):
        r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_status_degraded_when_one_degraded(client: TestClient) -> None:
    with (
        patch.object(health, "_check_db", return_value="ok"),
        patch.object(health, "_check_redis", return_value="ok"),
        patch.object(health, "_check_meili", return_value="degraded"),
    ):
        r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
