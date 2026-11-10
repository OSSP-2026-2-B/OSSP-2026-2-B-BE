"""실제 강남역 보행망(data/segments.gpkg)으로 경로 계산 확인"""

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.api.routes import LatLng
from app.main import app
from routing.astar import find_route
from routing.locate import in_pilot_area, to_lnglat, to_xy
from routing.service import RiskInput, get_graph, plan_routes

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
    assert shortest["walk_speed_mps"] == 0.9  # 30mm 이면 폭우 속도


def test_no_route():
    G, index = get_graph()
    dst, _ = index.nearest(*to_xy(END.lat, END.lng))
    blocked = {key for _, _, key in G.edges(dst, keys=True)}  # 도착 교차점의 모든 길 통제

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