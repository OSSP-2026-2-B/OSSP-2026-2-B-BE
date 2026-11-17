"""서울시 실시간 돌발 정보 (서울 열린데이터광장 AccInfo, OA-13315) → 통행 제외 규칙 1 의 통제 지점.

ITS 는 클라우드 서버 IP 를 막아 배포 서버에서 호출되지 않으므로 이 데이터로 대신한다.
서울 전체 돌발 상황 중 시범 지역 사각형 안의 전면통제·땅꺼짐(도로침하) 지점만 고른다.
좌표는 GRS80 TM(EPSG:5181)으로 오므로 경위도와 EPSG:5186 미터 좌표로 바꿔서 돌려준다.
(필드 이름은 2026-11-16 실제 응답으로 확인: acc_type·acc_dtype·grs80tm_x·grs80tm_y·acc_info)
"""

import xml.etree.ElementTree as ET

from pyproj import Transformer

from app.config import Settings, settings
from collectors.common import CollectorError, http_get, is_live, read_fixture
from collectors.its_control import FULL_BLOCK_WORDS, SINKHOLE_WORDS, ControlPoint, pilot_bbox

URL = "http://openapi.seoul.go.kr:8088/{key}/xml/AccInfo/1/{rows}/"
FIXTURE = "seoul_incident.xml"
ROWS = 1000                # 한 번에 받을 수 있는 최대 건수 (서울 전체 돌발은 보통 수십 건)
SINKHOLE_CODE = "09B15"    # 돌발 세부 유형: 도로침하
NO_DATA = "INFO-200"       # 해당하는 데이터 없음
CONSTRUCTION_TYPE = "A04"  # 돌발 유형: 공사 (차도 통제라 보도는 통제되지 않아 전면통제로 보지 않음)

_to_lnglat = Transformer.from_crs("EPSG:5181", "EPSG:4326", always_xy=True)
_to_5186 = Transformer.from_crs("EPSG:5181", "EPSG:5186", always_xy=True)


def classify(row: dict) -> str | None:
    text = row.get("acc_info", "")
    if row.get("acc_dtype") == SINKHOLE_CODE or any(w in text for w in SINKHOLE_WORDS):
        return "땅꺼짐"
    if row.get("acc_type") == CONSTRUCTION_TYPE:
        return None
    if any(w in text for w in FULL_BLOCK_WORDS):
        return "전면통제"
    return None


def parse(text: str, bbox: tuple[float, float, float, float]) -> list[ControlPoint]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise CollectorError(f"서울시 돌발 정보 응답 해석 실패: {text[:200]}") from e
    code = root.findtext(".//RESULT/CODE") or root.findtext("CODE")
    if code == NO_DATA:
        return []
    if code != "INFO-000":
        msg = root.findtext(".//RESULT/MESSAGE") or root.findtext("MESSAGE")
        raise CollectorError(f"서울시 돌발 정보 오류: {code} {msg}")

    min_x, min_y, max_x, max_y = bbox
    out = []
    for el in root.iter("row"):
        row = {c.tag: (c.text or "").strip() for c in el}
        kind = classify(row)
        if kind is None:
            continue
        try:
            tm_x, tm_y = float(row["grs80tm_x"]), float(row["grs80tm_y"])
        except (KeyError, ValueError):
            continue
        lng, lat = _to_lnglat.transform(tm_x, tm_y)
        if not (min_x <= lng <= max_x and min_y <= lat <= max_y):
            continue
        x, y = _to_5186.transform(tm_x, tm_y)
        out.append(ControlPoint(x, y, round(lng, 6), round(lat, 6), kind, row.get("acc_info", "")))
    return out


def fetch(s: Settings = settings) -> list[ControlPoint]:
    bbox = pilot_bbox(s)
    if is_live(s):
        if not s.seoul_opendata_key:
            raise CollectorError("SEOUL_OPENDATA_KEY 가 없습니다")
        return parse(http_get(URL.format(key=s.seoul_opendata_key, rows=ROWS)), bbox)
    return parse(read_fixture(FIXTURE), bbox)