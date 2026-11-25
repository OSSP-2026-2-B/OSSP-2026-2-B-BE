"""가상 지도로 경로 탐색 확인

  1 ──(10)── 2 ──(11)── 3        위쪽 길: 100m + 100m = 200m (짧음)
  │                     │
 (12)                  (13)      세로: 각 50m
  │                     │
  4 ──(14)── 5 ──(15)── 6        아래쪽 길: 50 + 100 + 100 + 50 = 300m (김)

괄호 안 숫자는 segment_id. 1 → 3 으로 간다.
"""

import pytest

from app.config import Settings
from routing.astar import find_route, min_danger_length
from routing.cost import risk_grade, route_grade, segment_cost
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


def test_route_grade():
    # 인자: 위험 이상 통과 거리, 가장 높은 위험도, 주의 통과 거리
    assert route_grade(150, 0.9, 0) == "위험"    # 위험 구간을 100m 이상 지남
    assert route_grade(150, 0.9, 500) == "위험"  # 주의 구간이 길어도 위험이 우선
    assert route_grade(25, 0.9, 0) == "주의"     # 고위험 구간 1개(25m)만 지남
    assert route_grade(0, 0.5, 150) == "주의"    # 위험 구간 없이 주의 구간을 100m 이상 지남
    assert route_grade(0, 0.5, 100) == "주의"    # 경계값 100m 는 주의
    assert route_grade(0, 0.5, 50) == "안전"     # 주의 구간을 100m 보다 짧게 지남
    assert route_grade(0, 0.0, 0) == "안전"


def test_route_grade_uses_settings():
    S = Settings(_env_file=None, route_caution_length_m=40)
    assert route_grade(0, 0.5, 50, S) == "주의"   # 기준을 40m 로 낮추면 50m 도 주의


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


def test_card_values(G):
    # 최단(α=0)은 위쪽 길 10번(100m, 위험도 0.9) + 11번(100m, 0)을 지난다
    r = find_route(G, 1, 3, alpha=0, risk={10: 0.9})
    assert r.danger_length_m == 100           # 0.9 ≥ 0.6 인 10번만
    assert r.risk_cost == pytest.approx(90)   # 100 × 0.9 + 100 × 0

    # 균형(α=5)은 아래로 돌아가서 위험 구간을 지나지 않는다
    r = find_route(G, 1, 3, alpha=5, risk={10: 0.9})
    assert r.danger_length_m == 0
    assert r.risk_cost == 0


def test_card_values_caution_only(G):
    # 주의(0.3)는 위험 통과 거리에는 안 들어가고 주의 통과 거리·누적 위험에 들어간다
    r = find_route(G, 1, 3, alpha=5, risk={10: 0.3})
    assert r.danger_length_m == 0
    assert r.caution_length_m == 100
    assert r.risk_cost == pytest.approx(30)   # 100 × 0.3


def test_caution_length_boundaries(G):
    # 0.3 이상 0.6 미만만 주의. 0.6 은 위험, 0.29 는 안전으로 어느 쪽에도 안 들어간다
    r = find_route(G, 1, 3, alpha=0, risk={10: 0.6, 11: 0.3})
    assert r.danger_length_m == 100
    assert r.caution_length_m == 100
    r = find_route(G, 1, 3, alpha=0, risk={10: 0.29, 11: 0.59})
    assert r.danger_length_m == 0
    assert r.caution_length_m == 100

def test_min_danger_length_no_risk(G):
    assert min_danger_length(G, 1, 3) == 0


def test_min_danger_length_picks_least_danger(G):
    # 위쪽 길은 10번(100m) 이 위험, 아래쪽 길은 12번(50m) 이 위험 → 어느 길로 가도 최소 50m 는 지나야 함
    risk = {10: 0.9, 12: 0.7}
    assert min_danger_length(G, 1, 3, risk) == 50
    # 최단 경로(위쪽)는 100m 를 지나므로 피할 수 있었던 위험은 100 − 50 = 50m
    assert find_route(G, 1, 3, alpha=0, risk=risk).danger_length_m == 100


def test_min_danger_length_prefers_safe_detour(G):
    # 위쪽 길만 위험하면 더 긴 아래쪽 길(300m)로 위험 없이 갈 수 있다 → 0
    assert min_danger_length(G, 1, 3, {10: 0.9}) == 0
    # 주의(0.3~0.6) 구간은 '위험'이 아니므로 세지 않는다
    assert min_danger_length(G, 1, 3, {10: 0.5, 14: 0.5}) == 0


def test_min_danger_length_never_uses_excluded(G):
    # 아래쪽 길 12번이 통행 제외면 위험한 위쪽 길밖에 없다 → 100m
    # (통행 제외 구간은 위험이 없어도 지나가지 않음)
    assert min_danger_length(G, 1, 3, {10: 0.9}, excluded={12}) == 100
    # 3 으로 들어가는 길이 모두 제외되면 경로 없음
    assert min_danger_length(G, 1, 3, {10: 0.9}, excluded={11, 13}) is None


def test_min_danger_length_parallel_segments():
    # 1–2 사이에 위험한 10번(100m) 과 더 긴 안전한 16번(120m) 이 나란히 있으면 16번을 고른다
    segments = [*SEGMENTS, {"segment_id": 16, "u": 1, "v": 2, "length": 120, "U": 0}]
    G = build_graph(segments, NODES)
    assert min_danger_length(G, 1, 3, {10: 0.9, 12: 0.9}) == 0
    assert min_danger_length(G, 1, 3, {10: 0.9, 12: 0.9, 16: 0.6}) == 50   # 16번도 위험하면 아래쪽 50m
