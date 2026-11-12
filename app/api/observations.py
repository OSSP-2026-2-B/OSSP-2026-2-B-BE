"""GET /api/observations: 화면 위쪽 강우 상태 바"""

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from app.config import Settings, settings
from collectors import kma_warning
from collectors.common import CollectorError, now_kst
from risk.exclusion import flood_warning_met
from risk.model import flood_forecast_override

logger = logging.getLogger(__name__)

LEVEL_ORDER = {"주의보": 1, "경보": 2}


class WarningCache:
    """특보는 자주 바뀌지 않아 10분 동안 재사용한다. 수집에 실패하면 직전 값을 유지한다."""

    def __init__(self, fetch: Callable = kma_warning.fetch, ttl_min: int = 10):
        self.fetch = fetch
        self.ttl = timedelta(minutes=ttl_min)
        self._at: datetime | None = None
        self._items: list = []
        self._lock = threading.Lock()

    def get(self, now: datetime | None = None) -> list:
        now = now or now_kst()
        with self._lock:
            if self._at is None or now - self._at >= self.ttl:
                try:
                    self._items = self.fetch()
                except CollectorError as e:
                    logger.warning("기상특보 수집 실패, 직전 값 사용: %s", e)
                self._at = now
            return self._items


warning_cache = WarningCache()


def build_observations(snapshot: dict, warnings: list, s: Settings = settings) -> dict:
    rain = snapshot["rain"]
    r1h, r3h, r15m = rain["rain_1h_mm"], rain["rain_3h_mm"], rain["rain_15m_mm"]

    badges = []
    if warnings:
        w = max(warnings, key=lambda x: LEVEL_ORDER[x.level])  # 경보가 있으면 경보만
        badges.append({
            "kind": "confirmed", "label": w.label, "source": "기상청 기상특보",
            "issued_at": w.issued_at.isoformat(),
        })

    # 경보 기준을 충족하면 예보 기준은 당연히 충족하므로 더 높은 것 하나만
    if flood_warning_met(r1h, r3h, s):
        badges.append(_estimated("침수경보 기준 충족", "1시간·3시간 강우"))
    elif flood_forecast_override(r15m, r1h, s):
        by_15m = r15m is not None and r15m >= s.o_h_15m_mm
        badges.append(_estimated("침수예보 기준 충족", "방재 AWS 15분 강우" if by_15m else "1시간 강우"))

    return {
        "updated_at": rain["updated_at"],
        "delayed": rain["delayed"],
        "rain_1h_mm": r1h,
        "rain_3h_mm": r3h,
        "rain_15m_mm": r15m,
        "forecast_1h_mm": None,  # 예보 수집기 없음
        "badges": badges,
    }


def _estimated(label: str, source: str) -> dict:
    return {"kind": "estimated", "label": label, "source": source, "issued_at": None}