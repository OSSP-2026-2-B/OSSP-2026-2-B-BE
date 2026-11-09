"""OSM 보행망 내려받기 (기획안 4.1). 인터넷 연결이 필요하다 (Overpass API).

강남역 중심 반경 R m 안의 보행 가능한 길(network_type="walk")을 받아
EPSG:5186 으로 바꾸고, 방향 없는 그래프로 만든 뒤 엣지·노드 표로 돌려준다.
U 판별에 필요한 layer·tunnel·indoor 태그도 함께 받는다.
"""

from pathlib import Path

import osmnx as ox

TARGET_CRS = "EPSG:5186"
EXTRA_WAY_TAGS = ["layer", "tunnel", "indoor", "covered", "highway", "name"]


def fetch_walk_network(lat: float, lon: float, radius_m: float, save_graphml: str | None = None):
    """→ (edges: list[dict], nodes: dict[node_id, (x, y)]).
    save_graphml 을 주면 받은 보행망을 저장해 다음부터 --graphml 로 다시 쓸 수 있다
    (OSM 은 계속 바뀌므로 팀원끼리 같은 결과를 만들려면 저장본을 공유)."""
    ox.settings.useful_tags_way = list(dict.fromkeys(list(ox.settings.useful_tags_way) + EXTRA_WAY_TAGS))
    G = ox.graph_from_point((lat, lon), dist=radius_m, network_type="walk", simplify=False)
    # 단순화(교차점 사이 점 합치기) 때 지상 길과 지하 길이 한 엣지로 합쳐지지 않게 한다
    G = ox.simplify_graph(G, edge_attrs_differ=["layer", "tunnel", "indoor"])
    G = ox.project_graph(G, to_crs=TARGET_CRS)
    if save_graphml:
        Path(save_graphml).parent.mkdir(parents=True, exist_ok=True)
        ox.save_graphml(G, save_graphml)
    return graph_to_records(ox.convert.to_undirected(G))


def load_walk_network(path: str):
    """미리 저장한 .graphml 파일에서 읽기 (오프라인·재현용)"""
    G = ox.load_graphml(path)
    if str(G.graph.get("crs", "")).upper() != TARGET_CRS:
        G = ox.project_graph(G, to_crs=TARGET_CRS)
    G = ox.convert.to_undirected(G)
    return graph_to_records(G)


def graph_to_records(G):
    nodes_gdf, edges_gdf = ox.graph_to_gdfs(G)
    nodes = {int(i): (float(r.x), float(r.y)) for i, r in nodes_gdf.iterrows()}
    edges = []
    for (u, v, _k), r in edges_gdf.iterrows():
        edges.append(
            {
                "u": int(u),
                "v": int(v),
                "geometry": r.geometry,
                **{t: r.get(t) for t in ("layer", "tunnel", "indoor", "highway", "name")},
            }
        )
    return edges, nodes
