"""위험도 함수: 제안서 계산 예시 표를 정답으로 확인"""

import pytest

from app.config import Settings
from risk.exclusion import flood_warning_met, rule1_excluded, rule2_excluded
from risk.model import activations, compute_risks, reasons, segment_risk

S = Settings(_env_file=None)


@pytest.mark.parametrize(
    "factors, rain, expected",
    [
        # 1차 (H·U 만)
        ({"H": 1}, 40, 0.43),
        ({"H": 1}, 60, 0.85),
        ({"H": 1}, 116, 0.85),
        ({"U": 1}, 40, 0.12),
        ({"U": 1}, 60, 0.36),
        ({"U": 1}, 116, 0.50),
        # 2차 (L 포함)
        ({"H": 1, "L": 0.6}, 40, 0.51),
        ({"H": 1, "L": 0.6}, 60, 0.90),
        ({"L": 0.8}, 40, 0.20),
        ({"L": 0.8}, 60, 0.40),
        ({"U": 1, "L": 0.8}, 40, 0.295),
        ({"U": 1, "L": 0.8}, 60, 0.61),
        ({"U": 1, "L": 0.8}, 116, 0.70),
    ],
)
def test_proposal_examples(factors, rain, expected):
    assert segment_risk(factors, rain, s=S) == pytest.approx(expected, abs=0.006)


def test_no_rain_no_risk():
    # 강우가 a(30mm) 이하면 정적 요인이 있어도 위험도 0
    assert segment_risk({"H": 1, "U": 1}, 30, s=S) == 0.0
    assert segment_risk({}, 116, s=S) == 0.0


def test_flood_forecast_override():
    # 15분 20mm 이상이면 1시간 강우가 적어도 g_H = 1 → H=1 구간 위험도 0.85
    assert activations(10, rain_15m_mm=20, s=S)["H"] == 1.0
    assert segment_risk({"H": 1}, 10, rain_15m_mm=20, s=S) == pytest.approx(0.85)
    # U 는 침수예보 조건과 무관
    assert activations(10, rain_15m_mm=20, s=S)["U"] == 0.0
    # 1시간 55mm 이상도 충족
    assert activations(55, s=S)["H"] == 1.0


def test_compute_risks_skips_zero():
    segs = {1: {"H": 1}, 2: {"U": 1}, 3: {}}
    out = compute_risks(segs, 40, s=S)
    assert set(out) == {1, 2}
    assert out[1] == pytest.approx(0.425)


def test_reasons():
    assert reasons(H=1, H_count=3, H_years="2010,2011,2022") == ["침수흔적 3회 (2010·2011·2022)"]
    assert reasons(U=1, U_reason="지하보도") == ["지하보도"]
    assert reasons() == []


def test_flood_warning():
    assert flood_warning_met(72, None, S)
    assert not flood_warning_met(71, None, S)
    assert flood_warning_met(50, 90, S)
    assert not flood_warning_met(50, 89, S)
    assert not flood_warning_met(49, 120, S)


def test_rule2_only_underground_with_flood_factor():
    segs = {1: {"U": 1, "H": 0.5}, 2: {"U": 1, "H": 0}, 3: {"U": 0, "H": 1}}
    assert rule2_excluded(segs, 72, None, S) == {1}
    assert rule2_excluded(segs, 60, 80, S) == set()


def test_rule1_radius():
    import geopandas as gpd
    from shapely.geometry import LineString

    gdf = gpd.GeoDataFrame(
        {"segment_id": [1, 2]},
        geometry=[LineString([(0, 0), (25, 0)]), LineString([(100, 0), (125, 0)])],
        crs="EPSG:5186",
    )
    assert rule1_excluded(gdf, [(60, 0)], S) == set()     # 구간 1 까지 35m, 구간 2 까지 40m
    assert rule1_excluded(gdf, [(40, 0)], S) == {1}       # 구간 1 까지 15m
    assert rule1_excluded(gdf, [(60, 0), (120, 0)], S) == {2}
    assert rule1_excluded(gdf, [], S) == set()
