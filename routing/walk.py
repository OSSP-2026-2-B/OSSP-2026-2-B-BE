"""강우별 보행 속도와 소요 시간 (기획안 3.4 보행 시간 표시, Shi et al., 2017).
속도는 모든 구간에 같이 적용되므로 경로 선택에는 영향을 주지 않고, 시간 표시에만 쓴다."""

import math

from app.config import Settings, settings


def walk_speed(rain_1h_mm: float, s: Settings = settings) -> float:
    """1시간 강우(mm)에 따른 보행 속도(m/s).
    1mm 미만 1.2 / 1mm 이상 20mm 미만 1.05 / 20mm 이상 0.9"""
    if rain_1h_mm >= s.heavy_rain_mm_per_h:
        return s.walk_speed_heavy_rain
    if rain_1h_mm >= s.light_rain_mm_per_h:
        return s.walk_speed_light_rain
    return s.walk_speed_no_rain


def duration_min(distance_m: float, rain_1h_mm: float, s: Settings = settings) -> int:
    """경로 길이(m)와 강우로 소요 시간(분)을 계산한다. 화면 표시용이라 올림해서 정수로 돌려준다."""
    seconds = distance_m / walk_speed(rain_1h_mm, s)
    return math.ceil(seconds / 60)