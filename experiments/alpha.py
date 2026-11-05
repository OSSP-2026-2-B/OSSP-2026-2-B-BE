"""α(회피 강도) 확정 실험 (기획안 4.4).

실제 강남역 보행망(data/segments.gpkg)에서 출발·도착 150쌍을 뽑아
1시간 강우 60mm 상황의 위험도로 α 를 바꿔 가며 경로를 찾고, 최단 경로(α=0)와 비교한다.

측정값 (α 마다)
- 추가거리 비율: (경로 거리 ÷ 최단 거리) - 1. 쌍별 값의 평균·상위 10%(90 백분위)
- 위험 노출 감소율: 1 - (모든 쌍의 '위험' 이상 구간 길이 합 ÷ α=0 일 때의 합)

균형 모드 α 선택 기준 (팀 합의)
① 상위 10% 추가거리가 20% 이내
② 위험 노출 감소율이 수렴: 다음 α 로 올려도 감소율이 1%p 미만으로만 늘어남
→ ①을 만족하는 α 중 ②를 처음 만족하는 가장 작은 α.
  ①을 만족하는 α 가 ②에 이르지 못하면, ①을 만족하는 가장 큰 α.

실행 (레포 최상위 폴더에서)
    python -m experiments.alpha
결과: experiments/results/ 에 쌍별 CSV, α별 요약 CSV, 그래프 PNG
"""

import argparse
import math
import random
from pathlib import Path

import networkx as nx
import pandas as pd

from risk.model import compute_risks
from routing.astar import DANGER, find_route
from routing.walk import walk_speed

DEFAULT_ALPHAS = [0, 1, 2, 3, 4, 5, 7, 10, 15, 20, 30]
RESULTS_DIR = Path("experiments/results")


def pick_pairs(
    G: nx.MultiGraph, n: int = 150, seed: int = 42, min_m: float = 300.0, max_m: float = 1500.0
) -> list[tuple[int, int]]:
    """가장 큰 연결 덩어리 안에서 직선거리 min_m~max_m 인 출발·도착 쌍을 n 개 뽑는다.
    seed 를 고정하면 언제 돌려도 같은 쌍이 나온다 (재현 가능)."""
    comp = max(nx.connected_components(G), key=len)
    nodes = sorted(comp)
    rng = random.Random(seed)
    pairs, seen = [], set()
    for _ in range(n * 200):
        if len(pairs) == n:
            break
        a, b = rng.sample(nodes, 2)
        if (a, b) in seen:
            continue
        seen.add((a, b))
        d = math.hypot(G.nodes[a]["x"] - G.nodes[b]["x"], G.nodes[a]["y"] - G.nodes[b]["y"])
        if min_m <= d <= max_m:
            pairs.append((a, b))
    if len(pairs) < n:
        raise ValueError(f"조건에 맞는 쌍을 {n}개 뽑지 못했습니다 ({len(pairs)}개)")
    return pairs


def danger_length(segment_ids: list[int], risk: dict[int, float], lengths: dict[int, float]) -> float:
    """경로 중 '위험' 등급(0.6) 이상 구간의 길이 합 (m)"""
    return sum(lengths[s] for s in segment_ids if risk.get(s, 0.0) >= DANGER)


def evaluate(
    G: nx.MultiGraph,
    pairs: list[tuple[int, int]],
    risk: dict[int, float],
    alphas: list[float],
    speed_mps: float,
) -> pd.DataFrame:
    """모든 쌍 × α 경로 탐색 → 행 하나에 (pair, alpha, 거리, 위험 노출 길이, 위험 구간 수, 최대 위험도)"""
    lengths = {d["segment_id"]: d["length"] for _, _, d in G.edges(data=True)}
    rows = []
    for i, (a, b) in enumerate(pairs):
        for alpha in alphas:
            r = find_route(G, a, b, alpha=alpha, risk=risk, speed_mps=speed_mps)
            if r is None:
                continue
            rows.append({
                "pair": i, "start": a, "end": b, "alpha": alpha,
                "distance_m": r.distance_m,
                "danger_m": danger_length(r.segment_ids, risk, lengths),
                "danger_segments": r.risk_segment_count,
                "max_risk": r.max_risk,
            })
    df = pd.DataFrame(rows)
    base = df[df["alpha"] == 0].set_index("pair")["distance_m"]
    df["detour"] = df["distance_m"] / df["pair"].map(base) - 1.0
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """α 별 요약: 평균·상위 10% 추가거리, 위험 노출 감소율, 위험 구간을 지나는 쌍 수"""
    base_danger = df.loc[df["alpha"] == 0, "danger_m"].sum()
    g = df.groupby("alpha")
    out = pd.DataFrame({
        "detour_mean": g["detour"].mean(),
        "detour_p90": g["detour"].quantile(0.9),
        "danger_m_total": g["danger_m"].sum(),
        "pairs_with_danger": g["danger_m"].apply(lambda s: int((s > 0).sum())),
    })
    out["danger_reduction"] = 1.0 - out["danger_m_total"] / base_danger if base_danger > 0 else 0.0
    return out.reset_index()


def choose_alpha(summary: pd.DataFrame, max_p90_detour: float = 0.20, converge_pp: float = 0.01) -> dict:
    """균형 모드 α 선택 (모듈 설명의 기준 ①②). 이유도 함께 돌려준다."""
    s = summary.sort_values("alpha").reset_index(drop=True)
    ok = s[(s["alpha"] > 0) & (s["detour_p90"] <= max_p90_detour)]
    if ok.empty:
        return {"alpha": None, "reason": f"상위 10% 추가거리 {max_p90_detour:.0%} 이내를 만족하는 α 가 없음"}
    for idx in ok.index:
        if idx + 1 >= len(s):
            break
        gain = s.loc[idx + 1, "danger_reduction"] - s.loc[idx, "danger_reduction"]
        if gain < converge_pp:
            a = s.loc[idx, "alpha"]
            reason = f"α={a:g} 에서 다음 α 로 올려도 감소율이 {gain * 100:.2f}%p 만 늘어 수렴"
            return {"alpha": a, "reason": reason}
    a = ok["alpha"].max()
    return {"alpha": a, "reason": f"수렴 전에 추가거리 기준에 걸려, 기준을 만족하는 가장 큰 α={a:g}"}


def plot(summary: pd.DataFrame, chosen: float | None, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax1 = plt.subplots(figsize=(7, 4))
    ax1.plot(summary["alpha"], summary["danger_reduction"] * 100, "o-", color="tab:red",
             label="danger reduction %")
    ax1.set_xlabel("alpha")
    ax1.set_ylabel("danger length reduction (%)", color="tab:red")
    ax2 = ax1.twinx()
    ax2.plot(summary["alpha"], summary["detour_p90"] * 100, "s--", color="tab:blue", label="detour p90 %")
    ax2.plot(summary["alpha"], summary["detour_mean"] * 100, "^:", color="tab:blue", label="detour mean %")
    ax2.axhline(20, color="gray", linewidth=0.8)
    ax2.set_ylabel("extra distance (%)", color="tab:blue")
    if chosen is not None:
        ax1.axvline(chosen, color="green", linewidth=1)
    fig.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=150)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="α 확정 실험")
    p.add_argument("--gpkg", default="data/segments.gpkg")
    p.add_argument("--rain", type=float, default=60.0, help="1시간 강우 mm")
    p.add_argument("--pairs", type=int, default=150)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--alphas", default=",".join(map(str, DEFAULT_ALPHAS)))
    p.add_argument("--out", default=str(RESULTS_DIR))
    args = p.parse_args(argv)

    import geopandas as gpd

    from routing.graph import load_graph_from_gpkg

    G = load_graph_from_gpkg(args.gpkg)
    seg = gpd.read_file(args.gpkg, layer="segments")
    factors = {int(r.segment_id): {"H": r.H, "U": r.U_flag} for r in seg.itertuples()}
    risk = compute_risks(factors, args.rain)
    alphas = [float(a) for a in args.alphas.split(",")]

    pairs = pick_pairs(G, args.pairs, args.seed)
    print(f"그래프: 노드 {G.number_of_nodes()}, 구간 {G.number_of_edges()}"
          f" / 강우 {args.rain:g}mm / 쌍 {len(pairs)}개")
    df = evaluate(G, pairs, risk, alphas, walk_speed(args.rain))
    summary = summarize(df)
    chosen = choose_alpha(summary)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "alpha_pairs.csv", index=False)
    summary.to_csv(out / "alpha_summary.csv", index=False)
    plot(summary, chosen["alpha"], out / "alpha_plot.png")

    view = summary.assign(
        detour_mean=(summary["detour_mean"] * 100).round(1),
        detour_p90=(summary["detour_p90"] * 100).round(1),
        danger_reduction=(summary["danger_reduction"] * 100).round(1),
        danger_m_total=summary["danger_m_total"].round(0),
    ).rename(columns={
        "detour_mean": "추가거리 평균%", "detour_p90": "추가거리 상위10%",
        "danger_m_total": "위험 노출 m", "pairs_with_danger": "위험 지나는 쌍",
        "danger_reduction": "노출 감소율%",
    })
    print(view.to_string(index=False))
    print(f"\n균형 모드 추천 α: {chosen['alpha']}  ({chosen['reason']})")
    print(f"결과 저장: {out}/alpha_pairs.csv, alpha_summary.csv, alpha_plot.png")


if __name__ == "__main__":
    main()
