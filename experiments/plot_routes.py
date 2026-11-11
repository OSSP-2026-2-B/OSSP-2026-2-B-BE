"""경로 비교 지도: 같은 출발·도착지에서 α 별 경로를 위험 등급 지도 위에 그린다 (팀 논의·발표용).

α 실험 결과(experiments/results/alpha_pairs.csv)에서 '균형 α 로 위험 노출이 가장 많이 줄면서
추가거리는 20% 이내'인 대표 쌍을 골라 그린다.

실행 (레포 최상위 폴더에서, α 실험을 먼저 돌린 뒤)
    python -m experiments.plot_routes
결과: experiments/results/route_compare.png
"""

import argparse
from pathlib import Path

import pandas as pd

from risk.model import compute_risks
from routing.astar import find_route
from routing.cost import risk_grade
from routing.walk import walk_speed

GRADE_COLORS = {"안전": "#d9d9d9", "주의": "#fdae6b", "위험": "#e6550d", "고위험": "#7f0000"}
ROUTE_STYLES = [("black", "-"), ("tab:blue", "-"), ("tab:green", "--"), ("tab:purple", ":")]


def choose_examples(
    df: pd.DataFrame, n: int = 3, balanced: float = 2.0, max_detour: float = 0.20
) -> list[int]:
    """균형 α 에서 위험 노출이 가장 많이 줄어든 쌍 n 개 (추가거리 max_detour 이내만)"""
    base = df[df["alpha"] == 0].set_index("pair")["danger_m"]
    bal = df[df["alpha"] == balanced].set_index("pair")
    gain = (base - bal["danger_m"])[bal["detour"] <= max_detour]
    return [int(p) for p in gain[gain > 0].sort_values(ascending=False).index[:n]]


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="경로 비교 지도")
    p.add_argument("--gpkg", default="data/segments.gpkg")
    p.add_argument("--pairs-csv", default="experiments/results/alpha_pairs.csv")
    p.add_argument("--rain", type=float, default=60.0)
    p.add_argument("--alphas", default="0,2,10", help="그릴 α (첫 값이 최단)")
    p.add_argument("--balanced", type=float, default=2.0, help="대표 쌍을 고를 때 기준 α")
    p.add_argument("--n", type=int, default=3, help="그릴 쌍 수")
    p.add_argument("--out", default="experiments/results/route_compare.png")
    args = p.parse_args(argv)

    import geopandas as gpd
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from routing.graph import load_graph_from_gpkg

    G = load_graph_from_gpkg(args.gpkg)
    seg = gpd.read_file(args.gpkg, layer="segments")
    factors = {int(r.segment_id): {"H": r.H, "U": r.U_flag} for r in seg.itertuples()}
    risk = compute_risks(factors, args.rain)
    seg["grade"] = [risk_grade(risk.get(int(s), 0.0)) for s in seg["segment_id"]]
    geom = seg.set_index("segment_id").geometry
    alphas = [float(a) for a in args.alphas.split(",")]

    df = pd.read_csv(args.pairs_csv)
    picks = choose_examples(df, args.n, args.balanced)
    if not picks:
        raise SystemExit("조건에 맞는 대표 쌍이 없습니다. --balanced 값이나 α 실험 결과를 확인하세요.")
    ends = df.drop_duplicates("pair").set_index("pair")[["start", "end"]]

    fig, axes = plt.subplots(1, len(picks), figsize=(6 * len(picks), 6.5), squeeze=False)
    for ax, pair in zip(axes[0], picks, strict=True):
        a, b = int(ends.loc[pair, "start"]), int(ends.loc[pair, "end"])
        speed = walk_speed(args.rain)
        routes = {al: find_route(G, a, b, alpha=al, risk=risk, speed_mps=speed) for al in alphas}
        lines = {
            al: gpd.GeoSeries([geom[s] for s in r.segment_ids], crs=seg.crs)
            for al, r in routes.items() if r
        }

        # 화면 범위: 모든 경로를 감싸는 사각형 + 150m 여백
        xmin, ymin, xmax, ymax = pd.concat(lines.values()).total_bounds
        pad = 150
        for grade, color in GRADE_COLORS.items():
            part = seg[seg["grade"] == grade]
            if part.empty:
                continue
            part.plot(ax=ax, color=color, linewidth=0.8 if grade == "안전" else 1.6, zorder=1)
        base_len = routes[alphas[0]].distance_m
        for (al, ln), (color, style) in zip(lines.items(), ROUTE_STYLES, strict=False):
            r = routes[al]
            row = df[(df["pair"] == pair) & (df["alpha"] == al)]
            danger = float(row["danger_m"].iloc[0]) if len(row) else float("nan")
            extra = (r.distance_m / base_len - 1) * 100
            label = f"a={al:g}: {r.distance_m:.0f} m (+{extra:.0f}%), danger {danger:.0f} m"
            ln.plot(ax=ax, color=color, linestyle=style, linewidth=3.2 - 0.6 * alphas.index(al), zorder=3,
                    label=label)
        for node, marker in ((a, "o"), (b, "*")):
            ax.scatter(G.nodes[node]["x"], G.nodes[node]["y"], s=160, marker=marker, color="white",
                       edgecolor="black", zorder=4)
        ax.set_xlim(xmin - pad, xmax + pad)
        ax.set_ylim(ymin - pad, ymax + pad)
        ax.set_aspect("equal")
        ax.set_axis_off()
        ax.set_title(f"pair {pair}  (o start, * end)", fontsize=10)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.18), fontsize=8, frameon=False)

    fig.suptitle(f"Route comparison at {args.rain:g} mm/h  |  grey safe, orange caution, red danger, "
                 f"dark red severe", fontsize=11)
    fig.tight_layout()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"대표 쌍: {picks}")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
