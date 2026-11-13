"""위험도 캐시: 가짜 수집기 + 작은 정적 데이터로 스냅샷 모양과 강우 지연 규칙을 확인"""

from datetime import datetime, timedelta

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from app.config import Settings
from collectors.aws_rain import AwsRain
from collectors.common import KST, CollectorError
from collectors.its_control import ControlPoint
from collectors.kma_ultra import KmaRain
from collectors.seoul_rain import SeoulRain
from risk.cache import (
    CLOSURE_KIND,
    RULE2_REASON,
    Collectors,
    RiskCache,
    StaticData,
    build_risk_cache,
    closure_points,
    is_data_unavailable,
    is_scenario,
    scenario_rain,
)
from routing.astar import find_route
from routing.graph import build_graph
from routing.locate import to_lnglat

S = Settings(_env_file=None)
NOW = datetime(2026, 11, 9, 20, 0, tzinfo=KST)


def static():
    # 1: 침수흔적 구간 / 2: 침수흔적 있는 지하보도 / 3: 아무 요인 없음
    factors = {1: {"H": 1.0, "U": 0}, 2: {"H": 0.5, "U": 1}, 3: {"H": 0.0, "U": 0}}
    info = {
        1: {"H": 1.0, "H_count": 2, "H_years": "2011,2022", "U": 0, "U_reason": ""},
        2: {"H": 0.5, "H_count": 1, "H_years": "2022", "U": 1, "U_reason": "지하보도"},
        3: {"H": 0.0, "H_count": 0, "H_years": "", "U": 0, "U_reason": ""},
    }
    gdf = gpd.GeoDataFrame(
        {"segment_id": [1, 2, 3]},
        geometry=[LineString([(0, 0), (25, 0)]), LineString([(25, 0), (50, 0)]),
                  LineString([(0, 0), (0, 25)])],
        crs="EPSG:5186",
    )
    return StaticData(factors, info, gdf)


class Fake:
    """수집기 응답을 테스트마다 바꿔 끼우는 가짜. None 이면 CollectorError."""

    def __init__(self, seoul=None, kma=None, aws=None, its=()):
        self.seoul_v, self.kma_v, self.aws_v, self.its_v = seoul, kma, aws, its

    def _ret(self, v):
        if v is None:
            raise CollectorError("응답 없음")
        return v

    def collectors(self):
        return Collectors(
            seoul=lambda s, now: self._ret(self.seoul_v),
            kma=lambda s, now: self._ret(self.kma_v),
            aws=lambda s, now: self._ret(self.aws_v),
            its=lambda s: self._ret(self.its_v),
        )


def make(fake):
    return RiskCache(S, static_loader=static, collectors=fake.collectors())


def seoul(r1h, r3h=None, ago_min=0):
    return SeoulRain(r1h, r3h if r3h is not None else r1h, NOW - timedelta(minutes=ago_min))


def test_snapshot_none_before_first_update():
    cache = make(Fake(seoul=seoul(0)))
    assert cache.get_snapshot() is None
    assert is_data_unavailable(None, NOW, S)


def test_no_rain_normal_interval_and_zero_risk():
    cache = make(Fake(seoul=seoul(0)))
    assert cache.update(NOW) == 30
    snap = cache.get_snapshot()
    assert snap["risk"] == {} and snap["excluded"] == set() and snap["reasons"] == {}
    assert snap["rain"]["delayed"] is False and snap["rain"]["source"] == "seoul"
    assert not is_data_unavailable(snap, NOW, S)


def test_rain_risk_reasons_and_rain_interval():
    cache = make(Fake(seoul=seoul(60, 80), aws=AwsRain(5.0, NOW)))
    assert cache.update(NOW) == 10
    snap = cache.get_snapshot()
    assert snap["risk"][1] == pytest.approx(0.85)       # H=1, 60mm → 제안서 예시
    assert 3 not in snap["risk"]                         # 요인 없는 구간은 생략
    assert snap["reasons"][1] == ["침수흔적 2회 (2011·2022)"]
    assert snap["reasons"][2] == ["침수흔적 1회 (2022)", "지하보도"]
    assert snap["excluded"] == set()                     # 60mm·3h 80mm → 침수경보 기준 미충족
    assert snap["rain"]["rain_15m_mm"] == 5.0


def test_snapshot_feeds_find_route():
    # 스냅샷의 risk·excluded 를 find_route 에 그대로 넣을 수 있어야 함
    cache = make(Fake(seoul=seoul(80)))
    cache.update(NOW)
    snap = cache.get_snapshot()
    G = build_graph(
        [{"segment_id": 1, "u": 1, "v": 2, "length": 25}, {"segment_id": 2, "u": 2, "v": 3, "length": 25}],
        [{"node_id": 1, "x": 0, "y": 0}, {"node_id": 2, "x": 25, "y": 0}, {"node_id": 3, "x": 50, "y": 0}],
    )
    assert 2 in snap["excluded"]                         # 72mm 이상 → 지하보도 통행 제외
    assert find_route(G, 1, 2, alpha=2, risk=snap["risk"], excluded=snap["excluded"]) is not None
    assert find_route(G, 1, 3, alpha=2, risk=snap["risk"], excluded=snap["excluded"]) is None


def test_rule2_underground_excluded_with_reason():
    cache = make(Fake(seoul=seoul(55, 95)))              # 1h 50 이상 & 3h 90 이상
    cache.update(NOW)
    snap = cache.get_snapshot()
    assert snap["excluded"] == {2}
    assert snap["reasons"][2][-1] == RULE2_REASON


def test_rule1_control_point():
    cp = ControlPoint(x=-20, y=10, lng=127.0, lat=37.5, kind="땅꺼짐", message="싱크홀")
    cache = make(Fake(seoul=seoul(0), its=[cp]))
    cache.update(NOW)
    snap = cache.get_snapshot()
    assert snap["excluded"] == {1, 3}                    # (-20,10) 에서 구간 1 은 22m·3 은 20m·2 는 46m
    assert snap["reasons"][3] == ["도로 통제 (땅꺼짐)"]
    assert snap["control_points"][0]["kind"] == "땅꺼짐"


def test_its_failure_keeps_previous_controls():
    fake = Fake(seoul=seoul(0), its=[ControlPoint(-20, 10, 127.0, 37.5, "전면통제", "")])
    cache = make(fake)
    cache.update(NOW)
    fake.its_v = None                                    # 다음 수집 실패
    cache.update(NOW + timedelta(minutes=10))
    assert cache.get_snapshot()["excluded"] == {1, 3}


def test_seoul_delayed_falls_back_to_kma():
    # 서울시 마지막 관측이 25분 전 → 초단기실황 RN1 대체, delayed
    cache = make(Fake(seoul=seoul(10, ago_min=25), kma=KmaRain(60.0, NOW - timedelta(minutes=50))))
    assert cache.update(NOW) == 10
    snap = cache.get_snapshot()
    rain = snap["rain"]
    assert rain["delayed"] is True and rain["source"] == "kma"
    assert rain["rain_1h_mm"] == 60.0 and rain["rain_3h_mm"] is None
    assert snap["risk"][1] == pytest.approx(0.85)
    # 마지막 정상값은 25분 전 → 아직 이용 가능
    assert not is_data_unavailable(snap, NOW, S)


def test_seoul_20min_is_still_normal():
    cache = make(Fake(seoul=seoul(10, ago_min=20), kma=KmaRain(60.0, NOW)))
    cache.update(NOW)
    assert cache.get_snapshot()["rain"]["source"] == "seoul"


def test_stale_over_60min_is_unavailable():
    fake = Fake(seoul=seoul(10), kma=KmaRain(12.0, NOW))
    cache = make(fake)
    cache.update(NOW)
    fake.seoul_v = None                                  # 이후 서울시 수집이 계속 실패
    later = NOW + timedelta(minutes=61)
    cache.update(later)
    snap = cache.get_snapshot()
    assert snap["rain"]["source"] == "kma"
    assert snap["rain"]["last_normal_at"] == NOW.isoformat()
    assert is_data_unavailable(snap, later, S)
    assert not is_data_unavailable(snap, NOW + timedelta(minutes=60), S)


def test_all_rain_sources_fail_reuses_previous():
    fake = Fake(seoul=seoul(40))
    cache = make(fake)
    cache.update(NOW)
    fake.seoul_v = None
    cache.update(NOW + timedelta(minutes=30))            # 서울시·초단기실황 모두 실패
    rain = cache.get_snapshot()["rain"]
    assert rain["source"] == "previous" and rain["delayed"] is True and rain["rain_1h_mm"] == 40.0


def test_no_data_at_all():
    cache = make(Fake())
    assert cache.update(NOW) == 30
    snap = cache.get_snapshot()
    assert snap["rain"]["source"] == "none" and snap["risk"] == {}
    assert is_data_unavailable(snap, NOW, S)


def test_old_aws_ignored_and_15m_override():
    # 15분 20mm 이상(O_H) 이면 1시간 강우가 적어도 H=1 구간 0.85
    cache = make(Fake(seoul=seoul(10), aws=AwsRain(22.0, NOW)))
    cache.update(NOW)
    assert cache.get_snapshot()["risk"][1] == pytest.approx(0.85)
    # 오래된 AWS 값은 쓰지 않음
    cache = make(Fake(seoul=seoul(10), aws=AwsRain(22.0, NOW - timedelta(minutes=30))))
    cache.update(NOW)
    snap = cache.get_snapshot()
    assert snap["rain"]["rain_15m_mm"] is None and 1 not in snap["risk"]


# ---------- build_risk_cache · rain_mm 시나리오 ----------
def test_build_risk_cache_pure_function():
    snap = build_risk_cache(static(), scenario_rain(80), [], NOW, S, scenario=True)
    assert snap["risk"][1] == pytest.approx(0.85) and 3 not in snap["risk"]
    assert snap["excluded"] == {2}                       # 80mm ≥ 72mm → 침수흔적 있는 지하보도
    assert snap["reasons"][2][-1] == RULE2_REASON
    assert snap["rain"] == {"rain_1h_mm": 80.0, "rain_3h_mm": None, "rain_15m_mm": None, "updated_at": None,
                            "delayed": False, "source": "scenario", "last_normal_at": None}
    assert is_scenario(snap) and snap["computed_at"] == NOW.isoformat()
    assert not is_scenario(build_risk_cache(static(), scenario_rain(80), [], NOW, S))   # 기본은 실시간


def test_update_and_scenario_share_the_same_calculation():
    # 초단기실황 대체(3h·15m 모름)와 rain_mm 시나리오는 강우 입력이 같으므로 결과도 같아야 한다
    cache = make(Fake(kma=KmaRain(75.0, NOW)))
    cache.update(NOW)
    live, scen = cache.get_snapshot(), cache.scenario(75.0, now=NOW)
    assert live["rain"]["source"] == "kma" and is_scenario(scen) and not is_scenario(live)
    for key in ("risk", "excluded", "reasons"):
        assert live[key] == scen[key]


def test_scenario_3h_unknown_only_72mm_rule():
    cache = make(Fake(seoul=seoul(60, 95)))              # 실시간: 1h 60 & 3h 95 → 규칙 2 충족
    cache.update(NOW)
    assert cache.get_snapshot()["excluded"] == {2}
    assert cache.scenario(60, now=NOW)["excluded"] == set()  # 시나리오: 3h 모름 → 72mm 조건만
    assert cache.scenario(72, now=NOW)["excluded"] == {2}


def test_scenario_applies_latest_controls_and_keeps_cache():
    cp = ControlPoint(x=-20, y=10, lng=127.0, lat=37.5, kind="땅꺼짐", message="싱크홀")
    cache = make(Fake(seoul=seoul(0), its=[cp]))
    cache.update(NOW)
    before = cache.get_snapshot()
    scen = cache.scenario(80, now=NOW)
    assert scen["excluded"] == {1, 2, 3}                 # 규칙 1(구간 1·3) ∪ 규칙 2(구간 2)
    assert scen["reasons"][3] == ["도로 통제 (땅꺼짐)"]
    assert scen["control_points"][0]["kind"] == "땅꺼짐"
    assert cache.get_snapshot() is before and before["excluded"] == {1, 3}   # 캐시는 그대로


def test_scenario_before_first_update():
    cache = make(Fake())
    scen = cache.scenario(60, now=NOW)
    assert scen["risk"][1] == pytest.approx(0.85) and scen["control_points"] == []
    assert cache.get_snapshot() is None


# ---------- closures 통제 시나리오 ----------
def latlng(x, y):
    """미터 좌표(EPSG:5186) → (위도, 경도). 가상 구간 근처에 시나리오 통제 지점을 찍기 위해"""
    lng, lat = to_lnglat(x, y)
    return lat, lng


def test_closure_points_convert_to_meters():
    cp = closure_points([latlng(-20, 10)])[0]
    assert (cp.x, cp.y) == (pytest.approx(-20, abs=0.1), pytest.approx(10, abs=0.1))
    assert cp.kind == CLOSURE_KIND


def test_closures_exclude_like_rule1_and_keep_cache():
    cache = make(Fake(seoul=seoul(0)))
    cache.update(NOW)
    before = cache.get_snapshot()
    scen = cache.scenario(0, [latlng(-20, 10)], now=NOW)   # (-20,10): 구간 1 은 22m·3 은 20m·2 는 46m
    assert scen["excluded"] == {1, 3}
    assert scen["reasons"][3] == ["도로 통제 (시나리오)"]
    assert scen["control_points"][-1]["kind"] == "시나리오" and is_scenario(scen)
    assert cache.get_snapshot() is before and before["excluded"] == set()   # 캐시는 그대로


def test_closures_add_to_real_controls():
    cp = ControlPoint(x=50, y=10, lng=127.0, lat=37.5, kind="전면통제", message="")   # 구간 2 만 (10m)
    cache = make(Fake(seoul=seoul(0), its=[cp]))
    cache.update(NOW)
    scen = cache.scenario(0, [latlng(-20, 10)], now=NOW)
    assert scen["excluded"] == {1, 2, 3}
    assert scen["reasons"][2] == ["침수흔적 1회 (2022)", "지하보도", "도로 통제 (전면통제)"]


def test_closures_only_use_live_rain():
    # rain_mm 없이 closures 만 → 실시간 강우(3시간 강우 포함)를 그대로 써서 규칙 2 도 실제 조건으로
    cache = make(Fake(seoul=seoul(55, 95)))                 # 1h 55 & 3h 95 → 규칙 2 충족
    cache.update(NOW)
    live = cache.get_snapshot()
    scen = cache.scenario(closures=[latlng(-20, 10)], base_rain=live["rain"], now=NOW)
    assert scen["excluded"] == {1, 2, 3}                    # closures(1·3) ∪ 규칙 2(2)
    assert scen["rain"] == live["rain"]                     # 관측 시각·3시간 강우까지 실시간 그대로
    assert is_scenario(scen)


def test_scenario_needs_rain():
    with pytest.raises(ValueError):
        make(Fake()).scenario(closures=[latlng(0, 0)])


@pytest.mark.skipif(not __import__("pathlib").Path(S.segments_path).exists(), reason="segments.gpkg 없음")
def test_real_segments_with_fixtures():
    # 실제 강남역 보행망 + 샘플 응답 (1h 49.5mm, 15분 21.5mm, 강남역 사거리 전면통제)
    cache = RiskCache(S)
    assert cache.update() == 10
    snap = cache.get_snapshot()
    assert len(snap["risk"]) > 1000
    assert snap["excluded"]                              # 전면통제 지점 반경 30m
    assert all(sid in snap["reasons"] for sid in snap["excluded"])
    assert not is_data_unavailable(snap, s=S)
