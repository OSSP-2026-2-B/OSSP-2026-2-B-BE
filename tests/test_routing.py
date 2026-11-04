"""가상 지도로 경로 탐색 확인

  1 ──(10)── 2 ──(11)── 3        위쪽 길: 100m + 100m = 200m (짧음)
  │                     │
 (12)                  (13)      세로: 각 50m
  │                     │
  4 ──(14)── 5 ──(15)── 6        아래쪽 길: 50 + 100 + 100 + 50 = 300m (김)

괄호 안 숫자는 segment_id. 1 → 3 으로 간다.
"""

import pytest

from routing.astar import find_route
from routing.cost import risk_grade, segment_cost
from routing.graph import build_graph

NODES = [
    {"node_id": 1, "x": 0, "y": 50}, {"node_id": 2, "x": 100, "y": 50}, {"node_id": 3, "x": 200, "y": 50},
    {"node_id": 4, "x": 0, "y": 0}, {"node_id": 5, "x": 100, "y": 0}, {"node_id": 6, "x": 200, "y": 0},
]
SEGMENTS = [
    {"segment_id": 10, "u": 1, "v": 2, "length": 100, "U": 0},
    {"segment_id": 11, "u": 2, "v": 3, "length": 100, "U": 0},
    {"segment_id": 12, "u": 1, "v": 4, "length": 50, "U": 0},
    {"segment_id": 13, "u": 3, "v": 6, "length": 50, "U": 0},
    {"segment_id": 14, "u": 4, "v": 5, "length": 100, "U": 0},
    {"segment_id": 15, "u": 5, "v": 6, "length": 100, "U": 0},
]


@pytest.fixture
def G():
    return build_graph(SEGMENTS, NODES)


def test_segment_cost_formula():
    # 위험도 0 이면 보행 시간과 같고, 0.9 · α=5 이면 (1 + 5 × 0.81) = 5.05 배
    assert segment_cost(25, 0.0, 5, 1.0) == 25
    assert segment_cost(25, 0.9, 5, 1.0) == pytest.approx(25 * 5.05)
    with pytest.raises(ValueError):
        segment_cost(25, 1.5, 5, 1.0)


def test_risk_grade_boundaries():
    assert risk_grade(0.29) == "안전"
    assert risk_grade(0.3) == "주의"
    assert risk_grade(0.6) == "위험"
    assert risk_grade(0.8) == "고위험"


def test_no_risk_takes_shortest(G):
    r = find_route(G, 1, 3, alpha=5)
    assert r.nodes == [1, 2, 3]
    assert r.distance_m == 200


def test_shortest_mode_ignores_risk(G):
    # α=0 (최단) 이면 위쪽 길이 위험해도 그대로 지나간다
    r = find_route(G, 1, 3, alpha=0, risk={10: 0.9})
    assert r.nodes == [1, 2, 3]
    assert r.risk_segment_count == 1


def test_balanced_mode_avoids_high_risk(G):
    # α=5 (균형) 이면 위쪽 길 10번(위험도 0.9)을 피해 아래로 돌아간다
    r = find_route(G, 1, 3, alpha=5, risk={10: 0.9})
    assert r.nodes == [1, 4, 5, 6, 3]
    assert r.distance_m == 300
    assert r.risk_segment_count == 0


def test_balanced_mode_keeps_low_risk(G):
    # 주의 수준(0.3) 은 페널티가 작아(1 + 5 × 0.09 = 1.45배) 100m 를 더 돌지 않는다
    r = find_route(G, 1, 3, alpha=5, risk={10: 0.3})
    assert r.nodes == [1, 2, 3]


def test_excluded_segment_is_never_used(G):
    # 통행 제외는 모드와 관계없이 지나가지 않는다 (최단 모드도)
    r = find_route(G, 1, 3, alpha=0, excluded={11})
    assert 11 not in r.segment_ids
    assert r.nodes == [1, 4, 5, 6, 3]


def test_no_route_returns_none(G):
    # 3 으로 들어가는 길이 모두 제외되면 경로 없음 → API 에서 임시주거시설 안내
    assert find_route(G, 1, 3, alpha=5, excluded={11, 13}) is None