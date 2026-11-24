"""POST /api/route 의 실제 계산"""

from dataclasses import dataclass, field
from functools import lru_cache

import networkx as nx
from shapely.geometry import LineString

from app.api.errors import ApiError
from app.config import Settings, settings
from risk.areas import area_candidates, build_areas, merge_reasons, segment_geoms, segments_near
from routing.astar import RouteResult, find_route, min_danger_length
from routing.cost import risk_grade, route_grade
from routing.fallback import find_shelter, get_shelters
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
    scenario: bool = False   # rain_mm 시나리오로 계산했으면 True (응답 rain.scenario)


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
    rain = {"rain_1h_mm": ri.rain_1h_mm, "updated_at": ri.updated_at, "scenario": ri.scenario}
    if found["shortest"] is None:
        fallback = find_shelter(G, index, src, start, ri.risk, ri.excluded, ri.rain_1h_mm, get_shelters(), s)
        if fallback is None:
            raise ApiError(404, "NO_ROUTE", "경로와 안내할 임시주거시설 모두 없습니다.")
        return {"rain": rain, "routes": [], "unavoidable_danger_m": None,
                "risk_areas": _areas([]), "fallback": fallback}

    base_min = duration_min(found["shortest"].distance_m, ri.rain_1h_mm, s)
    routes = [_route_json(m, found[m], G, start, end, base_min, speed, ri, s) for m in modes]
    # 출발·도착이 같으므로 모드와 관계없이 한 번만 계산 (경로가 있으면 이 값도 항상 있음)
    unavoidable = min_danger_length(G, src, dst, ri.risk, ri.excluded)
    areas = route_areas([found[m] for m in modes], G, ri, s)
    return {"rain": rain, "routes": routes, "unavoidable_danger_m": round(unavoidable),
            "risk_areas": areas, "fallback": None}


def route_areas(results: list[RouteResult], G, ri: RiskInput, s: Settings = settings, geoms=None) -> dict:
    """경로 후보들 주변(route_area_corridor_m) 의 '위험' 이상·통행 제외 구간을 면으로 묶은 FeatureCollection.
    경로를 보여 주는 동안 지도에는 이 면만 그린다 (경로와 상관없는 먼 위험 구역은 빼서 덜 촘촘하게)."""
    candidates = area_candidates(ri.risk, ri.excluded, s)
    if not candidates:
        return _areas([])
    geoms = segment_geoms() if geoms is None else geoms
    paths = [
        LineString([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in r.nodes])
        for r in results if len(r.nodes) >= 2
    ]
    near = segments_near(paths, candidates, geoms, s.route_area_corridor_m)
    return _areas(build_areas(near, ri.risk, ri.excluded, ri.reasons, geoms, s))


def _areas(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def danger_sections(r: RouteResult, node_xy, ri: RiskInput, s: Settings = settings) -> list[dict]:
    """경로 위에서 이어진 '위험' 이상 구간을 한 곳으로 묶는다 (비교 카드의 "위험 구간 N곳", 근거 시트 항목).

    25m 구간을 하나씩 세면 같은 도로가 수십 개로 나온다. 경로 순서대로 위험 구간을 이어 붙이고,
    사이에 낀 안전한 길이 section_gap_m 이하이면 같은 곳으로 본다. length_m 은 위험 구간 길이만 더하므로
    모든 곳의 합은 danger_length_m 과 같다.
    """
    groups: list[list[int]] = []   # 곳마다 경로 위 구간 순번(i) 목록
    gap = None                     # 마지막 위험 구간 뒤로 이어진 안전한 길이
    for i, (seg, length) in enumerate(zip(r.segment_ids, r.segment_lengths, strict=True)):
        if ri.risk.get(seg, 0.0) >= s.grade_danger:
            if gap is not None and gap <= s.section_gap_m:
                groups[-1].append(i)
            else:
                groups.append([i])
            gap = 0.0
        elif gap is not None:
            gap += length

    sections = []
    for idx in groups:
        segs = [r.segment_ids[i] for i in idx]
        mid = idx[len(idx) // 2]
        (xa, ya), (xb, yb) = node_xy[mid], node_xy[mid + 1]
        sections.append({
            "grade": risk_grade(max(ri.risk.get(seg, 0.0) for seg in segs), s),
            "length_m": round(sum(r.segment_lengths[i] for i in idx)),
            "segment_ids": segs,
            "location": to_lnglat((xa + xb) / 2, (ya + yb) / 2),
            "reasons": merge_reasons(ri.reasons.get(seg, []) for seg in segs),
        })
    return sections


def _route_json(mode, r: RouteResult, G, start, end, base_min, speed, ri: RiskInput, s) -> dict:
    minutes = duration_min(r.distance_m, ri.rain_1h_mm, s)
    node_xy = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in r.nodes]

    # 사용자가 찍은 지점 → 경로 → 도착 지점
    coords = [[start.lng, start.lat], *(to_lnglat(x, y) for x, y in node_xy), [end.lng, end.lat]]

    sections = danger_sections(r, node_xy, ri, s)
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
        "danger_length_m": round(r.danger_length_m),
        "caution_length_m": round(r.caution_length_m),
        "risk_cost": round(r.risk_cost, 1),
        "max_grade": risk_grade(r.max_risk, s),
        "route_grade": route_grade(r.danger_length_m, r.max_risk, r.caution_length_m, s),
        "geometry": {"type": "LineString", "coordinates": coords},
        "hazards": hazards,
        "danger_sections": sections,
        "danger_section_count": len(sections),
    }