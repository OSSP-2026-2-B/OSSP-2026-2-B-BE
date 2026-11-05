"""통행 제외 규칙 (기획안 3.4). 위험도와 달리 비용이 아니라 "아예 못 지나가는" 구간을 정한다.

규칙 1: 국토부 재난상황정보의 전면통제·땅꺼짐 지점 반경 30m 안의 보행 구간
규칙 2: 서울시 침수경보 기준 (1시간 72mm 이상, 또는 1시간 50mm 이상이면서 3시간 90mm 이상) 을
        충족하면, 침수 요인(H·L·W) 이 있는 지하 구간(U=1)
규칙 3: 통제수위 하천 인접 (확장, 아직 미구현)
"""

from collections.abc import Iterable, Mapping

from app.config import Settings, settings


def flood_warning_met(rain_1h_mm: float, rain_3h_mm: float | None, s: Settings = settings) -> bool:
    """서울시 침수경보 상황판단 기준 충족 여부"""
    if rain_1h_mm >= s.flood_warning_1h_mm:
        return True
    return (
        rain_3h_mm is not None
        and rain_1h_mm >= s.flood_warning_1h_alt_mm
        and rain_3h_mm >= s.flood_warning_3h_mm
    )


def rule2_excluded(
    segments: Mapping[int, Mapping[str, float]],
    rain_1h_mm: float,
    rain_3h_mm: float | None,
    s: Settings = settings,
) -> set[int]:
    """규칙 2: 경보 기준 충족 시 H·L·W 중 하나라도 0 보다 큰 지하 구간"""
    if not flood_warning_met(rain_1h_mm, rain_3h_mm, s):
        return set()
    out = set()
    for seg_id, f in segments.items():
        if not f.get("U"):
            continue
        if any((f.get(k) or 0) > 0 for k in ("H", "L", "W")):
            out.add(int(seg_id))
    return out


def rule1_excluded(
    segments_gdf, control_points: Iterable[tuple[float, float]], s: Settings = settings
) -> set[int]:
    """규칙 1: 통제 지점(EPSG:5186 x, y) 반경 안에 걸치는 구간.
    segments_gdf: segment_id 와 LineString geometry 를 가진 GeoDataFrame (EPSG:5186)"""
    import geopandas as gpd
    from shapely.geometry import Point

    pts = [Point(x, y) for x, y in control_points]
    if not pts or segments_gdf is None or len(segments_gdf) == 0:
        return set()
    zones = gpd.GeoDataFrame(geometry=[p.buffer(s.its_control_radius_m) for p in pts], crs=segments_gdf.crs)
    hit = gpd.sjoin(segments_gdf[["segment_id", "geometry"]], zones, predicate="intersects", how="inner")
    return {int(v) for v in hit["segment_id"].unique()}
