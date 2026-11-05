"""전처리 전체 실행: OSM 보행망 → 25m 분할 → U 판별 → 침수흔적도 공간조인 H → data/segments.gpkg

사용법 (레포 최상위 폴더에서)
    python -m preprocess.build_segments                      # OSM 다운로드 + data/raw/flood 의 SHP 사용
    python -m preprocess.build_segments --graphml data/raw/walk.graphml   # 저장해 둔 보행망 사용
    python -m preprocess.build_segments --inspect            # 침수흔적도 SHP 열 이름·값만 확인

결과 파일 data/segments.gpkg (EPSG:5186)
- segments 레이어: segment_id, u, v, length, U_flag, U_reason, H, H_count, H_years, geometry(LineString)
- nodes 레이어:    node_id, geometry(Point)
"""

import argparse
import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from app.config import settings
from preprocess.flood_history import TARGET_CRS, assign_h, load_traces, read_shp, year_of
from preprocess.split import split_edges
from preprocess.underground import classify_underground

logger = logging.getLogger(__name__)

# GeoPackage(SQLite) 는 열 이름의 대소문자를 구분하지 않아 'u' 와 'U' 를 함께 둘 수 없다
# → 파일에서는 지하 여부를 U_flag 로 저장하고, routing.graph 가 읽을 때 엣지 속성 U 로 바꾼다.
SEGMENT_COLUMNS = [
    "segment_id", "u", "v", "length", "U_flag", "U_reason", "H", "H_count", "H_years", "geometry",
]


def build(edges: list[dict], nodes: dict, traces: gpd.GeoDataFrame | None, max_m: float = 25.0):
    """엣지·노드 → (segments GeoDataFrame, nodes GeoDataFrame). 네트워크 없이 테스트할 수 있게 분리."""
    for e in edges:
        e["U"], e["U_reason"] = classify_underground(
            e.get("layer"), e.get("tunnel"), e.get("indoor"), e.get("highway")
        )

    segs, all_nodes = split_edges(edges, nodes, max_m)
    seg_gdf = gpd.GeoDataFrame(segs, geometry="geometry", crs=TARGET_CRS)

    h = assign_h(seg_gdf, traces)
    seg_gdf = seg_gdf.drop(columns=[c for c in ("H", "H_count", "H_years") if c in seg_gdf]).merge(
        h, on="segment_id", how="left"
    )
    seg_gdf = seg_gdf.rename(columns={"U": "U_flag"})
    seg_gdf = gpd.GeoDataFrame(seg_gdf[SEGMENT_COLUMNS], geometry="geometry", crs=TARGET_CRS)
    seg_gdf["U_flag"] = seg_gdf["U_flag"].astype(int)

    node_gdf = gpd.GeoDataFrame(
        {"node_id": list(all_nodes.keys())},
        geometry=[Point(xy) for xy in all_nodes.values()],
        crs=TARGET_CRS,
    )
    return seg_gdf, node_gdf


def write_gpkg(seg_gdf: gpd.GeoDataFrame, node_gdf: gpd.GeoDataFrame, out: str | Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()  # 이전 결과의 레이어가 남지 않도록 새로 만든다
    seg_gdf.to_file(out, layer="segments", driver="GPKG")
    node_gdf.to_file(out, layer="nodes", driver="GPKG")


def summary(seg_gdf: gpd.GeoDataFrame) -> str:
    years = pd.Series([y for ys in seg_gdf["H_years"] for y in ys.split(",") if y])
    years = years.value_counts().sort_index()
    length = seg_gdf["length"]
    under = seg_gdf["U_flag"] == 1
    lines = [
        f"구간 수: {len(seg_gdf)}  (평균 {length.mean():.1f} m, 최대 {length.max():.1f} m)",
        f"U=1 지하 구간: {int(under.sum())}  ({length[under].sum():.0f} m)",
        f"H>0 침수이력 구간: {int((seg_gdf['H'] > 0).sum())}",
        "연도별 H>0 구간 수: " + (", ".join(f"{y}={n}" for y, n in years.items()) or "없음"),
    ]
    return "\n".join(lines)


def inspect(flood_dir: str) -> None:
    """SHP 마다 연도·좌표계·열 이름·침수심 통계를 출력 (열 이름 확인용)"""
    for shp in sorted(Path(flood_dir).rglob("*.shp")):
        gdf = read_shp(shp)
        print(f"\n== {shp}  (연도 {year_of(shp)}, {len(gdf)}건)")
        print("열:", [c for c in gdf.columns if c != "geometry"])
        print(gdf.drop(columns="geometry").head(3).to_string())


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="보행망 전처리 → data/segments.gpkg")
    p.add_argument("--flood-dir", default="data/raw/flood",
                   help="침수흔적도 SHP 폴더 (연도가 이름에 있어야 함)")
    p.add_argument("--graphml", default=None, help="저장해 둔 보행망 .graphml (없으면 OSM 에서 받음)")
    p.add_argument("--save-graphml", default="data/raw/walk.graphml", help="받은 보행망 저장 위치")
    p.add_argument("--radius", type=float, default=settings.pilot_radius_m, help="수집 반경 m")
    p.add_argument("--out", default="data/segments.gpkg")
    p.add_argument("--inspect", action="store_true", help="침수흔적도 열 이름만 확인하고 끝냄")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.inspect:
        inspect(args.flood_dir)
        return

    from preprocess import osm_network

    if args.graphml:
        logger.info("보행망 읽는 중: %s", args.graphml)
        edges, nodes = osm_network.load_walk_network(args.graphml)
    else:
        logger.info("OSM 보행망 다운로드 중 (반경 %.0f m)...", args.radius)
        edges, nodes = osm_network.fetch_walk_network(
            settings.pilot_center_lat, settings.pilot_center_lon, args.radius, args.save_graphml
        )
        logger.info("보행망 저장: %s", args.save_graphml)

    traces = None
    if Path(args.flood_dir).exists():
        logger.info("침수흔적도 읽는 중: %s", args.flood_dir)
        traces = load_traces(args.flood_dir)
        years = ", ".join(map(str, sorted(traces["year"].unique())))
        logger.info("사용할 흔적 %d건 (%s)", len(traces), years)
    else:
        logger.warning("침수흔적도 폴더가 없어 H=0 으로 만듭니다: %s", args.flood_dir)

    seg_gdf, node_gdf = build(edges, nodes, traces, settings.segment_max_m)
    write_gpkg(seg_gdf, node_gdf, args.out)
    print(summary(seg_gdf))
    print(f"저장 완료: {args.out}")


if __name__ == "__main__":
    main()
