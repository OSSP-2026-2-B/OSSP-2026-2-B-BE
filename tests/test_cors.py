from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_cors_allows_frontend():
    res = client.options("/health", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
    })
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_blocks_unknown_origin():
    res = client.options("/health", headers={
        "Origin": "http://evil.example.com",
        "Access-Control-Request-Method": "GET",
    })
    assert res.headers.get("access-control-allow-origin") is None