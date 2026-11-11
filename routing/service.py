"""POST /api/route 의 실제 계산"""

from dataclasses import dataclass, field
from functools import lru_cache

import networkx as nx

from app.api.errors import ApiError
from app.config import Settings, settings
from routing.astar import RouteResult, find_route
from routing.cost import risk_grade
from routing.graph import load_graph_from_gpkg
from routing.locate import NodeIndex, in_pilot_area, to_lnglat, to_xy
from routing.walk import duration_min, walk_speed


@dataclass
class RiskInput:
    """위험도 캐시(risk.cache.get_snapshot)에서 가져온 값. 기본값은 빈 값(위험 0, 테스트용)"""

    risk: dict[int, float] = field(default_factory=dict)
    excluded: set[int] = field(default_factory=set)
    reasons: dict[int, list[str]] = field(default_factory=dict)
    rain_1h_mm: float = 0.0
    updated_at: str | None = None


@lru_cache(maxsize=1)
def get_graph() -> tuple[nx.MultiGraph, NodeIndex]:
    """서버에서 처음 한 번만 읽는다."""
    G = load_graph_from_gpkg()
    return G, NodeIndex(G)


def check_pilot_area(start, end, s: Settings = settings) -> None:
    for p in (start, end):
        if not in_pilot_area(p.lat, p.lng, s):
            raise ApiError(400, "OUT_OF_PILOT_AREA", "시범 지역(강남역 반경 1km) 밖입니다.")


def plan_routes(start, end, modes: list[str], ri: RiskInput, s: Settings = settings) -> dict:
    check_pilot_area(start, end, s)

    G, index = get_graph()
    src, _ = index.nearest(*to_xy(start.lat, start.lng))
    dst, _ = index.nearest(*to_xy(end.lat, end.lng))
    speed = walk_speed(ri.rain_1h_mm, s)

    # 우회 시간 계산을 위해 최단 경로는 항상 구한다
    found = {
        m: find_route(G, src, dst, s.alphas[m], ri.risk, ri.excluded, speed)
        for m in dict.fromkeys(["shortest", *modes])
    }
    if found["shortest"] is None:
        raise ApiError(404, "NO_ROUTE", "통행 제외 구간 때문에 목적지까지 갈 수 있는 경로가 없습니다.")

    base_min = duration_min(found["shortest"].distance_m, ri.rain_1h_mm, s)
    routes = [_route_json(m, found[m], G, start, end, base_min, speed, ri, s) for m in modes]
    return {
        "rain": {"rain_1h_mm": ri.rain_1h_mm, "updated_at": ri.updated_at},
        "routes": routes,
        "fallback": None,
    }


def _route_json(mode, r: RouteResult, G, start, end, base_min, speed, ri: RiskInput, s) -> dict:
    minutes = duration_min(r.distance_m, ri.rain_1h_mm, s)
    node_xy = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in r.nodes]

    # 사용자가 찍은 지점 → 경로 → 도착 지점
    coords = [[start.lng, start.lat], *(to_lnglat(x, y) for x, y in node_xy), [end.lng, end.lat]]

    hazards = []
    for i, seg in enumerate(r.segment_ids):
        if seg not in r.risky_segment_ids:
            continue
        (xa, ya), (xb, yb) = node_xy[i], node_xy[i + 1]
        risk = ri.risk.get(seg, 0.0)
        hazards.append({
            "segment_id": seg,
            "grade": risk_grade(risk, s),
            "risk": round(risk, 3),
            "location": to_lnglat((xa + xb) / 2, (ya + yb) / 2),
            "reasons": ri.reasons.get(seg, []),
        })

    return {
        "mode": mode,
        "distance_m": round(r.distance_m),
        "duration_min": minutes,
        "walk_speed_mps": speed,
        "detour_min": max(0, minutes - base_min),
        "risk_segment_count": r.risk_segment_count,
        "max_grade": risk_grade(r.max_risk, s),
        "geometry": {"type": "LineString", "coordinates": coords},
        "hazards": hazards,
    }