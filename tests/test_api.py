from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import risk.cache
from app.main import app
from collectors.common import now_kst

client = TestClient(app)

START = {"lat": 37.4979, "lng": 127.0276}
END = {"lat": 37.5045, "lng": 127.0250}


def fake_snapshot(rain_1h_mm=0.0, ago_min=0, risk=None, excluded=()):
    """risk.cache.get_snapshot() 이 돌려주는 모양. ago_min = 마지막 서울시 정상 관측이 몇 분 전인지"""
    t = (now_kst() - timedelta(minutes=ago_min)).isoformat(timespec="seconds")
    return {
        "risk": risk or {}, "excluded": set(excluded), "reasons": {},
        "rain": {"rain_1h_mm": rain_1h_mm, "rain_3h_mm": None, "rain_15m_mm": None, "updated_at": t,
                 "delayed": False, "source": "seoul", "last_normal_at": t},
        "control_points": [], "computed_at": t,
    }


def use_snapshot(monkeypatch, snap):
    monkeypatch.setattr(risk.cache, "get_snapshot", lambda: snap)


@pytest.fixture(autouse=True)
def fresh_snapshot(monkeypatch):
    """테스트에서는 스케줄러가 돌지 않으므로, 방금 갱신된 비 없는 스냅샷을 기본으로 넣는다"""
    use_snapshot(monkeypatch, fake_snapshot())


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


def test_route_uses_snapshot_rain(monkeypatch):
    snap = fake_snapshot(rain_1h_mm=30.0)
    use_snapshot(monkeypatch, snap)
    body = client.post("/api/route", json={"start": START, "end": END}).json()
    assert body["rain"] == {"rain_1h_mm": 30.0, "updated_at": snap["rain"]["updated_at"]}
    assert body["routes"][0]["walk_speed_mps"] == 0.9  # 20mm 이상 → 폭우 보행 속도


@pytest.mark.parametrize("ago_min, status", [(59, 200), (61, 503)])
def test_route_503_when_rain_stale(monkeypatch, ago_min, status):
    use_snapshot(monkeypatch, fake_snapshot(ago_min=ago_min))
    res = client.post("/api/route", json={"start": START, "end": END})
    assert res.status_code == status
    if status == 503:
        assert res.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_route_503_before_first_update(monkeypatch):
    use_snapshot(monkeypatch, None)  # 서버를 켠 직후, 첫 갱신 전
    res = client.post("/api/route", json={"start": START, "end": END})
    assert res.status_code == 503
    assert res.json() == {"error": {"code": "DATA_UNAVAILABLE", "message": "강우 자료를 받을 수 없습니다."}}


def test_out_of_area_reported_before_503(monkeypatch):
    use_snapshot(monkeypatch, None)
    res = client.post("/api/route", json={"start": START, "end": {"lat": 37.51, "lng": 127.05}})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "OUT_OF_PILOT_AREA"


def test_risk_layer_requires_bbox():
    assert client.get("/api/risk-layer").status_code == 422
    res = client.get("/api/risk-layer", params={"bbox": "127.02,37.49,127.03,37.50"})
    assert res.status_code == 200
    body = res.json()
    assert body["type"] == "FeatureCollection"
    assert body["features"] == []  # 비가 없으면 모두 '안전' → 뺌
    assert "mock" not in body


@pytest.mark.parametrize("bbox", ["127.02,37.49,127.03", "a,b,c,d", "127.03,37.49,127.02,37.50"])
def test_risk_layer_bad_bbox(bbox):
    res = client.get("/api/risk-layer", params={"bbox": bbox})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_REQUEST"


def test_risk_layer_503_without_snapshot(monkeypatch):
    use_snapshot(monkeypatch, None)
    res = client.get("/api/risk-layer", params={"bbox": "127.02,37.49,127.03,37.50"})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_observations_badges():
    kinds = {b["kind"] for b in client.get("/api/observations").json()["badges"]}
    assert kinds == {"confirmed", "estimated"}