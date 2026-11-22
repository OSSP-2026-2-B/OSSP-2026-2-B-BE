"""위험 면 (risk/areas.py): 가상 구간으로 묶기·등급 우선순위·주변 거르기를 확인하고,
실제 강남역 보행망으로 /api/risk-layer?shape=area · /api/route 의 risk_areas 를 확인"""

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, Polygon

import risk.cache
from app.config import Settings
from app.main import app
from risk.areas import area_candidates, build_areas, merge_reasons, segments_near
from risk.cache import RiskCache
from risk.layer import build_area_layer, to_lines
from routing.locate import to_xy

S = Settings(_env_file=None)
X0, Y0 = to_xy(37.4979, 127.0276)  # 강남역 (미터 좌표)
ALL = (127.0, 37.4, 127.1, 37.6)


def geoms() -> dict[int, LineString]:
    """1~4: 강남역에서 동쪽으로 25m 씩 이어지는 구간
    5: 1 과 8m 떨어져 나란한 보도 / 9: 500m 북쪽 외딴 구간"""
    out = {i + 1: LineString([(X0 + 25 * i, Y0), (X0 + 25 * (i + 1), Y0)]) for i in range(4)}
    out[5] = LineString([(X0, Y0 + 8), (X0 + 25, Y0 + 8)])
    out[9] = LineString([(X0, Y0 + 500), (X0 + 25, Y0 + 500)])
    return out


def areas(seg_ids, risk, excluded=(), reasons=None):
    return build_areas(seg_ids, risk, set(excluded), reasons or {}, geoms(), S)


def polygon(feature) -> Polygon:
    """경위도 면 → 미터 좌표 면 (면적·교집합 비교용)"""
    ext, *holes = feature["geometry"]["coordinates"]
    to_m = lambda ring: [to_xy(lat, lng) for lng, lat in ring]  # noqa: E731
    return Polygon(to_m(ext), [to_m(h) for h in holes])


def test_connected_segments_become_one_area():
    out = areas([1, 2, 3, 4, 5], {1: 0.85, 2: 0.85, 3: 0.85, 4: 0.85, 5: 0.85},
                reasons={1: ["침수흔적 2회 (2011·2022)"], 2: ["침수흔적 2회 (2011·2022)"], 5: ["지하보도"]})
    assert len(out) == 1                     # 이어진 구간 + 나란한 보도 → 면 하나
    f = out[0]
    assert f["type"] == "Feature" and f["geometry"]["type"] == "Polygon"
    props = f["properties"]
    assert props["grade"] == "고위험" and props["excluded"] is False
    assert props["length_m"] == 125          # 25m × 5
    assert props["reasons"] == ["침수흔적 2회 (2011·2022)", "지하보도"]  # 많은 구간에 붙은 태그부터
    lng, lat = props["center"]
    assert polygon(f).buffer(0.5).contains(Point(to_xy(lat, lng)))   # 버튼 위치는 면 안쪽


def test_caution_and_safe_not_drawn():
    assert areas([1, 2, 3], {1: 0.2, 2: 0.45, 3: 0.59}) == []   # 안전·주의는 면으로 그리지 않음
    assert area_candidates({1: 0.2, 2: 0.45, 3: 0.6, 4: 0.85}, {7}, S) == {3, 4, 7}


def test_grades_do_not_overlap_and_excluded_wins():
    risk = {1: 0.7, 2: 0.7, 3: 0.85, 4: 0.85}   # 위험 위험 고위험 고위험
    out = areas([1, 2, 3, 4], risk, excluded={4})
    by = {(f["properties"]["grade"], f["properties"]["excluded"]): f for f in out}
    assert set(by) == {("위험", False), ("고위험", False), ("고위험", True)}

    polys = [polygon(f) for f in out]
    for i, a in enumerate(polys):
        for b in polys[i + 1:]:
            assert a.intersection(b).area < 1.0    # 경계선만 맞닿고 겹치지 않음 (단순화 오차 < 1㎡)
    assert by[("고위험", True)]["properties"]["length_m"] == 25


def test_far_segments_are_separate_areas():
    out = areas([1, 9], {1: 0.85, 9: 0.85})
    assert len(out) == 2
    assert [f["properties"]["area_id"] for f in out] == [1, 2]


def test_segments_near_route():
    route = LineString([(X0 - 50, Y0 - 20), (X0 + 150, Y0 - 20)])   # 구간 1~4 바로 남쪽을 지나는 경로
    g = geoms()
    assert segments_near([route], [1, 2, 9], g, 150) == [1, 2]   # 500m 떨어진 9 는 빠짐
    assert segments_near([], [1, 2], g, 150) == []
    assert segments_near([route], [404], g, 150) == []            # 모르는 구간은 무시


def test_merge_reasons_by_frequency():
    assert merge_reasons([["A"], ["B", "A"], ["B"], ["B"], []]) == ["B", "A"]
    assert merge_reasons([["A", "A"]]) == ["A"]


def test_area_layer_bbox_and_shape():
    lines = to_lines(_gdf())
    snap = {"risk": {1: 0.85, 2: 0.4, 9: 0.85}, "excluded": set(), "reasons": {},
            "rain": {"updated_at": "2026-11-11T14:20:00+09:00"}}
    out = build_area_layer(snap, ALL, lines, geoms(), S)
    assert out["type"] == "FeatureCollection" and out["scenario"] is False
    assert len(out["features"]) == 2               # 1, 9 (2 는 주의라 빠짐)

    south = (127.0, 37.4, 127.1, lines[1].bounds[3] + 1e-4)   # 9(500m 북쪽) 가 빠지는 범위
    assert len(build_area_layer(snap, south, lines, geoms(), S)["features"]) == 1


def _gdf():
    import geopandas as gpd

    g = geoms()
    return gpd.GeoDataFrame({"segment_id": list(g)}, geometry=list(g.values()), crs="EPSG:5186")


# ---------- 실제 보행망 + fixture 캐시 → API ----------
@pytest.fixture
def fixture_cache(monkeypatch):
    cache = RiskCache(Settings(_env_file=None, collector_mode="fixture"))
    cache.update()
    monkeypatch.setattr(risk.cache, "_cache", cache)
    return cache


def test_risk_layer_area_from_fixture_cache(fixture_cache):
    client = TestClient(app)
    bbox = {"bbox": "127.016,37.492,127.036,37.507"}
    lines = client.get("/api/risk-layer", params=bbox).json()["features"]
    body = client.get("/api/risk-layer", params={**bbox, "shape": "area"}).json()
    feats = body["features"]

    assert body["updated_at"] == fixture_cache.get_snapshot()["rain"]["updated_at"]
    assert 0 < len(feats) < len(lines) / 50       # 구간 수천 개 → 면 몇 개
    assert {f["geometry"]["type"] for f in feats} == {"Polygon"}
    assert {f["properties"]["grade"] for f in feats} <= {"위험", "고위험"}   # 주의 없음
    assert any(f["properties"]["excluded"] for f in feats)                # 강남역 사거리 통제
    assert client.get("/api/risk-layer", params={**bbox, "shape": "dot"}).status_code == 422


def test_route_sections_and_areas_from_fixture_cache(fixture_cache):
    res = TestClient(app).post("/api/route", json={   # 신논현역 5번 출구 → 역삼초등학교
        "start": {"lat": 37.5043, "lng": 127.0248}, "end": {"lat": 37.4962, "lng": 127.033},
        "modes": ["shortest", "balanced"],
    })
    assert res.status_code == 200
    body = res.json()
    for r in body["routes"]:
        secs = r["danger_sections"]
        assert r["danger_section_count"] == len(secs)
        assert r["danger_section_count"] <= r["risk_segment_count"]
        seg_ids = [s for sec in secs for s in sec["segment_ids"]]
        assert seg_ids == [h["segment_id"] for h in r["hazards"]]           # 같은 구간을 경로 순서대로 묶음
        assert abs(sum(sec["length_m"] for sec in secs) - r["danger_length_m"]) <= len(secs)  # 반올림 오차만
    shortest = body["routes"][0]
    assert shortest["risk_segment_count"] > 10 and shortest["danger_section_count"] <= 3   # 수십 개 → 몇 곳

    feats = body["risk_areas"]["features"]
    assert feats and {f["properties"]["grade"] for f in feats} <= {"위험", "고위험"}
