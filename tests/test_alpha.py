"""α 실험: 가상 격자 지도에서 지표 계산과 α 선택 규칙 확인"""

import pandas as pd
import pytest

from experiments.alpha import choose_alpha, evaluate, pick_pairs, summarize
from routing.graph import build_graph


def grid(n=5, step=100):
    """n×n 격자. 가운데 세로줄(x=200)의 세로 구간이 위험 구간"""
    nodes, segs, risk = [], [], {}
    nid = lambda i, j: i * n + j + 1  # noqa: E731
    for i in range(n):
        for j in range(n):
            nodes.append({"node_id": nid(i, j), "x": j * step, "y": i * step})
    sid = 1
    for i in range(n):
        for j in range(n):
            if j + 1 < n:
                segs.append({"segment_id": sid, "u": nid(i, j), "v": nid(i, j + 1), "length": step})
                sid += 1
            if i + 1 < n:
                segs.append({"segment_id": sid, "u": nid(i, j), "v": nid(i + 1, j), "length": step})
                if j == n // 2:
                    risk[sid] = 0.85
                sid += 1
    return build_graph(segs, nodes), risk


def test_pick_pairs_reproducible():
    G, _ = grid()
    p1 = pick_pairs(G, n=10, seed=1, min_m=100, max_m=600)
    p2 = pick_pairs(G, n=10, seed=1, min_m=100, max_m=600)
    assert p1 == p2 and len(p1) == 10


def test_evaluate_and_summarize():
    G, risk = grid()
    # 왼쪽 아래 → 가운데 위: α=0 은 위험한 가운데 세로줄을 따라갈 수도 있음, α 가 크면 피함
    pairs = [(1 * 5 + 2 + 1, 4 * 5 + 2 + 1), (1, 25)]
    df = evaluate(G, pairs, risk, [0, 5, 50], speed_mps=1.0)
    assert set(df["alpha"]) == {0, 5, 50}
    assert (df.loc[df["alpha"] == 0, "detour"] == 0).all()
    s = summarize(df).set_index("alpha")
    assert s.loc[0, "danger_reduction"] == pytest.approx(0)
    # α 가 클수록 위험 노출은 줄고 추가거리는 늘어남
    assert s.loc[50, "danger_m_total"] <= s.loc[5, "danger_m_total"] <= s.loc[0, "danger_m_total"]
    assert s.loc[50, "detour_mean"] >= s.loc[0, "detour_mean"]


def summary_table(reductions, p90s):
    alphas = [0, 1, 2, 3, 5]
    return pd.DataFrame({"alpha": alphas, "danger_reduction": reductions, "detour_p90": p90s})


def test_choose_alpha_converges():
    # 감소율이 3에서 5로 갈 때 0.5%p 만 늘어남 → 3 선택
    s = summary_table([0, 0.30, 0.50, 0.60, 0.605], [0, 0.05, 0.10, 0.15, 0.18])
    assert choose_alpha(s)["alpha"] == 3


def test_choose_alpha_limited_by_detour():
    # 계속 늘어나지만 α=3 부터 상위 10% 추가거리가 20% 초과 → 기준 만족하는 가장 큰 α=2
    s = summary_table([0, 0.30, 0.50, 0.60, 0.70], [0, 0.05, 0.15, 0.25, 0.30])
    assert choose_alpha(s)["alpha"] == 2


def test_choose_alpha_none():
    s = summary_table([0, 0.3, 0.5, 0.6, 0.7], [0, 0.3, 0.3, 0.3, 0.3])
    assert choose_alpha(s)["alpha"] is None
