"""위험 면 (risk_areas): 이어진 위험 구간을 면 하나로 묶어 지도에 그린다 (docs/api.md 1·2번)

25m 구간을 하나씩 선으로 그리면 이음매가 겹치고, 차도와 양쪽 보도가 굵은 띠가 되어 촘촘해 보인다.
- '위험' 등급 이상(위험도 0.6 이상)이거나 통행 제외인 구간만 쓴다. '주의'는 그리지 않는다
  (경로 카드의 위험 구간 수·통과 거리와 같은 기준).
- 구간을 area_buffer_m 만큼 부풀려 합친다 → 맞닿은 구간, 나란한 차도·보도가 면 하나가 된다.
- 등급 면끼리 겹치지 않게 통행 제외 > 고위험 > 위험 순으로 앞 면을 뺀다
  (겹친 곳의 색이 두 번 칠해지지 않게).
- 미터 좌표(EPSG:5186)에서 계산하고, 내보낼 때만 경위도로 바꾼다.
- 표시용으로만 묶는다. 경로 탐색은 그대로 구간별 위험도를 쓴다.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from functools import lru_cache

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from app.config import Settings, settings
from routing.cost import risk_grade
from routing.locate import to_lnglat, to_lnglat_arrays

GROUPS = ("excluded", "고위험", "위험")  # 앞 면이 우선 (뒤 면에서 앞 면을 뺀다)
MIN_AREA_M2 = 10.0    # 등급 면을 서로 빼고 남은 이보다 작은 조각은 버린다
MIN_HOLE_M2 = 200.0   # 나란한 길 사이에 생기는 이보다 작은 구멍은 메운다 (블록 크기 구멍은 남긴다)


@lru_cache(maxsize=1)
def segment_geoms() -> dict[int, LineString]:
    """segment_id → 미터 좌표 선. 위험도 캐시가 이미 읽어 둔 segments.gpkg 를 재사용한다."""
    from risk.cache import get_static

    gdf = get_static().gdf
    return {int(sid): geom for sid, geom in zip(gdf["segment_id"], gdf.geometry, strict=True)}


def area_candidates(risk: Mapping[int, float], excluded: Iterable[int], s: Settings = settings) -> set[int]:
    """면으로 그릴 구간: '위험' 등급 이상이거나 통행 제외"""
    return {sid for sid, r in risk.items() if r >= s.grade_danger} | set(excluded)


def segments_near(
    paths: Iterable[LineString], seg_ids: Iterable[int], geoms: Mapping[int, LineString], dist: float
) -> list[int]:
    """seg_ids 중 경로 선(paths) 에서 dist m 안에 걸치는 구간.

    면으로 합치기 전에 구간부터 고른다. 먼저 합치면 경로와 상관없는 먼 길까지 이어진
    큰 덩어리 하나가 통째로 딸려 온다 (강남역 일대 H=1 구간은 맞닿으면 거의 한 덩어리).
    """
    paths = [p for p in paths if p is not None and not p.is_empty]
    sids = sorted(sid for sid in set(seg_ids) if sid in geoms)
    if not paths or not sids:
        return []
    corridor = shapely.union_all(shapely.buffer(paths, dist))
    shapely.prepare(corridor)
    hit = shapely.intersects(corridor, [geoms[sid] for sid in sids])
    return [sid for sid, h in zip(sids, hit, strict=True) if h]


def build_areas(
    seg_ids: Iterable[int],
    risk: Mapping[int, float],
    excluded: Iterable[int],
    reasons: Mapping[int, list[str]],
    geoms: Mapping[int, LineString],
    s: Settings = settings,
) -> list[dict]:
    """seg_ids 중 면으로 그릴 구간을 등급별로 합친 GeoJSON Feature 목록 (Polygon 하나씩)"""
    excluded = set(excluded)
    groups: dict[str, list[int]] = {g: [] for g in GROUPS}
    for sid in sorted(set(seg_ids)):
        if sid not in geoms:
            continue
        if sid in excluded:
            groups["excluded"].append(sid)
            continue
        grade = risk_grade(risk.get(sid, 0.0), s)
        if grade in groups:
            groups[grade].append(sid)

    features: list[dict] = []
    taken = None  # 앞 등급 면들의 합 (뒤 등급 면에서 뺀다)
    for group in GROUPS:
        sids = groups[group]
        if not sids:
            continue
        lines = [geoms[sid] for sid in sids]
        union = shapely.union_all(shapely.buffer(lines, s.area_buffer_m, quad_segs=4))
        union = _fill_small_holes(union).simplify(s.area_simplify_m, preserve_topology=True)
        shape = union if taken is None else union.difference(taken)
        taken = union if taken is None else shapely.union_all([taken, union])

        tree = shapely.STRtree(lines)
        for poly in _polygons(shape):
            if poly.area < MIN_AREA_M2:
                continue
            members = [sids[i] for i in sorted(tree.query(poly, predicate="intersects"))]
            if members:
                features.append(_feature(len(features) + 1, poly, group, members, risk, reasons, geoms, s))
    return features


def merge_reasons(tag_lists: Iterable[list[str]]) -> list[str]:
    """구간들의 이유 태그 합집합. 많은 구간에 붙은 태그부터 (같으면 먼저 나온 순)"""
    counts = Counter()
    for tags in tag_lists:
        counts.update(dict.fromkeys(tags, 1))
    return [tag for tag, _ in counts.most_common()]


def _feature(area_id, poly: Polygon, group, members, risk, reasons, geoms, s) -> dict:
    center = poly.point_on_surface()
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": _rings(orient(poly, sign=1.0))},
        "properties": {
            "area_id": area_id,
            "grade": risk_grade(max(risk.get(sid, 0.0) for sid in members), s),
            "excluded": group == "excluded",
            "length_m": round(sum(geoms[sid].length for sid in members)),
            "reasons": merge_reasons(reasons.get(sid, []) for sid in members),
            "center": to_lnglat(center.x, center.y),   # 통행 제외 버튼 위치 (면 안쪽 점)
        },
    }


def _polygons(shape) -> list[Polygon]:
    return [g for g in shapely.get_parts(shape) if isinstance(g, Polygon) and not g.is_empty]


def _fill_small_holes(shape):
    out = []
    for poly in _polygons(shape):
        holes = [r for r in poly.interiors if Polygon(r).area >= MIN_HOLE_M2]
        out.append(Polygon(poly.exterior, holes))
    return shapely.union_all(out) if out else shape


def _rings(poly: Polygon) -> list[list[list[float]]]:
    """Polygon → GeoJSON 좌표 [바깥 고리, 구멍...] (경위도, 소수 6자리)"""
    return [_ring(poly.exterior.coords), *(_ring(r.coords) for r in poly.interiors)]


def _ring(coords) -> list[list[float]]:
    xy = np.asarray(coords)
    lng, lat = to_lnglat_arrays(xy[:, 0], xy[:, 1])
    return np.round(np.column_stack([lng, lat]), 6).tolist()
