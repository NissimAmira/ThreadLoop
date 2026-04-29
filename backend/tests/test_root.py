from fastapi.testclient import TestClient


def test_root_returns_metadata(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["name"]
    assert body["version"]
    assert body["docs"] == "/docs"
