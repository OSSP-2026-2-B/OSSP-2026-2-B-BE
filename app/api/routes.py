"""프론트 연동용 API. /route 는 실제 경로 계산,
/risk-layer·/observations 는 위험도 캐시 연결 전까지 mock."""

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from routing.service import RiskInput, plan_routes

router = APIRouter(prefix="/api")

Mode = Literal["shortest", "balanced", "safety"]


class LatLng(BaseModel):
    lat: float
    lng: float


class RouteRequest(BaseModel):
    start: LatLng
    end: LatLng
    modes: list[Mode] = Field(default=["shortest", "balanced"])


def current_risk() -> RiskInput:
    # TODO: risk.cache.get_snapshot() 머지 후 연결
    return RiskInput()


@router.post("/route")
def route(req: RouteRequest):
    return plan_routes(req.start, req.end, req.modes, current_risk())


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