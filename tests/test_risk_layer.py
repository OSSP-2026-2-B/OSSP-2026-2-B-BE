"""GET /api/risk-layer: 작은 가상 구간으로 응답 모양을 확인하고,
fixture 모드 수집기 + 실제 강남역 보행망으로 캐시 → API 전체 흐름을 확인"""

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString

import risk.cache
from app.config import Settings
from app.main import app
from risk.cache import RiskCache
from risk.layer import build_layer, to_lines
from routing.locate import to_lnglat, to_xy

S = Settings(_env_file=None)
X0, Y0 = to_xy(37.4979, 127.0276)  # 강남역 (미터 좌표)
ALL = (127.0, 37.4, 127.1, 37.6)    # 가상 구간을 모두 포함하는 bbox


def lines():
    # 강남역에서 동쪽으로 25m 씩 이어지는 구간 1~4
    gdf = gpd.GeoDataFrame(
        {"segment_id": [1, 2, 3, 4]},
        geometry=[LineString([(X0 + 25 * i, Y0), (X0 + 25 * (i + 1), Y0)]) for i in range(4)],
        crs="EPSG:5186",
    )
    return to_lines(gdf)


def snap(risk, excluded=(), reasons=None):
    return {
        "risk": risk, "excluded": set(excluded), "reasons": reasons or {},
        "rain": {"rain_1h_mm": 49.5, "updated_at": "2026-11-11T14:20:00+09:00"},
    }


def test_to_lines_lng_lat():
    line = lines()[1]
    assert line.coords[0] == to_lnglat(X0, Y0)
    assert line.coords[0] == pytest.approx([127.0276, 37.4979], abs=1e-6)
    assert line.bounds[0] < line.bounds[2]  # 동쪽으로 갈수록 경도 증가


def test_safe_segments_dropped_but_excluded_kept():
    s = snap(
        risk={1: 0.85, 2: 0.2, 3: 0.4},   # 고위험 / 안전 / 주의
        excluded={4},                      # 위험도 0 이지만 통행 제외
        reasons={1: ["침수흔적 2회 (2011·2022)"], 4: ["도로 통제 (전면통제)"]},
    )
    layer = build_layer(s, ALL, lines(), S)

    assert layer["type"] == "FeatureCollection"
    assert layer["updated_at"] == "2026-11-11T14:20:00+09:00"
    props = [f["properties"] for f in layer["features"]]
    assert [p["segment_id"] for p in props] == [1, 3, 4]   # 2(안전) 빠짐, 번호 순
    assert props[0] == {"segment_id": 1, "risk": 0.85, "grade": "고위험", "excluded": False,
                        "reasons": ["침수흔적 2회 (2011·2022)"]}
    assert props[1]["grade"] == "주의" and props[1]["reasons"] == []
    assert props[2]["grade"] == "안전" and props[2]["excluded"] is True
    assert layer["features"][0]["geometry"]["type"] == "LineString"


def test_bbox_filter():
    L = lines()
    s = snap(risk={1: 0.9, 2: 0.9, 3: 0.9, 4: 0.9})
    eps = 1e-7  # 경도 약 1cm. 이웃 구간은 끝점을 공유하므로 경계에서 살짝 안쪽으로

    def ids(min_lng, max_lng):
        return [f["properties"]["segment_id"]
                for f in build_layer(s, (min_lng, 37.4, max_lng, 37.6), L, S)["features"]]

    assert ids(127.0, L[1].bounds[2] - eps) == [1]
    assert ids(L[2].bounds[0] + eps, L[3].bounds[2] - eps) == [2, 3]
    assert ids(L[4].bounds[0] + eps, 127.1) == [4]
    assert ids(126.0, 126.1) == []


def test_unknown_segment_ignored():
    assert build_layer(snap(risk={999: 0.9}), ALL, lines(), S)["features"] == []


# ---------- 캐시 → API 전체 흐름 ----------
# fixture 모드 샘플: 1h 49.5mm, 15분 21.5mm, 강남역 사거리 전면통제 1건
@pytest.fixture
def fixture_cache(monkeypatch):
    cache = RiskCache(Settings(_env_file=None, collector_mode="fixture"))
    cache.update()
    monkeypatch.setattr(risk.cache, "_cache", cache)
    return cache


def test_risk_layer_from_fixture_cache(fixture_cache):
    snapshot = fixture_cache.get_snapshot()
    res = TestClient(app).get("/api/risk-layer", params={"bbox": "127.01,37.48,127.05,37.51"})
    assert res.status_code == 200
    body = res.json()
    assert "mock" not in body
    assert body["updated_at"] == snapshot["rain"]["updated_at"]

    props = [f["properties"] for f in body["features"]]
    grades = {p["grade"] for p in props}
    assert "고위험" in grades and "안전" not in grades   # 15분 21.5mm → 침수예보 조건 → H=1 구간 고위험
    excluded = [p for p in props if p["excluded"]]
    assert {p["segment_id"] for p in excluded} == snapshot["excluded"]  # 통제 지점 근처 구간 모두
    assert all("도로 통제 (전면통제)" in p["reasons"] for p in excluded)


def test_route_from_fixture_cache(fixture_cache):
    res = TestClient(app).post("/api/route", json={
        "start": {"lat": 37.4979, "lng": 127.0276}, "end": {"lat": 37.5045, "lng": 127.0250},
        "modes": ["shortest", "balanced"],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["rain"]["rain_1h_mm"] == 49.5
    shortest, balanced = body["routes"]
    assert shortest["walk_speed_mps"] == 0.9             # 20mm 이상 → 폭우 보행 속도
    assert shortest["hazards"]                           # 위험도가 실제로 반영됨 (캐시 연결 전에는 0)
    assert balanced["risk_segment_count"] < shortest["risk_segment_count"]
