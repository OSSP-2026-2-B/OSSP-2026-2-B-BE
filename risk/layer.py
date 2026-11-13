"""GET /api/risk-layer 응답 만들기 (docs/api.md 2번)

지도 화면 범위(bbox) 안의 구간별 위험 등급을 GeoJSON FeatureCollection 으로 돌려준다.

- 위험도 스냅샷(get_snapshot 또는 rain_mm·closures 시나리오)의 risk·excluded·reasons 를 그대로 쓴다.
- '안전'(0.3 미만) 구간은 뺀다. 단, 통행 제외 구간은 등급과 관계없이 넣는다
  (예: 침수이력이 없는 길이라도 도로 통제 지점 근처면 지도에 보여야 한다).
- 구간 좌표는 미터 좌표(EPSG:5186) → 경위도(EPSG:4326) 변환을 처음 한 번만 한다.
- bbox 판정은 구간의 외곽 사각형이 bbox 와 겹치는지로 한다 (구간이 25m 이하라 충분히 정확).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

from app.config import Settings, settings
from routing.cost import risk_grade

BBox = tuple[float, float, float, float]  # 최소경도, 최소위도, 최대경도, 최대위도


@dataclass(frozen=True)
class SegmentLine:
    coords: list[list[float]]  # GeoJSON 순서 [[경도, 위도], ...] (소수 6자리 ≈ 10cm)
    bounds: BBox                # 이 구간의 외곽 사각형


def to_lines(gdf) -> dict[int, SegmentLine]:
    """segment_id·geometry(EPSG:5186) GeoDataFrame → {segment_id: 경위도 선}"""
    wgs = gdf.to_crs("EPSG:4326")
    return {
        int(sid): SegmentLine([[round(x, 6), round(y, 6)] for x, y in geom.coords], geom.bounds)
        for sid, geom in zip(wgs["segment_id"], wgs.geometry, strict=True)
    }


@lru_cache(maxsize=1)
def segment_lines() -> dict[int, SegmentLine]:
    """실제 보행망 구간 선. 위험도 캐시가 이미 읽어 둔 segments.gpkg 를 재사용한다."""
    from risk.cache import get_static

    return to_lines(get_static().gdf)


def _overlaps(a: BBox, b: BBox) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def build_layer(
    snapshot: Mapping, bbox: BBox, lines: Mapping[int, SegmentLine], s: Settings = settings
) -> dict:
    risk, excluded, reasons = snapshot["risk"], snapshot["excluded"], snapshot["reasons"]
    features = []
    # 위험도 > 0 이거나 통행 제외인 구간만 후보 (나머지는 위험도 0 = '안전')
    for sid in sorted(set(risk) | set(excluded)):
        line = lines.get(sid)
        if line is None or not _overlaps(line.bounds, bbox):
            continue
        r = risk.get(sid, 0.0)
        grade = risk_grade(r, s)
        is_excluded = sid in excluded
        if grade == "안전" and not is_excluded:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": line.coords},
            "properties": {
                "segment_id": sid,
                "risk": round(r, 3),
                "grade": grade,
                "excluded": is_excluded,
                "reasons": reasons.get(sid, []),
            },
        })
    return {
        "type": "FeatureCollection",
        "updated_at": snapshot["rain"]["updated_at"],  # /api/route 의 rain.updated_at 과 같은 값
        "scenario": bool(snapshot.get("scenario")),   # rain_mm·closures 시나리오면 True
        "features": features,
    }
