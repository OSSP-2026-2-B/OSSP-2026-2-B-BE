"""경로가 없을 때 대신 안내할 이재민 임시주거시설 """

import logging
import math
from functools import lru_cache

import networkx as nx

from app.config import Settings, settings
from preprocess.shelters import Shelter, load_shelters
from routing.astar import find_route
from routing.locate import NodeIndex, to_lnglat, to_xy
from routing.walk import duration_min, walk_speed

logger = logging.getLogger(__name__)

REASON = "통행 제외 구간 때문에 목적지까지 갈 수 있는 경로가 없습니다."


@lru_cache(maxsize=1)
def get_shelters() -> list[Shelter]:
    """서버에서 처음 한 번만 읽는다. 파일이 없으면 빈 목록."""
    try:
        return load_shelters()
    except OSError as e:
        logger.warning("임시주거시설 자료를 읽지 못함: %s", e)
        return []


def find_shelter(
    G: nx.MultiGraph,
    index: NodeIndex,
    src: int,
    start,
    risk: dict[int, float],
    excluded: set[int],
    rain_1h_mm: float,
    shelters: list[Shelter],
    s: Settings = settings,
) -> dict | None:
    """갈 수 있는 가장 가까운 시설. 모두 막혔으면 직선거리로 가장 가까운 시설. 시설이 없으면 None."""
    if not shelters:
        return None
    speed = walk_speed(rain_1h_mm, s)

    best = None  # (경로 길이, 시설, 경로)
    for sh in shelters:
        node, snap_m = index.nearest(*to_xy(sh.lat, sh.lng))
        if snap_m > s.shelter_snap_max_m:
            continue  # 보행 그래프 밖 시설
        r = find_route(G, src, node, s.alpha_balanced, risk, excluded, speed)
        if r is not None and (best is None or r.distance_m < best[0]):
            best = (r.distance_m, sh, r)

    if best:
        _, sh, r = best
        coords = [
            [start.lng, start.lat],
            *(to_lnglat(G.nodes[n]["x"], G.nodes[n]["y"]) for n in r.nodes),
            [sh.lng, sh.lat],
        ]
        return _shelter_json(
            sh, True, round(r.distance_m), duration_min(r.distance_m, rain_1h_mm, s),
            {"type": "LineString", "coordinates": coords},
        )

    here = to_xy(start.lat, start.lng)
    sh = min(shelters, key=lambda x: math.dist(here, to_xy(x.lat, x.lng)))
    return _shelter_json(sh, False, round(math.dist(here, to_xy(sh.lat, sh.lng))), None, None)


def _shelter_json(sh: Shelter, reachable: bool, distance_m: int, minutes, geometry) -> dict:
    return {
        "type": "shelter",
        "shelter_id": sh.shelter_id,
        "name": sh.name,
        "address": sh.address,
        "capacity": sh.capacity,
        "location": [sh.lng, sh.lat],
        "reachable": reachable,
        "distance_m": distance_m,
        "duration_min": minutes,
        "geometry": geometry,
        "reason": REASON,
    }