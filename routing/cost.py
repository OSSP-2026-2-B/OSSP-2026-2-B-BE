"""경로 비용과 위험 등급 (기획안 3.3, 3.4).

Cost(e,t) = WalkTime(e) × (1 + α · Risk(e,t)²)
위험한 구간을 실제보다 "더 긴 길"처럼 만들어, 경로 탐색이 자연스럽게 피하게 한다.
"""

from app.config import Settings, settings


def segment_cost(length_m: float, risk: float, alpha: float, speed_mps: float) -> float:
    """구간 하나의 비용(초). 위험도가 0이면 보행 시간과 같다."""
    if not 0.0 <= risk <= 1.0:
        raise ValueError(f"risk 는 0~1 이어야 합니다: {risk}")
    walk_time = length_m / speed_mps
    return walk_time * (1.0 + alpha * risk * risk)


def risk_grade(risk: float, s: Settings = settings) -> str:
    """위험도 → 등급. 0.3 미만 안전 / 0.6 미만 주의 / 0.8 미만 위험 / 0.8 이상 고위험"""
    if risk >= s.grade_severe:
        return "고위험"
    if risk >= s.grade_danger:
        return "위험"
    if risk >= s.grade_caution:
        return "주의"
    return "안전"