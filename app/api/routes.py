"""프론트 연동용 API. 지금은 docs/api.md 예시를 돌려주는 mock 이며,
경로 탐색·수집기가 완성되면 실제 값으로 바꾼다 ("mock": true 로 구분)."""

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api")

Mode = Literal["shortest", "balanced", "safety"]


class LatLng(BaseModel):
    lat: float
    lng: float


class RouteRequest(BaseModel):
    start: LatLng
    end: LatLng
    modes: list[Mode] = Field(default=["shortest", "balanced"])


MOCK_ROUTES = {
    "shortest": {
        "distance_m": 1200, "duration_min": 19, "walk_speed_mps": 1.05, "detour_min": 0,
        "risk_segment_count": 3, "max_grade": "고위험",
        "hazards": [{
            "segment_id": 1024, "grade": "고위험", "risk": 0.85,
            "location": [127.0265, 37.4998], "reasons": ["침수흔적 3회 (2010·2011·2022)"],
        }],
    },
    "balanced": {
        "distance_m": 1500, "duration_min": 24, "walk_speed_mps": 1.05, "detour_min": 5,
        "risk_segment_count": 0, "max_grade": "주의", "hazards": [],
    },
    "safety": {
        "distance_m": 1560, "duration_min": 25, "walk_speed_mps": 1.05, "detour_min": 6,
        "risk_segment_count": 0, "max_grade": "주의", "hazards": [],
    },
}


@router.post("/route")
def route(req: RouteRequest):
    line = [[req.start.lng, req.start.lat], [req.end.lng, req.end.lat]]
    return {
        "mock": True,
        "rain": {"rain_1h_mm": 48.0, "updated_at": "2026-10-28T19:50:00+09:00"},
        "routes": [
            {"mode": m, **MOCK_ROUTES[m], "geometry": {"type": "LineString", "coordinates": line}}
            for m in req.modes
        ],
        "fallback": None,
    }


@router.get("/risk-layer")
def risk_layer(bbox: str = Query(..., description="최소경도,최소위도,최대경도,최대위도")):
    return {
        "mock": True,
        "type": "FeatureCollection",
        "updated_at": "2026-10-28T19:50:00+09:00",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[127.0276, 37.4979], [127.0279, 37.4981]]},
            "properties": {
                "segment_id": 1024, "risk": 0.85, "grade": "고위험", "excluded": False,
                "reasons": ["침수흔적 3회 (2010·2011·2022)"],
            },
        }],
    }


@router.get("/observations")
def observations():
    return {
        "mock": True,
        "updated_at": "2026-10-28T19:50:00+09:00",
        "delayed": False,
        "rain_1h_mm": 38.0,
        "rain_3h_mm": 61.5,
        "rain_15m_mm": 22.0,
        "forecast_1h_mm": 45.0,
        "badges": [
            {"kind": "confirmed", "label": "호우경보", "source": "기상청 기상특보",
             "issued_at": "2026-10-28T19:40:00+09:00"},
            {"kind": "estimated", "label": "침수예보 기준 충족", "source": "방재 AWS 15분 강우",
             "issued_at": None},
        ],
    }