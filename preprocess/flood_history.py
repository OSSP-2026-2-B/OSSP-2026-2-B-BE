"""H(침수이력) 계산 — 서울시 침수흔적도 (2010~2025 연도별 SHP, 2015·2021 없음).

기록 종류별 규칙 (기획안 3.2)
- road   : 지목이 '도' 또는 '도로' 인 필지 흔적 → 구간과 겹치면 침수심 F_SHIM ÷ 0.5m
- nearby : 건물 피해 기록(2022 일부, 2023~2025)과 지목 구분이 없는 2010·2011 기록
           → 구간이 반경 30m 안이면 (침수심 ÷ 0.5m) × 0.5
- ignore : 그 밖의 지목(대지·공원 등) 필지 흔적은 쓰지 않음
각 값은 최대 1 로 자르고, 구간의 H = 모든 연도·기록 중 가장 큰 값.
H_count = 흔적이 있는 연도 수, H_years = "2010,2011,2022" 처럼 연도 목록.

※ 연도마다 SHP 열 이름이 다를 수 있다. `python -m preprocess.build_segments --inspect` 로 열을 확인하고
   아래 DEPTH_COLUMNS / JIMOK_COLUMNS 에 없으면 추가한다.
"""

import re
from pathlib import Path

import geopandas as gpd
import pandas as pd

from app.config import Settings, settings

TARGET_CRS = "EPSG:5186"
SOURCE_CRS = "EPSG:5179"  # 침수흔적도 원본 좌표계 (.prj 가 없을 때만 사용)

DEPTH_COLUMNS = ["F_SHIM", "f_shim", "SHIM", "침수심"]
JIMOK_COLUMNS = ["JIMOK", "jimok", "지목", "LNDCGR_NM", "JIMOK_NM"]
ROAD_JIMOK = {"도", "도로"}
NEARBY_YEARS = {2010, 2011, 2023, 2024, 2025}  # 지목 구분 없음 / 건물 피해 기록 연도

YEAR_RE = re.compile(r"(20[12]\d)")


def year_of(path: Path) -> int | None:
    """파일·폴더 이름에서 연도(2010~2029)를 찾는다. 예) data/raw/flood/2022/침수흔적_2022.shp"""
    m = YEAR_RE.findall(str(path))
    return int(m[-1]) if m else None


def _first_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def classify(year: int, jimok) -> str:
    """기록 하나 → 'road' / 'nearby' / 'ignore'"""
    j = "" if jimok is None or (isinstance(jimok, float) and jimok != jimok) else str(jimok).strip()
    if j in ROAD_JIMOK:
        return "road"
    if year in NEARBY_YEARS or j == "":
        return "nearby"
    return "ignore"


def read_shp(path: Path) -> gpd.GeoDataFrame:
    """한글 SHP 는 .cpg 가 없으면 대개 CP949 인코딩이다."""
    if path.with_suffix(".cpg").exists():
        gdf = gpd.read_file(path)
    else:
        gdf = gpd.read_file(path, encoding="cp949")
    if gdf.crs is None:
        gdf = gdf.set_crs(SOURCE_CRS)
    return gdf.to_crs(TARGET_CRS)


def normalize_traces(gdf: gpd.GeoDataFrame, year: int) -> gpd.GeoDataFrame:
    """원본 SHP → year, depth_m, kind, geometry 표준 형태"""
    dcol = _first_col(gdf, DEPTH_COLUMNS)
    jcol = _first_col(gdf, JIMOK_COLUMNS)
    if dcol is None:
        raise ValueError(f"{year}년 SHP 에서 침수심 열을 찾지 못했습니다. 열 목록: {list(gdf.columns)}")
    depth = pd.to_numeric(gdf[dcol], errors="coerce")
    jimok = gdf[jcol] if jcol else pd.Series([None] * len(gdf), index=gdf.index)
    out = gpd.GeoDataFrame(
        {
            "year": year,
            "depth_m": depth,
            "kind": [classify(year, j) for j in jimok],
        },
        geometry=gdf.geometry.make_valid(),
        crs=gdf.crs,
    )
    return out[(out["kind"] != "ignore") & out.geometry.notna() & ~out.geometry.is_empty]


def load_traces(flood_dir: str | Path) -> gpd.GeoDataFrame:
    """폴더 안의 모든 연도 SHP 를 읽어 하나로 합친다."""
    parts = []
    for shp in sorted(Path(flood_dir).rglob("*.shp")):
        year = year_of(shp)
        if year is None:
            raise ValueError(f"파일 이름에서 연도를 찾지 못했습니다: {shp}")
        parts.append(normalize_traces(read_shp(shp), year))
    if not parts:
        return gpd.GeoDataFrame(columns=["year", "depth_m", "kind", "geometry"], geometry="geometry",
                                crs=TARGET_CRS)
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=TARGET_CRS)


def trace_value(depth_m: float, s: Settings = settings) -> float:
    """침수심 → 0~1. 침수심이 비어 있으면 흔적이 있다는 사실만으로 1 로 본다 (보수적)."""
    if depth_m is None or depth_m != depth_m:
        return 1.0
    return min(1.0, max(0.0, float(depth_m) / s.flood_full_depth_m))


def assign_h(segments: gpd.GeoDataFrame, traces: gpd.GeoDataFrame, s: Settings = settings) -> pd.DataFrame:
    """segments(segment_id, geometry) × traces → segment_id 별 H, H_count, H_years"""
    empty = pd.DataFrame({"segment_id": segments["segment_id"], "H": 0.0, "H_count": 0, "H_years": ""})
    if traces is None or len(traces) == 0:
        return empty

    traces = traces.to_crs(segments.crs).copy()
    traces["value"] = [trace_value(d, s) for d in traces["depth_m"]]

    # 반경 기록은 30m 버퍼로 넓히고 값 × 0.5
    near = traces["kind"] == "nearby"
    traces.loc[near, "value"] = traces.loc[near, "value"] * s.flood_nearby_weight
    traces.loc[near, "geometry"] = traces.loc[near, "geometry"].buffer(s.flood_nearby_radius_m)

    hits = gpd.sjoin(
        segments[["segment_id", "geometry"]],
        traces[["year", "value", "geometry"]],
        predicate="intersects",
        how="inner",
    )
    hits = hits[hits["value"] > 0]
    if hits.empty:
        return empty

    per_seg = hits.groupby("segment_id").agg(
        H=("value", "max"),
        years=("year", lambda ys: sorted(set(int(y) for y in ys))),
    )
    per_seg["H_count"] = per_seg["years"].map(len)
    per_seg["H_years"] = per_seg["years"].map(lambda ys: ",".join(str(y) for y in ys))
    per_seg["H"] = per_seg["H"].clip(upper=1.0)

    out = empty.drop(columns=["H", "H_count", "H_years"]).merge(
        per_seg[["H", "H_count", "H_years"]], left_on="segment_id", right_index=True, how="left"
    )
    out["H"] = out["H"].fillna(0.0)
    out["H_count"] = out["H_count"].fillna(0).astype(int)
    out["H_years"] = out["H_years"].fillna("")
    return out
