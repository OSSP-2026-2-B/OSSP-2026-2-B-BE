"""프론트 연동용 API.

/route·/risk-layer·/observations 모두 위험도 캐시(risk.cache.get_snapshot)를 읽어 계산한다.
/route 본문·/risk-layer 쿼리에 rain_mm(선택)을 주면 실시간 강우 대신 그 값으로 계산한다 (발표 강우 슬라이더).
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
RAIN_MM_MAX = 200.0   # 시나리오 강우 상한 (mm/h). 서울 1시간 최대 기록은 약 141mm (2022-08-08)
RAIN_MM_DESC = "시나리오 1시간 강우(mm). 주면 실시간 강우 대신 이 값으로 위험도를 계산한다"


class LatLng(BaseModel):
    lat: float
    lng: float


class RouteRequest(BaseModel):
    start: LatLng
    end: LatLng
    modes: list[Mode] = Field(default=["shortest", "balanced"])
    rain_mm: float | None = Field(default=None, ge=0, le=RAIN_MM_MAX, description=RAIN_MM_DESC)


def current_snapshot() -> dict:
    """최신 위험도 스냅샷. 강우 자료를 쓸 수 없으면 503.

    서버를 켠 직후 첫 갱신 전이거나, 서울시 10분 강우의 마지막 정상값이 60분을 넘으면
    위험도를 믿을 수 없으므로 계산하지 않는다 (강우 0 으로 계산하면 위험한 길을 안전하다고 안내하게 됨).
    """
    snap = risk_cache.get_snapshot()
    if risk_cache.is_data_unavailable(snap):
        raise ApiError(503, "DATA_UNAVAILABLE", "강우 자료를 받을 수 없습니다.")
    return snap


def snapshot_for(rain_mm: float | None) -> dict:
    """rain_mm 이 없으면 실시간 스냅샷(503 검사), 있으면 그 강우로 계산한 시나리오 스냅샷.

    시나리오는 실시간 강우가 필요 없으므로 503 검사를 하지 않는다
    → 수집이 끊기거나 서버를 막 켰을 때도 발표 시연은 동작한다. 계산은 요청마다 약 25ms.
    """
    if rain_mm is None:
        return current_snapshot()
    return risk_cache.build_scenario(rain_mm)


def risk_input(snap: dict) -> RiskInput:
    return RiskInput(
        risk=snap["risk"],
        excluded=snap["excluded"],
        reasons=snap["reasons"],
        rain_1h_mm=snap["rain"]["rain_1h_mm"],
        updated_at=snap["rain"]["updated_at"],
        scenario=risk_cache.is_scenario(snap),
    )


@router.post("/route")
def route(req: RouteRequest):
    check_pilot_area(req.start, req.end)  # 요청 자체가 잘못된 경우(400)를 강우 자료 확인(503)보다 먼저 알린다
    return plan_routes(req.start, req.end, req.modes, risk_input(snapshot_for(req.rain_mm)))


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
def risk_layer(
    bbox: str = Query(..., description="최소경도,최소위도,최대경도,최대위도"),
    rain_mm: float | None = Query(None, ge=0, le=RAIN_MM_MAX, description=RAIN_MM_DESC),
):
    box = parse_bbox(bbox)
    return build_layer(snapshot_for(rain_mm), box, segment_lines())


@router.get("/observations")
def observations():
    return build_observations(current_snapshot(), warning_cache.get())