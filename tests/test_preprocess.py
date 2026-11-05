"""전처리: 인터넷·원본 SHP 없이 가상 지도로 확인 (좌표 단위 m, EPSG:5186)

  1 ────── 60m ────── 2 ── 20m ── 3
  (y=0)                            지하보도(layer=-1)
"""

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon

from preprocess.build_segments import build, write_gpkg
from preprocess.flood_history import assign_h, event_years, normalize_traces, year_of
from preprocess.split import n_pieces, split_edges
from preprocess.underground import classify_underground
from routing.astar import find_route
from routing.graph import load_graph_from_gpkg

NODES = {1: (0.0, 0.0), 2: (60.0, 0.0), 3: (80.0, 0.0)}


def edges():
    return [
        {"u": 1, "v": 2, "geometry": LineString([(0, 0), (60, 0)]), "highway": "footway"},
        # 방향이 반대로 저장된 엣지 (3 → 2): 분할 시 u 쪽에서 시작하도록 뒤집혀야 함
        {"u": 2, "v": 3, "geometry": LineString([(80, 0), (60, 0)]), "highway": "footway", "layer": "-1"},
    ]


def box(x0, y0, x1, y1):
    return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def test_n_pieces():
    assert n_pieces(25) == 1
    assert n_pieces(25.0000001) == 1  # 부동소수 오차는 무시
    assert n_pieces(26) == 2
    assert n_pieces(60) == 3
    assert n_pieces(0.5) == 1


def test_split_equal_lengths_and_new_nodes():
    segs, nodes = split_edges(edges(), NODES)
    assert len(segs) == 3 + 1
    assert [s["length"] for s in segs[:3]] == pytest.approx([20, 20, 20])
    # 1 → 새 노드 → 새 노드 → 2 로 이어짐
    assert segs[0]["u"] == 1 and segs[2]["v"] == 2
    assert segs[0]["v"] == segs[1]["u"] and segs[0]["v"] > 3
    assert nodes[segs[0]["v"]] == pytest.approx((20, 0))
    # 뒤집힌 엣지: 2(60,0) 에서 시작
    assert segs[3]["u"] == 2 and segs[3]["geometry"].coords[0] == (60, 0)
    assert len({s["segment_id"] for s in segs}) == 4


@pytest.mark.parametrize(
    "tags, expected",
    [
        ({}, (0, "")),
        ({"layer": "0"}, (0, "")),
        ({"layer": "-1", "highway": "footway"}, (1, "지하보도")),
        ({"tunnel": "yes", "highway": "steps"}, (1, "지하보도")),
        ({"tunnel": "yes", "highway": "primary"}, (1, "지하차도")),
        ({"indoor": "yes", "highway": "corridor"}, (1, "실내 통로")),
        ({"indoor": "yes", "layer": "-2"}, (1, "지하상가 통로")),
        ({"layer": ["0", "-1"], "highway": ["footway", "steps"]}, (1, "지하보도")),  # 합쳐진 길
        ({"layer": "['0', '-1']", "highway": "footway"}, (1, "지하보도")),         # graphml 저장본
        ({"tunnel": "building_passage"}, (0, "")),
    ],
)
def test_underground(tags, expected):
    assert classify_underground(**tags) == expected


def test_years():
    from pathlib import Path

    import pandas as pd

    assert year_of(Path("data/raw/flood/2022/2022 서울시 침수흔적도 업뎃.shp")) == 2022
    df = pd.DataFrame({
        "F_SAT_YMD": ["20200728", None, "abc"],
        "F_YR": ["2018", "2018", None],
    })
    # 침수 시작일 → F_YR → 파일 연도 순
    assert event_years(df, 2018) == [2020, 2018, 2018]
    assert event_years(pd.DataFrame({"피해일시": ["20250822 - 1031"]}), 2025) == [2025]
    assert event_years(pd.DataFrame({"F_YR": [2013.0]}), 2013) == [2013]
    assert event_years(pd.DataFrame({"other": [1]}), 2019) == [2019]


def test_assign_h_overlap_rule():
    seg = gpd.GeoDataFrame(
        {"segment_id": [1, 2, 3]},
        geometry=[LineString([(0, 0), (25, 0)]), LineString([(25, 0), (50, 0)]),
                  LineString([(200, 0), (225, 0)])],
        crs="EPSG:5186",
    )
    raw = {
        # 구간 1 과 겹침 → 0.3/0.5 = 0.6 / 구간 2 와는 15m → 0.6 × 0.5 = 0.3
        2016: gpd.GeoDataFrame({"F_SHIM": [0.3]}, geometry=[box(5, -5, 10, 5)], crs="EPSG:5186"),
        # 구간 1 과 겹침 → 1.2/0.5 = 2.4 → 최대 1 / 구간 2 와는 22m → 0.5
        2022: gpd.GeoDataFrame({"F_SHIM": [1.2], "F_SAT_YMD": ["20220808"]}, geometry=[box(0, -5, 3, 5)],
                               crs="EPSG:5186"),
        # 2018년 파일에 섞인 2020년 기록: 구간 2 에서 20m → 0.8 × 0.5 = 0.4, 구간 1 에서 30.5m (밖)
        2018: gpd.GeoDataFrame({"F_SHIM": [0.4], "F_SAT_YMD": ["20200728"]}, geometry=[box(48, 20, 53, 25)],
                               crs="EPSG:5186"),
        # 2025년 형식 (피해일시): 구간 3 과 겹침 → 0.6
        2025: gpd.GeoDataFrame({"F_SHIM": [0.3], "피해일시": ["20250822 - 1031"]},
                               geometry=[box(210, -5, 215, 5)], crs="EPSG:5186"),
    }
    traces = gpd.GeoDataFrame(
        gpd.pd.concat([normalize_traces(g, y) for y, g in raw.items()], ignore_index=True), crs="EPSG:5186"
    )
    h = assign_h(seg, traces).set_index("segment_id")

    assert h.loc[1, "H"] == pytest.approx(1.0)
    assert h.loc[1, "H_years"] == "2016,2022" and h.loc[1, "H_count"] == 2
    assert h.loc[2, "H"] == pytest.approx(0.5)        # 겹치는 흔적 없음 → 반경 값 중 최대
    assert h.loc[2, "H_years"] == "2016,2020,2022" and h.loc[2, "H_count"] == 3
    assert h.loc[3, "H"] == pytest.approx(0.6)
    assert h.loc[3, "H_years"] == "2025"


def test_assign_h_no_traces():
    seg = gpd.GeoDataFrame({"segment_id": [1]}, geometry=[LineString([(0, 0), (25, 0)])], crs="EPSG:5186")
    h = assign_h(seg, None)
    assert h["H"].tolist() == [0.0] and h["H_years"].tolist() == [""]


def test_build_and_load_gpkg(tmp_path):
    raw = gpd.GeoDataFrame({"F_SHIM": [0.25]}, geometry=[box(10, -3, 12, 3)], crs="EPSG:5186")
    traces = normalize_traces(raw, 2022)
    seg_gdf, node_gdf = build(edges(), NODES, traces)
    assert list(seg_gdf.columns) == ["segment_id", "u", "v", "length", "U_flag", "U_reason", "H", "H_count",
                                     "H_years", "geometry"]
    assert seg_gdf["U_flag"].tolist() == [0, 0, 0, 1]
    assert seg_gdf["U_reason"].tolist()[-1] == "지하보도"
    # 구간 1(0~20m) 은 흔적과 겹침 → 0.5, 구간 2·3 은 30m 안 → 0.25, 구간 4(60~80m) 는 48m → 0
    assert seg_gdf["H"].tolist() == pytest.approx([0.5, 0.25, 0.25, 0])

    out = tmp_path / "segments.gpkg"
    write_gpkg(seg_gdf, node_gdf, out)

    # 라우팅 모듈이 그대로 읽어 경로를 찾을 수 있어야 함
    G = load_graph_from_gpkg(str(out))
    r = find_route(G, 1, 3, alpha=0)
    assert r is not None and r.distance_m == pytest.approx(80)
    assert len(r.segment_ids) == 4
    assert [G.edges[e]["U"] for e in G.edges(keys=True)].count(1) == 1  # U_flag → 엣지 속성 U
