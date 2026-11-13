"""이재민 임시주거시설 → data/shelters.csv (경로 없음 안내용, 팀 논의 10번)

출처: 서울 열린데이터광장 OA-21068 '서울시 이재민임시주거시설' Open API (SEOUL_OPENDATA_KEY),
      서비스명 TbGtnVictP. 서울시 전체 약 1,200곳 (2026-11-13 live 확인).
      좌표는 위경도(GPS)와 X·Y(GRS80 중부원점)를 함께 주며, 여기서는 위경도를 그대로 쓴다.
      (비슷한 TlInetqkP 는 지진겸용 OA-21064 목록 1,100곳이며 수용인원 필드가 없다.)

사용법 (레포 최상위 폴더에서, 수집기와 같은 live/fixture 모드)
    COLLECTOR_MODE=live python -m preprocess.shelters --inspect   # 필드 이름·앞 3행만 확인 (저장 안 함)
    COLLECTOR_MODE=live python -m preprocess.shelters             # data/shelters.csv 저장 + 개수 출력
    python -m preprocess.shelters                                 # fixture(가상 샘플) → shelters_sample.csv

처리 순서
1. 서울시 전체 시설을 1,000건씩 나눠 받는다 (서울시 Open API 는 한 번에 최대 1,000건).
2. 행마다 번호·시설명·주소·위경도·수용인원을 꺼낸다. 필드 이름은 후보 목록 중 응답에 있는 것을 쓴다.
3. 좌표가 없거나 서울 밖(잘못 입력된 값)이면 버린다.
4. 강남역에서 shelter_keep_radius_m(기본 2km) 안만 남긴다.
5. 지하 시설(시설명·주소에 '지하', '지하철' 제외)은 침수 때 갈 곳이 아니므로 뺀다.
6. 같은 건물의 여러 공간(이름이 같고 20m 안)은 한 줄로 합치고 수용인원을 더한다.
7. 강남역에서 가까운 순으로 저장하고, 보행 그래프(segments.gpkg) 노드까지 거리로 '그래프 안' 시설 수를 센다.

결과 data/shelters.csv (WGS84): shelter_id, name, address, lng, lat, capacity (수용인원 모르면 빈칸)
"""

import argparse
import csv
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from pyproj import Transformer

from app.config import Settings, settings
from collectors.common import CollectorError, http_get, is_live, read_fixture

URL = "http://openapi.seoul.go.kr:8088/{key}/json/{service}/{start}/{end}/"
FIXTURE = "seoul_shelters.json"
FIXTURE_SERVICE = "TbGtnVictP"    # 샘플 응답 안의 서비스명
PAGE = 1000                     # 서울시 Open API 한 번 최대 요청 건수
COLUMNS = ["shelter_id", "name", "address", "lng", "lat", "capacity"]
SAMPLE_OUT = "data/shelters_sample.csv"   # fixture 결과는 가상 샘플이라 커밋하는 파일과 따로 둔다

# 응답 필드 이름 (2026-11-13 live --inspect 로 확인). 실제 응답 필드:
#   FCLT_NO·FCLT_SN(번호) ACTC_FCLT_NM(시설명) DADDR(도로명 주소) LAT·LOT(위경도) XCRD·YCRD(중부원점)
#   VICTIM_ACTC_PWR(이재민 수용능력, 명) FCAR(면적 ㎡) FCLT_SE_CD(시설 구분) CTPV_NM·SGG_NM(시·구) 코드류
# 이름이 바뀌면 후보를 튜플에 추가한다 (앞에 있을수록 우선).
FIELDS = {
    "id": ("FCLT_NO",),
    "name": ("ACTC_FCLT_NM",),
    "address": ("DADDR",),
    "lat": ("LAT",),
    "lng": ("LOT",),
    "capacity": ("VICTIM_ACTC_PWR",),
}
SEOUL_BOUNDS = (126.76, 37.41, 127.19, 37.72)   # 경도·위도 최소/최대 (서울 밖 좌표는 입력 오류로 본다)
UNDERGROUND = re.compile(r"지하(?!철)")
MERGE_M = 20.0                  # 같은 건물로 볼 거리

_TO_5186 = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)


@dataclass
class Shelter:
    shelter_id: str
    name: str
    address: str
    lng: float
    lat: float
    capacity: int | None


# ---------- 응답 해석 ----------
def parse_page(text: str, service: str) -> tuple[list[dict], int]:
    """응답 한 페이지 → (행 목록, 전체 건수)"""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise CollectorError(f"임시주거시설 응답 해석 실패: {text[:200]}") from e
    body = data.get(service)
    if body is None:
        # 키 오류·서비스명 오류는 {"RESULT": {"CODE": ..., "MESSAGE": ...}} 만 온다
        res = data.get("RESULT", {})
        raise CollectorError(
            f"임시주거시설 API 오류 {res.get('CODE')}: {res.get('MESSAGE')} (서비스명 {service})"
        )
    code = body.get("RESULT", {}).get("CODE", "")
    if code not in ("INFO-000", ""):
        raise CollectorError(f"임시주거시설 API 오류 {code}: {body['RESULT'].get('MESSAGE')}")
    return body.get("row", []), int(body.get("list_total_count", 0))


def fetch_rows(s: Settings = settings, service: str | None = None) -> list[dict]:
    """live: 서울시 전체를 1,000건씩 받음 / fixture: 샘플 응답"""
    service = service or s.seoul_shelter_service
    if not is_live(s):
        return parse_page(read_fixture(FIXTURE), FIXTURE_SERVICE)[0]
    if not s.seoul_opendata_key:
        raise CollectorError("SEOUL_OPENDATA_KEY 가 없습니다")
    rows, start, total = [], 1, None
    while total is None or start <= total:
        url = URL.format(key=s.seoul_opendata_key, service=service, start=start, end=start + PAGE - 1)
        page, total = parse_page(http_get(url), service)
        if not page:
            break
        rows += page
        start += PAGE
    return rows


def _pick(row: dict, kind: str):
    """후보 이름 중 응답에 있고 값이 비어 있지 않은 첫 필드의 값"""
    for key in FIELDS[kind]:
        v = row.get(key)
        if v is not None and str(v).strip() != "":
            return v
    return None


def _to_int(v) -> int | None:
    try:
        n = int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def parse_rows(rows: list[dict]) -> tuple[list[Shelter], int]:
    """행 → Shelter 목록. 좌표가 없거나 서울 밖인 행은 버리고 그 수를 함께 돌려준다."""
    out, dropped = [], 0
    for i, r in enumerate(rows, start=1):
        try:
            lat, lng = float(_pick(r, "lat")), float(_pick(r, "lng"))
        except (TypeError, ValueError):
            dropped += 1
            continue
        min_lng, min_lat, max_lng, max_lat = SEOUL_BOUNDS
        if not (min_lng <= lng <= max_lng and min_lat <= lat <= max_lat):
            dropped += 1
            continue
        sid = _pick(r, "id")
        out.append(Shelter(
            shelter_id=str(sid).strip() if sid is not None else f"S{i:04d}",
            name=str(_pick(r, "name") or "").strip(),
            address=str(_pick(r, "address") or "").strip(),
            lng=round(lng, 6), lat=round(lat, 6),
            capacity=_to_int(_pick(r, "capacity")),
        ))
    return out, dropped


# ---------- 거르기·합치기 ----------
def to_xy(lat: float, lng: float) -> tuple[float, float]:
    return _TO_5186.transform(lng, lat)


def distance_m(a: Shelter, lat: float, lng: float) -> float:
    (xa, ya), (xb, yb) = to_xy(a.lat, a.lng), to_xy(lat, lng)
    return math.hypot(xa - xb, ya - yb)


def is_underground(sh: Shelter) -> bool:
    return bool(UNDERGROUND.search(sh.name) or UNDERGROUND.search(sh.address))


def _same_name(a: str, b: str) -> bool:
    """공백·괄호 안 설명을 빼고 비교 ('이촌1동주민센터 (대강당)' = '이촌1동 주민센터')"""
    def norm(t: str) -> str:
        return re.sub(r"\(.*?\)|\s", "", t)
    return norm(a) == norm(b)


def merge_same_building(shelters: list[Shelter]) -> list[Shelter]:
    """이름이 같고 20m 안이면 한 시설로 합치고 수용인원을 더한다 (앞에 나온 행의 번호·주소를 쓴다)."""
    kept: list[Shelter] = []
    for sh in shelters:
        for k in kept:
            if _same_name(k.name, sh.name) and distance_m(k, sh.lat, sh.lng) <= MERGE_M:
                if sh.capacity is not None:
                    k.capacity = (k.capacity or 0) + sh.capacity
                break
        else:
            kept.append(Shelter(**asdict(sh)))
    return kept


@dataclass
class Result:
    shelters: list[Shelter]     # 저장할 시설 (강남역에서 가까운 순)
    total: int                  # 받은 행 수
    no_coord: int               # 좌표 없음·서울 밖
    far: int                    # 보관 반경 밖
    underground: int            # 지하 시설
    merged: int                 # 같은 건물로 합쳐진 행


def select(rows: list[dict], s: Settings = settings) -> Result:
    shelters, no_coord = parse_rows(rows)
    near = [sh for sh in shelters
            if distance_m(sh, s.pilot_center_lat, s.pilot_center_lon) <= s.shelter_keep_radius_m]
    above = [sh for sh in near if not is_underground(sh)]
    merged = merge_same_building(above)
    merged.sort(key=lambda sh: distance_m(sh, s.pilot_center_lat, s.pilot_center_lon))
    return Result(merged, len(rows), no_coord, len(shelters) - len(near),
                  len(near) - len(above), len(above) - len(merged))


# ---------- 그래프 안 시설 세기 ----------
def nearest_node_m(shelters: list[Shelter], node_xy: np.ndarray) -> list[float]:
    """시설마다 가장 가까운 보행 그래프 노드까지 거리 (m). node_xy: (N, 2) EPSG:5186"""
    out = []
    for sh in shelters:
        x, y = to_xy(sh.lat, sh.lng)
        out.append(float(np.hypot(node_xy[:, 0] - x, node_xy[:, 1] - y).min()))
    return out


def load_node_xy(gpkg: str) -> np.ndarray:
    import geopandas as gpd

    nodes = gpd.read_file(gpkg, layer="nodes")
    return np.column_stack([nodes.geometry.x, nodes.geometry.y])


# ---------- 파일 ----------
def write_csv(shelters: list[Shelter], out: str | Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for sh in shelters:
            row = asdict(sh)
            row["capacity"] = "" if sh.capacity is None else sh.capacity
            w.writerow(row)


def load_shelters(path: str | Path | None = None) -> list[Shelter]:
    """data/shelters.csv → Shelter 목록 (경로 없음 안내에서 사용)"""
    with Path(path or settings.shelters_path).open(encoding="utf-8") as f:
        return [
            Shelter(r["shelter_id"], r["name"], r["address"], float(r["lng"]), float(r["lat"]),
                    _to_int(r["capacity"]))
            for r in csv.DictReader(f)
        ]


def summary(res: Result, node_dist: list[float] | None, s: Settings = settings) -> str:
    center = (s.pilot_center_lat, s.pilot_center_lon)
    km = s.shelter_keep_radius_m / 1000
    lines = [
        f"받은 행 {res.total} → 좌표 없음·서울 밖 {res.no_coord} · 강남역 {km:g}km 밖 {res.far} · "
        f"지하 시설 {res.underground} · 같은 건물 합침 {res.merged} 제외",
        f"저장 {len(res.shelters)}곳 (강남역 {km:g}km 안)",
        f"  시범 지역(반경 {s.pilot_radius_m / 1000:g}km) 안: "
        f"{sum(distance_m(sh, *center) <= s.pilot_radius_m for sh in res.shelters)}곳",
    ]
    if node_dist is not None:
        inside = [(sh, d) for sh, d in zip(res.shelters, node_dist, strict=True) if d <= s.shelter_snap_max_m]
        lines.append(f"  보행 그래프 노드 {s.shelter_snap_max_m:g}m 안 (경로 안내 가능): {len(inside)}곳")
        for sh, d in inside[:15]:
            lines.append(f"    - {sh.name} | 강남역 {distance_m(sh, *center):.0f}m · 노드 {d:.0f}m · "
                         f"수용 {sh.capacity if sh.capacity is not None else '?'}명")
    return "\n".join(lines)


def inspect(rows: list[dict]) -> str:
    keys = sorted({k for r in rows for k in r})
    picked = {kind: next((k for k in cands if k in keys), None) for kind, cands in FIELDS.items()}
    lines = [f"행 수 {len(rows)}", f"필드: {', '.join(keys)}", f"사용할 필드: {picked}"]
    lines += [json.dumps(r, ensure_ascii=False) for r in rows[:3]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="이재민 임시주거시설 → data/shelters.csv")
    ap.add_argument("--inspect", action="store_true", help="필드 이름과 앞 3행만 출력 (저장 안 함)")
    ap.add_argument("--service", default=None, help="서울시 Open API 서비스명 (기본: config)")
    ap.add_argument("--out", default=None, help="저장 경로 (기본: live 는 data/shelters.csv)")
    ap.add_argument("--gpkg", default=settings.segments_path, help="보행 그래프 (그래프 안 시설 세기)")
    args = ap.parse_args(argv)

    service = args.service or settings.seoul_shelter_service
    mode = "live" if is_live() else "fixture(가상 샘플)"
    print(f"출처: 서울시 Open API {service} · 모드 {mode}")
    rows = fetch_rows(settings, service)
    if args.inspect:
        print(inspect(rows))
        return

    res = select(rows)
    out = args.out or (settings.shelters_path if is_live() else SAMPLE_OUT)
    write_csv(res.shelters, out)
    node_dist = nearest_node_m(res.shelters, load_node_xy(args.gpkg)) if Path(args.gpkg).exists() else None
    print(summary(res, node_dist))
    print(f"→ {out}")


if __name__ == "__main__":
    main()
