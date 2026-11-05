"""엣지를 25m 이하 구간으로 같은 길이 분할 (기획안 4.1).

길이 L 인 엣지를 n = ceil(L / 25) 개로 나눈다. 예) 60m → 3개 × 20m
- 위험도를 25m 단위로 매기면 긴 도로 중 침수된 일부만 피할 수 있다.
- 중간 지점마다 새 노드(정수 ID, 기존 최대 ID + 1 부터)를 만든다.
"""

import math

from shapely.geometry import LineString, Point
from shapely.ops import substring


def n_pieces(length_m: float, max_m: float = 25.0) -> int:
    return max(1, math.ceil(round(length_m, 6) / max_m))


def orient(line: LineString, start_xy: tuple[float, float]) -> LineString:
    """선의 시작점이 start_xy 쪽이 되도록 필요하면 뒤집는다 (OSMnx 무방향 그래프는 방향이 섞임)."""
    p0, p1 = Point(line.coords[0]), Point(line.coords[-1])
    s = Point(start_xy)
    return line if p0.distance(s) <= p1.distance(s) else LineString(line.coords[::-1])


def split_edges(edges: list[dict], nodes: dict[int, tuple[float, float]], max_m: float = 25.0):
    """edges: u, v, geometry(LineString) 와 나머지 속성을 가진 dict 목록
    nodes: node_id → (x, y)

    → (segments, nodes)
      segments: segment_id, u, v, length, geometry + 원래 엣지 속성
      nodes:    node_id → (x, y) (중간 노드 추가)
    """
    nodes = dict(nodes)
    next_node = max(nodes, default=0) + 1
    next_seg = 1
    segments = []

    for e in edges:
        u, v = int(e["u"]), int(e["v"])
        line = orient(e["geometry"], nodes[u])
        total = line.length
        n = n_pieces(total, max_m)
        piece = total / n
        extra = {k: val for k, val in e.items() if k not in ("u", "v", "geometry", "length")}

        prev = u
        for i in range(n):
            part = substring(line, i * piece, (i + 1) * piece)
            if i == n - 1:
                end = v
            else:
                end = next_node
                next_node += 1
                x, y = part.coords[-1]
                nodes[end] = (x, y)
            segments.append(
                {"segment_id": next_seg, "u": prev, "v": end, "length": piece, "geometry": part, **extra}
            )
            next_seg += 1
            prev = end

    return segments, nodes
