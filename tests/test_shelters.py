"""이재민 임시주거시설 적재: 샘플 응답·가짜 페이지로 거르기·합치기·저장 규칙 확인 (네트워크 없음)"""

import csv
import json

import numpy as np
import pytest

from app.config import Settings
from collectors.common import CollectorError
from preprocess import shelters as sh
from preprocess.shelters import Shelter

S = Settings(_env_file=None)
LIVE = Settings(_env_file=None, collector_mode="live", seoul_opendata_key="k", seoul_shelter_service="SVC")


def row(no, name, lat, lng, addr="서울특별시 강남구 1", cap=100):
    """실제 응답(TbGtnVictP)과 같은 필드 이름의 한 행"""
    return {"FCLT_NO": no, "ACTC_FCLT_NM": name, "DADDR": addr, "VICTIM_ACTC_PWR": cap,
            "LAT": lat, "LOT": lng}


def test_fixture_select():
    res = sh.select(sh.fetch_rows(S), S)
    assert res.total == 10
    assert (res.no_coord, res.far, res.underground, res.merged) == (2, 1, 1, 1)
    names = [x.name for x in res.shelters]
    assert names[0] == "(샘플) 가나초등학교"                  # 강남역에서 가까운 순
    assert "(샘플) 사아복지관" not in names                    # 지하1층 강당 → 제외
    merged = next(x for x in res.shelters if x.shelter_id == "1003")
    assert merged.capacity == 96                              # 같은 건물 두 공간 72 + 24


def test_zero_capacity_is_unknown():
    rows = [row("1", "A", "37.5", "127.03", cap=0.0), row("2", "B", "37.5", "127.03", cap=189.0)]
    out, _ = sh.parse_rows(rows)
    assert [x.capacity for x in out] == [None, 189]        # 실제 응답은 189.0 처럼 실수로 온다


def test_parse_rows_drops_bad_coords_and_reads_fields():
    rows = [row("1", "A", "37.5", "127.03"), row("2", "B", "", "127.03"), row("3", "C", 0, 0),
            {"ACTC_FCLT_NM": "D", "LAT": 37.5, "LOT": 127.0, "VICTIM_ACTC_PWR": "1,200"}]
    out, dropped = sh.parse_rows(rows)
    assert dropped == 2
    assert [x.shelter_id for x in out] == ["1", "S0004"]      # 번호가 없으면 순번으로
    assert out[1].capacity == 1200
    assert out[0] == Shelter("1", "A", "서울특별시 강남구 1", 127.03, 37.5, 100)


@pytest.mark.parametrize("name, addr, expected", [
    ("가동주민센터", "강남구 1 (지하1층 강당)", True),
    ("지하 대강당", "", True),
    ("나초등학교", "강남역 지하철 2번 출구 앞", False),        # '지하철' 은 지하 시설이 아님
    ("다중학교", "강남구 3 (2층 체육관)", False),
])
def test_underground(name, addr, expected):
    assert sh.is_underground(Shelter("1", name, addr, 127.03, 37.5, None)) is expected


def test_merge_needs_same_name_and_near():
    a = Shelter("1", "가동주민센터", "", 127.0300, 37.5000, 100)
    b = Shelter("2", "가동 주민센터", "", 127.03001, 37.50001, None)   # 약 1m, 이름 같음 → 합침
    c = Shelter("3", "나초등학교", "", 127.03002, 37.50002, 50)        # 가깝지만 다른 이름 → 따로
    d = Shelter("4", "가동주민센터", "", 127.0350, 37.5000, 70)         # 이름 같지만 440m → 따로
    out = sh.merge_same_building([a, b, c, d])
    assert [x.shelter_id for x in out] == ["1", "3", "4"]
    assert out[0].capacity == 100                             # 모르는 수용인원은 더하지 않음
    assert a.capacity == 100                                  # 입력은 바꾸지 않음


def test_keep_radius():
    s = Settings(_env_file=None, shelter_keep_radius_m=500)
    res = sh.select([row("1", "A", 37.4990, 127.0300), row("2", "B", 37.5030, 127.0240)], s)
    assert [x.shelter_id for x in res.shelters] == ["1"] and res.far == 1


def test_nearest_node_distance():
    x, y = sh.to_xy(37.4979, 127.0276)
    nodes = np.array([[x + 30, y], [x + 500, y]])
    d = sh.nearest_node_m([Shelter("1", "A", "", 127.0276, 37.4979, None)], nodes)
    assert d[0] == pytest.approx(30, abs=0.01)


def test_csv_round_trip(tmp_path):
    items = [Shelter("1", "A, 강당", "주소", 127.03, 37.5, 300), Shelter("2", "B", "", 127.02, 37.49, None)]
    path = tmp_path / "shelters.csv"
    sh.write_csv(items, path)
    with path.open(encoding="utf-8") as f:
        assert next(csv.reader(f)) == ["shelter_id", "name", "address", "lng", "lat", "capacity"]
    assert sh.load_shelters(path) == items


def test_api_error_messages():
    with pytest.raises(CollectorError, match="INFO-100"):
        sh.parse_page('{"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키가 유효하지 않습니다."}}', "SVC")
    with pytest.raises(CollectorError, match="INFO-200"):
        sh.parse_page('{"SVC": {"RESULT": {"CODE": "INFO-200", "MESSAGE": "데이터 없음"}}}', "SVC")


def test_live_pages_through_all_rows(monkeypatch):
    # 2,345건 → 1~1000, 1001~2000, 2001~3000 세 번 요청
    calls = []

    def fake_get(url, params=None):
        start, end = (int(v) for v in url.rstrip("/").split("/")[-2:])
        calls.append((start, end))
        n = max(0, min(end, 2345) - start + 1)
        page = [row(str(start + i), "A", 37.5, 127.03) for i in range(n)]
        return json.dumps({"SVC": {"list_total_count": 2345, "RESULT": {"CODE": "INFO-000"}, "row": page}})

    monkeypatch.setattr(sh, "http_get", fake_get)
    rows = sh.fetch_rows(LIVE)
    assert len(rows) == 2345
    assert calls == [(1, 1000), (1001, 2000), (2001, 3000)]
    assert "/k/json/SVC/" in sh.URL.format(key="k", service="SVC", start=1, end=2)


def test_live_needs_key():
    with pytest.raises(CollectorError, match="SEOUL_OPENDATA_KEY"):
        sh.fetch_rows(Settings(_env_file=None, collector_mode="live"))
