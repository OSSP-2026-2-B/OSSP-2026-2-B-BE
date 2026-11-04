"""보행 그래프 만들기 (기획안 4.1, docs 데이터 형식).

segments(25m 구간) + nodes(교차점) 표 → NetworkX 그래프
- 노드: node_id, 좌표 x·y (EPSG:5186, 미터)
- 엣지: segment_id, length, U(지하 구간)
보행은 일방통행이 없으므로 방향 없는 그래프(MultiGraph)로 만든다.
같은 두 교차점 사이에 길이 여러 개일 수 있어 Multi 그래프를 쓴다.
"""

from collections.abc import Iterable, Mapping

import networkx as nx


def build_graph(segments: Iterable[Mapping], nodes: Iterable[Mapping]) -> nx.MultiGraph:
    """segments: segment_id, u, v, length, U 를 가진 행들
    nodes: node_id, x, y 를 가진 행들"""
    G = nx.MultiGraph()
    for n in nodes:
        G.add_node(int(n["node_id"]), x=float(n["x"]), y=float(n["y"]))
    for s in segments:
        G.add_edge(
            int(s["u"]), int(s["v"]),
            key=int(s["segment_id"]),          # 구간 번호를 key 로 써서 겹치지 않게
            segment_id=int(s["segment_id"]),
            length=float(s["length"]),
            U=int(s.get("U", 0)),
        )
    return G


def load_graph_from_gpkg(path: str = "data/segments.gpkg") -> nx.MultiGraph:
    """전처리 결과 파일을 읽어 그래프로 만든다 (파일이 생기면 사용)."""
    import geopandas as gpd

    seg = gpd.read_file(path, layer="segments")
    nod = gpd.read_file(path, layer="nodes")
    nod = nod.assign(x=nod.geometry.x, y=nod.geometry.y)
    return build_graph(seg.to_dict("records"), nod.to_dict("records"))