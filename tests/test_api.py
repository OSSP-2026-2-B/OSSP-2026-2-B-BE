from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

START = {"lat": 37.4979, "lng": 127.0276}
END = {"lat": 37.5045, "lng": 127.0250}


def test_route_returns_requested_modes():
    res = client.post("/api/route", json={"start": START, "end": END, "modes": ["shortest", "balanced"]})
    assert res.status_code == 200
    assert [r["mode"] for r in res.json()["routes"]] == ["shortest", "balanced"]


def test_route_default_modes():
    res = client.post("/api/route", json={"start": START, "end": END})
    assert [r["mode"] for r in res.json()["routes"]] == ["shortest", "balanced"]


def test_route_rejects_bad_mode():
    res = client.post("/api/route", json={"start": START, "end": END, "modes": ["fast"]})
    assert res.status_code == 422


def test_route_geometry_is_lng_lat():
    res = client.post("/api/route", json={"start": START, "end": END})
    first_point = res.json()["routes"][0]["geometry"]["coordinates"][0]
    assert first_point == [START["lng"], START["lat"]]


def test_risk_layer_requires_bbox():
    assert client.get("/api/risk-layer").status_code == 422
    res = client.get("/api/risk-layer", params={"bbox": "127.02,37.49,127.03,37.50"})
    assert res.status_code == 200
    assert res.json()["type"] == "FeatureCollection"


def test_observations_badges():
    kinds = {b["kind"] for b in client.get("/api/observations").json()["badges"]}
    assert kinds == {"confirmed", "estimated"}