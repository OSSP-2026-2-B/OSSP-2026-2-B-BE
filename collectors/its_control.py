"""국가교통정보센터 돌발상황정보 → 통행 제외 규칙 1 의 통제 지점.

시범 지역 사각형 안의 돌발상황 중 전면통제이거나 땅꺼짐(싱크홀·지반침하) 인 지점만 고른다.
좌표는 경위도(WGS84)로 오므로 위험도 계산용 EPSG:5186 미터 좌표로 바꿔서 돌려준다.
"""

import json
import math
from dataclasses import dataclass

from pyproj import Transformer

from app.config import Settings, settings
from collectors.common import CollectorError, http_get, is_live, read_fixture

URL = "https://openapi.its.go.kr:9443/eventInfo"
FIXTURE = "its_events.json"
FULL_BLOCK_WORDS = ("전면통제", "전면 통제", "전면차단")
SINKHOLE_WORDS = ("땅꺼짐", "싱크홀", "지반침하", "도로침하", "포트홀 대형")

_to_5186 = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)


@dataclass(frozen=True)
class ControlPoint:
    x: float          # EPSG:5186
    y: float
    lng: float
    lat: float
    kind: str         # "전면통제" / "땅꺼짐"
    message: str


def pilot_bbox(s: Settings = settings) -> tuple[float, float, float, float]:
    """시범 지역 원(그래프 수집 반경 포함)을 덮는 경위도 사각형 (minX, minY, maxX, maxY)"""
    r = s.its_bbox_radius_m
    dlat = r / 111_320
    dlng = r / (111_320 * math.cos(math.radians(s.pilot_center_lat)))
    return (s.pilot_center_lon - dlng, s.pilot_center_lat - dlat,
            s.pilot_center_lon + dlng, s.pilot_center_lat + dlat)


def classify(item: dict) -> str | None:
    text = " ".join(str(item.get(k) or "") for k in ("lanesBlocked", "lanesBlockType", "message",
                                                      "eventDetailType", "eventType"))
    if any(w in text for w in SINKHOLE_WORDS):
        return "땅꺼짐"
    if any(w in text for w in FULL_BLOCK_WORDS):
        return "전면통제"
    return None


def parse(text: str, bbox: tuple[float, float, float, float]) -> list[ControlPoint]:
    try:
        body = json.loads(text)
        items = body["body"]["items"] or []
    except (ValueError, KeyError, TypeError) as e:
        raise CollectorError(f"ITS 응답 해석 실패: {text[:200]}") from e
    if isinstance(items, dict):     # 한 건이면 리스트가 아니라 객체로 오는 경우 대비
        items = [items]
    min_x, min_y, max_x, max_y = bbox
    out = []
    for it in items:
        kind = classify(it)
        if kind is None:
            continue
        try:
            lng, lat = float(it["coordX"]), float(it["coordY"])
        except (KeyError, ValueError, TypeError):
            continue
        if not (min_x <= lng <= max_x and min_y <= lat <= max_y):
            continue
        x, y = _to_5186.transform(lng, lat)
        out.append(ControlPoint(x, y, lng, lat, kind, str(it.get("message") or "")))
    return out


def fetch(s: Settings = settings) -> list[ControlPoint]:
    bbox = pilot_bbox(s)
    if is_live(s):
        if not s.its_api_key:
            raise CollectorError("ITS_API_KEY 가 없습니다")
        params = {
            "apiKey": s.its_api_key, "type": "all", "eventType": "all", "getType": "json",
            "minX": bbox[0], "minY": bbox[1], "maxX": bbox[2], "maxY": bbox[3],
        }
        return parse(http_get(URL, params), bbox)
    return parse(read_fixture(FIXTURE), bbox)
