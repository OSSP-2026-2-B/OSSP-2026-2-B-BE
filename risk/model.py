"""구간별 침수 위험도 계산 (기획안 3.3).

    Risk(e,t) = 1 - Π_k (1 - s_k · X_k(e) · g_k(t))
    g_k(t)    = max( clip((R(t) - a_k) / (b_k - a_k), 0, 1),  O_k(t) )

- X_k(e): 구간 e 의 정적 요인 값 (0~1). H 침수이력, U 지하, L 저지대, W 하천 인접.
- g_k(t): 지금 비가 그 요인을 얼마나 "깨우는지" (0~1). 강우가 a_k 이하면 0, b_k 이상이면 1,
  그 사이는 직선으로 증가. O_k(t)=1 이면 (예: 침수예보 강우 조건 충족) 강우와 관계없이 1.
- s_k: 요인이 완전히 깨어났을 때의 위험 강도.
- 1 - Π(1 - p_k): 요인들이 서로 독립인 위험이라고 보고 "하나라도 터질 확률" 처럼 합친다.
  그래서 합쳐도 1 을 넘지 않고, 요인이 하나 늘면 위험도는 줄지 않는다.

이 모듈은 강우 값을 인자로 받는 순수 함수만 둔다 (수집기·캐시 구조와 무관하게 재사용).
"""

from collections.abc import Mapping
from dataclasses import dataclass

from app.config import Settings, settings

FACTORS = ("H", "U", "L", "W")


@dataclass(frozen=True)
class FactorParam:
    s: float  # 강도
    a: float  # 위험이 생기기 시작하는 1시간 강우 (mm)
    b: float  # 위험이 최대가 되는 1시간 강우 (mm)


def factor_params(s: Settings = settings) -> dict[str, FactorParam]:
    """config 값 → 요인별 파라미터"""
    return {
        "H": FactorParam(s.risk_h_s, s.risk_h_a, s.risk_h_b),
        "U": FactorParam(s.risk_u_s, s.risk_u_a, s.risk_u_b),
        "L": FactorParam(s.risk_l_s, s.risk_l_a, s.risk_l_b),
        "W": FactorParam(s.risk_w_s, s.risk_w_a, s.risk_w_b),
    }


def clip01(x: float) -> float:
    return min(1.0, max(0.0, x))


def activation(rain_1h_mm: float, p: FactorParam, override: float = 0.0) -> float:
    """g_k(t). override 는 O_k(t) (0 또는 1)."""
    ramp = clip01((rain_1h_mm - p.a) / (p.b - p.a))
    return max(ramp, clip01(override))


def flood_forecast_override(
    rain_15m_mm: float | None, rain_1h_mm: float, s: Settings = settings
) -> float:
    """O_H(t): 침수예보 강우 조건 (15분 20mm 이상 또는 1시간 55mm 이상) 이면 1."""
    if rain_1h_mm >= s.o_h_1h_mm:
        return 1.0
    if rain_15m_mm is not None and rain_15m_mm >= s.o_h_15m_mm:
        return 1.0
    return 0.0


def activations(
    rain_1h_mm: float,
    rain_15m_mm: float | None = None,
    s: Settings = settings,
) -> dict[str, float]:
    """현재 강우로 모든 요인의 g_k(t) 를 한 번에 계산 (구간마다 다시 계산하지 않도록)."""
    params = factor_params(s)
    overrides = {"H": flood_forecast_override(rain_15m_mm, rain_1h_mm, s)}
    return {k: activation(rain_1h_mm, params[k], overrides.get(k, 0.0)) for k in FACTORS}


def combine(factors: Mapping[str, float], g: Mapping[str, float], s: Settings = settings) -> float:
    """Risk = 1 - Π(1 - s_k·X_k·g_k). factors 에 없는 요인은 0 으로 본다."""
    params = factor_params(s)
    survive = 1.0
    for k in FACTORS:
        x = clip01(float(factors.get(k, 0.0) or 0.0))
        survive *= 1.0 - params[k].s * x * g[k]
    return clip01(1.0 - survive)


def segment_risk(
    factors: Mapping[str, float],
    rain_1h_mm: float,
    rain_15m_mm: float | None = None,
    s: Settings = settings,
) -> float:
    """구간 하나의 위험도. 예) segment_risk({"H": 1}, 40) → 0.425"""
    return combine(factors, activations(rain_1h_mm, rain_15m_mm, s), s)


def compute_risks(
    segments: Mapping[int, Mapping[str, float]],
    rain_1h_mm: float,
    rain_15m_mm: float | None = None,
    s: Settings = settings,
) -> dict[int, float]:
    """segment_id → {H, U, ...} 를 받아 segment_id → 위험도. 0 인 구간은 빼서 돌려준다
    (routing.astar.find_route 는 없는 구간을 0 으로 본다)."""
    g = activations(rain_1h_mm, rain_15m_mm, s)
    out = {}
    for seg_id, f in segments.items():
        r = combine(f, g, s)
        if r > 0:
            out[int(seg_id)] = r
    return out


def reasons(
    H: float = 0.0,
    H_count: int = 0,
    H_years: str = "",
    U: int = 0,
    U_reason: str = "",
) -> list[str]:
    """화면의 "왜 이 길을 피하나요?" 이유 태그.
    예) ['침수흔적 3회 (2010·2011·2022)', '지하보도']"""
    out = []
    if H and H > 0 and H_count:
        years = [y for y in str(H_years or "").split(",") if y]
        tag = f"침수흔적 {int(H_count)}회"
        if years:
            tag += f" ({'·'.join(years)})"
        out.append(tag)
    if U:
        out.append(U_reason or "지하 구간")
    return out
