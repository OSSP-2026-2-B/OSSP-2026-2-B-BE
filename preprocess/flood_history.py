"""H(침수이력) 계산 — 서울시 침수흔적도 (2010~2025 연도별 SHP, 2015·2021 없음).

규칙 ('겹침 기준', 2026-11-06 팀 논의에서 확정. 기획서 원안은 '지목 기준')
- 침수흔적도에 지목 열이 없어 도로 필지를 구분할 수 없다.
  → 흔적 폴리곤이 보행 구간과 직접 겹치면  min(1, 침수심 ÷ 0.5m)
  → 겹치지 않고 반경 30m 안에만 있으면      min(1, 침수심 ÷ 0.5m) × 0.5
- 구간의 H = 모든 흔적 중 가장 큰 값.
- H_count = 흔적이 있는 '침수 연도' 수, H_years = "2010,2011,2022" 처럼 연도 목록.
  침수 연도는 파일 연도가 아니라 침수 시작일(F_SAT_YMD 등)의 연도를 쓴다.
  (예: 2018년 파일에 2020년 호우 기록, 2024년 파일에 2023년 기록이 섞여 있음)

※ 열 이름은 `python -m preprocess.build_segments --inspect` 로 확인한다.
"""

import logging
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd

from app.config import Settings, settings

logger = logging.getLogger(__name__)

TARGET_CRS = "EPSG:5186"
SOURCE_CRS = "EPSG:5179"  # 침수흔적도 원본 좌표계 (.prj 가 없을 때만 사용)

DEPTH_COLUMNS = ["F_SHIM", "f_shim", "SHIM", "침수심"]
# 침수 연도를 찾을 열 (앞에서부터). 날짜 열은 앞 4자리를 연도로 쓴다.
DATE_COLUMNS = ["F_SAT_YMD", "피해일시"]
YEAR_COLUMNS = ["F_YR"]

YEAR_RE = re.compile(r"(20[12]\d)")
MIN_YEAR, MAX_YEAR = 2000, 2099


def year_of(path: Path) -> int | None:
    """파일·폴더 이름에서 연도(2010~2029)를 찾는다. 예) data/raw/flood/2022/침수흔적_2022.shp"""
    m = YEAR_RE.findall(str(path))
    return int(m[-1]) if m else None


def _first_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _year_from(value) -> int | None:
    """'20200728', '20250822 - 1031', 2018, 2018.0 → 연도. 알아볼 수 없으면 None"""
    if value is None or (isinstance(value, float) and value != value):
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    if len(text) < 4 or not text[:4].isdigit():
        return None
    y = int(text[:4])
    return y if MIN_YEAR <= y <= MAX_YEAR else None


def event_years(gdf: pd.DataFrame, file_year: int) -> list[int]:
    """기록마다 침수 연도: 침수 시작일 → F_YR → 파일 연도 순으로 처음 알아볼 수 있는 값"""
    cols = [c for c in DATE_COLUMNS + YEAR_COLUMNS if c in gdf.columns]
    out = []
    for values in zip(*(gdf[c] for c in cols), strict=True) if cols else [()] * len(gdf):
        year = next((y for y in map(_year_from, values) if y), None)
        out.append(year or file_year)
    return out


def read_shp(path: Path) -> gpd.GeoDataFrame:
    """한글 SHP 는 .cpg 가 없으면 대개 CP949 인코딩이다."""
    if path.with_suffix(".cpg").exists():
        gdf = gpd.read_file(path)
    else:
        gdf = gpd.read_file(path, encoding="cp949")
    if gdf.crs is None:
        gdf = gdf.set_crs(SOURCE_CRS)
    return gdf.to_crs(TARGET_CRS)


def normalize_traces(gdf: gpd.GeoDataFrame, file_year: int) -> gpd.GeoDataFrame:
    """원본 SHP → year(침수 연도), depth_m, geometry 표준 형태"""
    dcol = _first_col(gdf, DEPTH_COLUMNS)
    if dcol is None:
        raise ValueError(f"{file_year}년 SHP 에서 침수심 열을 찾지 못했습니다. 열 목록: {list(gdf.columns)}")
    out = gpd.GeoDataFrame(
        {
            "year": event_years(gdf, file_year),
            "depth_m": pd.to_numeric(gdf[dcol], errors="coerce").to_numpy(),
        },
        geometry=gdf.geometry.make_valid().to_numpy(),
        crs=gdf.crs,
    )
    return out[out.geometry.notna() & ~out.geometry.is_empty].reset_index(drop=True)


def find_shps(flood_dir: str | Path) -> list[Path]:
    return sorted(Path(flood_dir).rglob("*.shp"))


def load_traces(flood_dir: str | Path) -> gpd.GeoDataFrame:
    """폴더 안의 모든 연도 SHP 를 읽어 하나로 합친다."""
    parts = []
    for shp in find_shps(flood_dir):
        year = year_of(shp)
        if year is None:
            raise ValueError(f"파일·폴더 이름에서 연도를 찾지 못했습니다: {shp}")
        parts.append(normalize_traces(read_shp(shp), year))
    if not parts:
        logger.warning("SHP 파일을 찾지 못했습니다: %s", flood_dir)
        return gpd.GeoDataFrame(columns=["year", "depth_m", "geometry"], geometry="geometry", crs=TARGET_CRS)
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

    traces = traces.to_crs(segments.crs).reset_index(drop=True)
    traces["value"] = [trace_value(d, s) for d in traces["depth_m"]]
    seg = segments[["segment_id", "geometry"]]

    # ① 흔적과 직접 겹치는 구간: 값 그대로
    direct = gpd.sjoin(seg, traces[["year", "value", "geometry"]], predicate="intersects", how="inner")
    # ② 반경 30m 안의 구간: 값 × 0.5 (①에도 해당하면 아래 max 에서 ① 값이 남는다)
    near_zone = traces[["year", "value"]].assign(value=traces["value"] * s.flood_nearby_weight)
    near_zone = gpd.GeoDataFrame(near_zone, geometry=traces.geometry.buffer(s.flood_nearby_radius_m),
                                 crs=traces.crs)
    near = gpd.sjoin(seg, near_zone, predicate="intersects", how="inner")

    hits = pd.concat([direct[["segment_id", "year", "value"]], near[["segment_id", "year", "value"]]])
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
