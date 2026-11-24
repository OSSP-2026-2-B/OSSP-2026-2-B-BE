"""실제 강남역 보행망(data/segments.gpkg)으로 경로 계산 확인"""

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.api.routes import LatLng
from app.config import Settings
from app.main import app
from routing.astar import RouteResult, find_route
from routing.locate import in_pilot_area, to_lnglat, to_xy
from routing.service import RiskInput, danger_sections, get_graph, plan_routes

START = LatLng(lat=37.4979, lng=127.0276)  # 강남역
END = LatLng(lat=37.5045, lng=127.0250)    # 북쪽 약 800m


def shortest_segments() -> list[int]:
    G, index = get_graph()
    src, _ = index.nearest(*to_xy(START.lat, START.lng))
    dst, _ = index.nearest(*to_xy(END.lat, END.lng))
    return find_route(G, src, dst, alpha=0).segment_ids


def test_coordinate_round_trip():
    x, y = to_xy(START.lat, START.lng)
    assert to_lnglat(x, y) == pytest.approx([START.lng, START.lat], abs=1e-6)


def test_pilot_area():
    assert in_pilot_area(37.4979, 127.0276)
    assert not in_pilot_area(37.51, 127.05)


def test_route_without_risk():
    shortest, balanced = plan_routes(START, END, ["shortest", "balanced"], RiskInput())["routes"]
    assert 700 < shortest["distance_m"] < 1000
    assert balanced["distance_m"] == shortest["distance_m"]  # 위험 0 이면 같은 경로
    assert shortest["route_grade"] == "안전"
    
    coords = shortest["geometry"]["coordinates"]
    assert coords[0] == [START.lng, START.lat]
    assert coords[-1] == [END.lng, END.lat]
    assert len(coords) > 2


def test_balanced_avoids_risk():
    risk = {seg: 0.9 for seg in shortest_segments()}
    ri = RiskInput(risk=risk, rain_1h_mm=30)
    shortest, balanced = plan_routes(START, END, ["shortest", "balanced"], ri)["routes"]

    assert balanced["risk_segment_count"] < shortest["risk_segment_count"]
    assert balanced["distance_m"] > shortest["distance_m"]
    assert shortest["hazards"][0]["grade"] == "고위험"
    assert shortest["route_grade"] == "위험"  # 경로 전체(700m 이상)가 위험 구간
    assert shortest["walk_speed_mps"] == 0.9  # 30mm 이면 폭우 속도


def test_caution_only_route_grade():
    # 최단 경로 전체가 '주의'(0.4) 이면 위험 구간은 없어도 주의 구간 700m 이상 → 경로 '주의'
    risk = {seg: 0.4 for seg in shortest_segments()}
    (shortest,) = plan_routes(START, END, ["shortest"], RiskInput(risk=risk, rain_1h_mm=40))["routes"]
    assert shortest["danger_length_m"] == 0
    assert isinstance(shortest["caution_length_m"], int)
    assert shortest["caution_length_m"] == shortest["distance_m"]
    assert shortest["route_grade"] == "주의"


def test_no_route_gives_shelter():
    G, index = get_graph()
    dst, _ = index.nearest(*to_xy(END.lat, END.lng))
    blocked = {key for _, _, key in G.edges(dst, keys=True)}  # 도착 교차점의 모든 길 통제

    out = plan_routes(START, END, ["balanced"], RiskInput(excluded=blocked))
    fb = out["fallback"]
    assert out["routes"] == []
    assert fb["type"] == "shelter"
    assert fb["reachable"] is True
    assert fb["duration_min"] > 0
    coords = fb["geometry"]["coordinates"]
    assert coords[0] == [START.lng, START.lat]   # 출발 지점에서 시작
    assert coords[-1] == fb["location"]           # 시설에서 끝남


def test_shelter_unreachable_gives_straight_distance():
    G, index = get_graph()
    src, _ = index.nearest(*to_xy(START.lat, START.lng))
    blocked = {key for _, _, key in G.edges(src, keys=True)}  # 출발 교차점의 모든 길 통제

    fb = plan_routes(START, END, ["balanced"], RiskInput(excluded=blocked))["fallback"]
    assert fb["reachable"] is False
    assert fb["geometry"] is None
    assert fb["duration_min"] is None
    assert fb["distance_m"] > 0


def test_no_route_without_shelters(monkeypatch):
    monkeypatch.setattr("routing.service.get_shelters", lambda: [])
    G, index = get_graph()
    dst, _ = index.nearest(*to_xy(END.lat, END.lng))
    blocked = {key for _, _, key in G.edges(dst, keys=True)}

    with pytest.raises(ApiError) as e:
        plan_routes(START, END, ["balanced"], RiskInput(excluded=blocked))
    assert e.value.code == "NO_ROUTE"

def test_out_of_pilot_area_api():
    res = TestClient(app).post("/api/route", json={
        "start": {"lat": START.lat, "lng": START.lng},
        "end": {"lat": 37.51, "lng": 127.05},
    })
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "OUT_OF_PILOT_AREA"


def _result(lengths):
    n = len(lengths)
    return RouteResult(nodes=list(range(n + 1)), segment_ids=list(range(1, n + 1)), distance_m=sum(lengths),
                       cost=0, risk_segment_count=0, max_risk=0, segment_lengths=lengths)


def test_danger_sections_merge_short_gaps():
    S = Settings(_env_file=None)
    xy = [(i * 20.0, 0.0) for i in range(8)]
    # 위험 위험 안전 위험 안전 안전 주의 → 20m 틈은 같은 곳, 40m 틈 뒤 주의는 세지 않음
    r = _result([20.0] * 7)
    ri = RiskInput(risk={1: 0.9, 2: 0.9, 4: 0.7, 7: 0.4},
                   reasons={1: ["침수흔적 1회 (2022)"], 4: ["지하보도"]})
    (sec,) = danger_sections(r, xy, ri, S)
    assert sec["segment_ids"] == [1, 2, 4]
    assert sec["length_m"] == 60                       # 위험 구간 길이만 (사이 안전 20m 제외)
    assert sec["grade"] == "고위험"
    assert sec["reasons"] == ["침수흔적 1회 (2022)", "지하보도"]

    # 틈이 section_gap_m(25m) 보다 길면 다른 곳
    r = _result([20.0, 30.0, 20.0])
    secs = danger_sections(r, xy, RiskInput(risk={1: 0.9, 3: 0.65}), S)
    assert [s["segment_ids"] for s in secs] == [[1], [3]]
    assert [s["grade"] for s in secs] == ["고위험", "위험"]
    assert danger_sections(r, xy, RiskInput(), S) == []


def test_route_has_sections_and_areas():
    risk = {seg: 0.9 for seg in shortest_segments()}
    out = plan_routes(START, END, ["shortest", "balanced"], RiskInput(risk=risk, rain_1h_mm=30))
    shortest = out["routes"][0]
    assert shortest["danger_section_count"] == 1       # 최단 경로 전체가 위험 → 한 곳
    assert shortest["danger_sections"][0]["length_m"] == shortest["danger_length_m"]
    assert out["risk_areas"]["type"] == "FeatureCollection"
    assert out["risk_areas"]["features"]
    assert plan_routes(START, END, ["shortest"], RiskInput())["risk_areas"]["features"] == []
