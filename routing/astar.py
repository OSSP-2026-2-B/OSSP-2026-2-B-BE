"""위험 회피 경로 탐색 (기획안 3.4, 4.4).

NetworkX A* 로 Cost(e,t) = WalkTime(e) × (1 + α·Risk²) 의 합이 가장 작은 경로를 찾는다.
- 추정 위험은 비용으로만 반영하고,
- 통행 제외 구간(규칙 1·2)은 아예 지나가지 못하게 한다.
"""

import math
from dataclasses import dataclass, field

import networkx as nx

from routing.cost import segment_cost

DANGER = 0.6  # '위험' 등급 이상 기준 (위험 구간 수 세기)


@dataclass
class RouteResult:
    nodes: list[int]                       # 지나는 교차점 순서
    segment_ids: list[int]                 # 지나는 길 조각 순서
    distance_m: float                      # 실제 거리
    cost: float                            # 비용 합 (위험 페널티 포함)
    risk_segment_count: int                # '위험' 등급 이상 구간 수
    max_risk: float                        # 경로에서 가장 높은 위험도
    risky_segment_ids: list[int] = field(default_factory=list)


def find_route(
    G: nx.MultiGraph,
    start: int,
    end: int,
    alpha: float,
    risk: dict[int, float] | None = None,       # segment_id → 위험도 (없으면 0)
    excluded: set[int] | None = None,           # 통행 제외 segment_id
    speed_mps: float = 1.2,
) -> RouteResult | None:
    """경로가 없으면 None (→ API 에서 임시주거시설 안내)."""
    risk = risk or {}
    excluded = excluded or set()

    def edge_cost(data: dict) -> float | None:
        if data["segment_id"] in excluded:
            return None                          # None 이면 NetworkX 가 이 길을 건너뜀
        r = risk.get(data["segment_id"], 0.0)
        return segment_cost(data["length"], r, alpha, speed_mps)

    def weight(u, v, keyed: dict) -> float | None:
        # MultiGraph 는 두 교차점 사이의 길들이 {key: 속성} 으로 들어온다 → 가장 싼 길 선택
        costs = [c for c in (edge_cost(d) for d in keyed.values()) if c is not None]
        return min(costs) if costs else None

    def heuristic(a, b) -> float:
        # 목적지까지 직선거리 ÷ 속도. 실제 비용보다 항상 작거나 같아서 최단 경로가 보장된다
        xa, ya = G.nodes[a]["x"], G.nodes[a]["y"]
        xb, yb = G.nodes[b]["x"], G.nodes[b]["y"]
        return math.hypot(xb - xa, yb - ya) / speed_mps

    try:
        path = nx.astar_path(G, start, end, heuristic=heuristic, weight=weight)
    except nx.NetworkXNoPath:
        return None

    # 경로의 교차점 사이마다 실제로 고른 길 조각을 다시 찾아 결과 정리
    seg_ids, dist, total = [], 0.0, 0.0
    for a, b in zip(path, path[1:], strict=False):
        best = min(
            (d for d in G[a][b].values() if edge_cost(d) is not None),
            key=edge_cost,
        )
        seg_ids.append(best["segment_id"])
        dist += best["length"]
        total += edge_cost(best)

    risks = [risk.get(s, 0.0) for s in seg_ids]
    risky = [s for s, r in zip(seg_ids, risks, strict=True) if r >= DANGER]
    return RouteResult(
        nodes=path,
        segment_ids=seg_ids,
        distance_m=dist,
        cost=total,
        risk_segment_count=len(risky),
        max_risk=max(risks, default=0.0),
        risky_segment_ids=risky,
    )