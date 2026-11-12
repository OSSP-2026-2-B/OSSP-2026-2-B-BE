"""프론트 연동용 API.

/route·/risk-layer·/observations 모두 위험도 캐시(risk.cache.get_snapshot)를 읽어 계산한다.
"""

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.api.observations import build_observations, warning_cache
from risk import cache as risk_cache
from risk.layer import BBox, build_layer, segment_lines
from routing.service import RiskInput, check_pilot_area, plan_routes

router = APIRouter(prefix="/api")

Mode = Literal["shortest", "balanced", "safety"]


class LatLng(BaseModel):
    lat: float
    lng: float


class RouteRequest(BaseModel):
    start: LatLng
    end: LatLng
    modes: list[Mode] = Field(default=["shortest", "balanced"])


def current_snapshot() -> dict:
    """최신 위험도 스냅샷. 강우 자료를 쓸 수 없으면 503.

    서버를 켠 직후 첫 갱신 전이거나, 서울시 10분 강우의 마지막 정상값이 60분을 넘으면
    위험도를 믿을 수 없으므로 계산하지 않는다 (강우 0 으로 계산하면 위험한 길을 안전하다고 안내하게 됨).
    """
    snap = risk_cache.get_snapshot()
    if risk_cache.is_data_unavailable(snap):
        raise ApiError(503, "DATA_UNAVAILABLE", "강우 자료를 받을 수 없습니다.")
    return snap


def current_risk() -> RiskInput:
    snap = current_snapshot()
    return RiskInput(
        risk=snap["risk"],
        excluded=snap["excluded"],
        reasons=snap["reasons"],
        rain_1h_mm=snap["rain"]["rain_1h_mm"],
        updated_at=snap["rain"]["updated_at"],
    )


@router.post("/route")
def route(req: RouteRequest):
    check_pilot_area(req.start, req.end)  # 요청 자체가 잘못된 경우(400)를 강우 자료 확인(503)보다 먼저 알린다
    return plan_routes(req.start, req.end, req.modes, current_risk())


def parse_bbox(text: str) -> BBox:
    """'최소경도,최소위도,최대경도,최대위도' → 숫자 4개. 형식이 틀리면 422"""
    try:
        v = [float(x) for x in text.split(",")]
    except ValueError:
        v = []
    if len(v) != 4 or not (v[0] < v[2] and v[1] < v[3]):
        raise ApiError(422, "INVALID_REQUEST",
                       "bbox 는 '최소경도,최소위도,최대경도,최대위도' 형식이어야 합니다.")
    return v[0], v[1], v[2], v[3]


@router.get("/risk-layer")
def risk_layer(bbox: str = Query(..., description="최소경도,최소위도,최대경도,최대위도")):
    box = parse_bbox(bbox)
    return build_layer(current_snapshot(), box, segment_lines())


@router.get("/observations")
def observations():
    return build_observations(current_snapshot(), warning_cache.get())