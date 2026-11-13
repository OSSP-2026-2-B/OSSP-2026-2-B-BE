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
    assert body["rain"] == {"rain_1h_mm": 30.0, "updated_at": snap["rain"]["updated_at"], "scenario": False}
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


def test_observations_503_without_snapshot(monkeypatch):
    use_snapshot(monkeypatch, None)
    res = client.get("/api/observations")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "DATA_UNAVAILABLE"

# ---------- rain_mm 시나리오 (발표 강우 슬라이더) ----------
BBOX = {"bbox": "127.01,37.48,127.05,37.51"}


def test_route_rain_mm_works_without_live_data(monkeypatch):
    use_snapshot(monkeypatch, None)  # 실시간 강우 없음 → 평소라면 503
    res = client.post("/api/route", json={"start": START, "end": END, "rain_mm": 60})
    assert res.status_code == 200
    body = res.json()
    assert body["rain"] == {"rain_1h_mm": 60.0, "updated_at": None, "scenario": True}
    shortest, balanced = body["routes"]
    assert shortest["walk_speed_mps"] == 0.9            # 시나리오 강우로 보행 속도도 바뀜
    assert shortest["hazards"]                          # 60mm → 침수흔적 구간 고위험
    assert balanced["risk_segment_count"] < shortest["risk_segment_count"]


def test_route_rain_mm_zero_has_no_hazards():
    body = client.post("/api/route", json={"start": START, "end": END, "rain_mm": 0}).json()
    assert body["rain"]["scenario"] is True
    assert all(r["hazards"] == [] for r in body["routes"])


@pytest.mark.parametrize("rain_mm", [-1, 201, "많이"])
def test_route_rain_mm_validation(rain_mm):
    res = client.post("/api/route", json={"start": START, "end": END, "rain_mm": rain_mm})
    assert res.status_code == 422
    assert res.json()["error"]["details"][0]["field"] == "rain_mm"


def test_risk_layer_rain_mm(monkeypatch):
    use_snapshot(monkeypatch, None)
    body = client.get("/api/risk-layer", params={**BBOX, "rain_mm": 80}).json()
    assert body["scenario"] is True and body["updated_at"] is None
    props = [f["properties"] for f in body["features"]]
    assert "고위험" in {p["grade"] for p in props}
    excluded = [p for p in props if p["excluded"]]   # 72mm 이상 → 침수흔적 있는 지하 구간 통행 제외
    assert excluded and all(risk.cache.RULE2_REASON in p["reasons"] for p in excluded)
    # 60mm 는 72mm 미만이고 3시간 강우는 모름 → 규칙 2 없음
    body60 = client.get("/api/risk-layer", params={**BBOX, "rain_mm": 60}).json()
    assert not any(f["properties"]["excluded"] for f in body60["features"])


def test_risk_layer_live_is_not_scenario():
    body = client.get("/api/risk-layer", params=BBOX).json()
    assert body["scenario"] is False


def test_risk_layer_rain_mm_validation():
    res = client.get("/api/risk-layer", params={**BBOX, "rain_mm": -5})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_REQUEST"


# ---------- closures 통제 시나리오 (통제 시나리오 토글) ----------
def middle_of_route() -> dict:
    """비 없는 최단 경로 가운데 지점 (여기를 통제하면 다른 길로 돌아가야 함)"""
    body = client.post("/api/route", json={"start": START, "end": END, "rain_mm": 0}).json()
    coords = body["routes"][0]["geometry"]["coordinates"]
    lng, lat = coords[len(coords) // 2]
    return {"lat": lat, "lng": lng, "distance_m": body["routes"][0]["distance_m"]}


def test_route_closure_forces_detour():
    mid = middle_of_route()
    closure = {"lat": mid["lat"], "lng": mid["lng"]}
    body = client.post("/api/route", json={"start": START, "end": END, "rain_mm": 0,
                                           "closures": [closure]}).json()
    assert body["rain"]["scenario"] is True
    assert body["routes"][0]["distance_m"] > mid["distance_m"]   # 통제 지점을 피해 돌아감


def test_route_closure_at_destination_no_route():
    # 도착지 주변 30m 가 모두 막히면 경로 없음 → 임시주거시설 안내(fallback)를 시연할 수 있는 상황
    res = client.post("/api/route", json={"start": START, "end": END, "rain_mm": 0, "closures": [END]})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NO_ROUTE"


def test_route_closures_only_use_live_rain(monkeypatch):
    snap = fake_snapshot(rain_1h_mm=30.0)
    use_snapshot(monkeypatch, snap)
    body = client.post("/api/route", json={"start": START, "end": END, "closures": [END]})
    assert body.status_code == 404                               # 통제는 적용됨
    mid = middle_of_route()
    body = client.post("/api/route", json={"start": START, "end": END,
                                           "closures": [{"lat": mid["lat"], "lng": mid["lng"]}]}).json()
    assert body["rain"] == {"rain_1h_mm": 30.0, "updated_at": snap["rain"]["updated_at"], "scenario": True}


def test_route_closures_only_still_needs_live_data(monkeypatch):
    use_snapshot(monkeypatch, None)
    res = client.post("/api/route", json={"start": START, "end": END, "closures": [END]})
    assert res.status_code == 503                                # 강우는 실시간이 필요
    res = client.post("/api/route", json={"start": START, "end": END, "rain_mm": 0, "closures": [END]})
    assert res.status_code == 404                                # rain_mm 도 주면 503 없이 계산


def test_route_closures_validation():
    res = client.post("/api/route", json={"start": START, "end": END, "closures": [START] * 21})
    assert res.status_code == 422
    assert res.json()["error"]["details"][0]["field"] == "closures"


def test_risk_layer_closures():
    lng, lat = 127.0276, 37.4979
    body = client.get("/api/risk-layer", params={**BBOX, "closures": f"{lng},{lat}"}).json()
    assert body["scenario"] is True
    props = [f["properties"] for f in body["features"]]
    assert props and all(p["excluded"] and "도로 통제 (시나리오)" in p["reasons"] for p in props)


@pytest.mark.parametrize("closures", ["127.02", "a,b", "127.02,37.49;", ";".join(["127.02,37.49"] * 21)])
def test_risk_layer_closures_validation(closures):
    res = client.get("/api/risk-layer", params={**BBOX, "closures": closures})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_REQUEST"
