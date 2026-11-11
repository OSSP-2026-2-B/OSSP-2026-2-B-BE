"""위험도 캐시 (팀 논의 3번 인터페이스).

    update_risk_cache() -> int   스케줄러가 호출. 수집 → 위험도·통행 제외 계산 → 캐시 교체.
                                 다음 실행까지 기다릴 분(평소 30 / 강우 시 10)을 돌려준다.
    get_snapshot() -> dict|None  API 가 읽는 최신 결과. 첫 갱신 전에는 None.

스냅샷 모양:
    {
      "risk":     {segment_id: 위험도},      # 0 인 구간은 생략 → find_route(risk=...) 에 그대로
      "excluded": {segment_id, ...},         # 통행 제외 → find_route(excluded=...) 에 그대로
      "reasons":  {segment_id: [이유 태그]},  # 위험도 > 0 또는 통행 제외 구간만
      "rain": {
        "rain_1h_mm", "rain_3h_mm", "rain_15m_mm",   # 3h·15m 는 모를 때 None
        "updated_at",       # 사용한 강우 관측 시각 (ISO 8601, KST)
        "delayed",          # 서울시 10분 관측 대신 초단기실황 등을 쓴 경우 True
        "source",           # "seoul" / "kma" / "previous" / "none"
        "last_normal_at",   # 마지막 서울시 10분 정상 관측 시각 (없으면 None)
      },
      "control_points": [{"lng", "lat", "kind", "message"}],   # 규칙 1 통제 지점
      "computed_at": 계산 시각,
    }

강우 지연 규칙 (합의됨):
- 서울시 10분 관측이 20분 넘게 없으면 기상청 초단기실황 RN1 으로 대체하고 delayed = True.
- 마지막 정상값(서울시 10분 관측)이 60분을 넘으면 is_data_unavailable() 이 True
  → /api/route 는 503 DATA_UNAVAILABLE.
"""

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import Settings, settings
from collectors import aws_rain, its_control, kma_ultra, seoul_rain
from collectors.common import CollectorError, now_kst
from risk.exclusion import rule1_excluded, rule2_excluded
from risk.model import compute_risks, reasons

logger = logging.getLogger(__name__)

RULE2_REASON = "침수경보 기준 충족 (지하 구간 통행 제외)"


@dataclass
class StaticData:
    """data/segments.gpkg 에서 한 번만 읽는 정적 요인"""
    factors: dict[int, dict[str, float]]            # segment_id → {"H", "U"}
    info: dict[int, dict]                           # segment_id → reasons() 인자
    gdf: object = None                              # segment_id·geometry GeoDataFrame (규칙 1)


def load_static(path: str) -> StaticData:
    import geopandas as gpd

    seg = gpd.read_file(path, layer="segments")
    u_col = "U_flag" if "U_flag" in seg.columns else "U"
    factors, info = {}, {}
    for r in seg.itertuples(index=False):
        sid = int(r.segment_id)
        H = float(getattr(r, "H", 0) or 0)
        U = int(getattr(r, u_col, 0) or 0)
        factors[sid] = {"H": H, "U": U}
        info[sid] = {
            "H": H, "H_count": int(getattr(r, "H_count", 0) or 0),
            "H_years": str(getattr(r, "H_years", "") or ""),
            "U": U, "U_reason": str(getattr(r, "U_reason", "") or "").strip(),
        }
    return StaticData(factors, info, seg[["segment_id", "geometry"]])


@dataclass
class Collectors:
    """테스트에서 바꿔 끼울 수 있도록 수집 함수를 묶어 둔다"""
    seoul: Callable = seoul_rain.fetch
    kma: Callable = kma_ultra.fetch
    aws: Callable = aws_rain.fetch
    its: Callable = its_control.fetch


class RiskCache:
    def __init__(
        self,
        s: Settings = settings,
        static_loader: Callable[[], StaticData] | None = None,
        collectors: Collectors | None = None,
    ):
        self.s = s
        self.static_loader = static_loader
        self.collectors = collectors or Collectors()
        self._lock = threading.Lock()
        self._snapshot: dict | None = None
        self._static: StaticData | None = None
        self._last_normal: seoul_rain.SeoulRain | None = None
        self._last_controls: list = []

    # ---------- 공개 함수 ----------
    def get_snapshot(self) -> dict | None:
        """최신 스냅샷 (읽기 전용으로 쓸 것). 첫 갱신 전에는 None."""
        with self._lock:
            return self._snapshot

    def update(self, now: datetime | None = None) -> int:
        now = now or now_kst()
        static = self._get_static()
        rain = self._resolve_rain(now)
        controls = self._resolve_controls()

        risk = compute_risks(static.factors, rain["rain_1h_mm"], rain["rain_15m_mm"], self.s)
        ex1_by_kind = {   # 이유 태그에 통제 종류를 적기 위해 종류별로 계산
            kind: rule1_excluded(static.gdf, [(c.x, c.y) for c in controls if c.kind == kind], self.s)
            for kind in {c.kind for c in controls}
        }
        ex1 = set().union(*ex1_by_kind.values())
        ex2 = rule2_excluded(static.factors, rain["rain_1h_mm"], rain["rain_3h_mm"], self.s)
        snapshot = {
            "risk": risk,
            "excluded": ex1 | ex2,
            "reasons": self._reasons(static, risk, ex1_by_kind, ex2),
            "rain": {**rain, "updated_at": _iso(rain["updated_at"]),
                     "last_normal_at": _iso(rain["last_normal_at"])},
            "control_points": [
                {"lng": c.lng, "lat": c.lat, "kind": c.kind, "message": c.message} for c in controls
            ],
            "computed_at": _iso(now),
        }
        with self._lock:
            self._snapshot = snapshot
        logger.info(
            "위험도 갱신: 1h %.1fmm (%s%s) · 위험도>0 %d · 통행 제외 %d (규칙1 %d / 규칙2 %d)",
            rain["rain_1h_mm"], rain["source"], ", 지연" if rain["delayed"] else "",
            len(risk), len(ex1 | ex2), len(ex1), len(ex2),
        )
        return self._next_interval(rain)

    # ---------- 내부 ----------
    def _get_static(self) -> StaticData:
        if self._static is None:
            loader = self.static_loader or (lambda: load_static(self.s.segments_path))
            self._static = loader()
        return self._static

    def _resolve_rain(self, now: datetime) -> dict:
        """서울시 10분 → (20분 넘게 없으면) 초단기실황 → 이전 값 순으로 강우를 정한다."""
        try:
            seoul = self.collectors.seoul(self.s, now=now)
            if self._last_normal is None or seoul.observed_at >= self._last_normal.observed_at:
                self._last_normal = seoul
        except CollectorError as e:
            logger.warning("서울시 강우 수집 실패: %s", e)

        rain_15m = self._resolve_15m(now)
        last = self._last_normal
        last_at = last.observed_at if last else None
        limit = timedelta(minutes=self.s.rain_delay_fallback_min)
        if last is not None and now - last.observed_at <= limit:
            return _rain(last.rain_1h_mm, last.rain_3h_mm, rain_15m, last_at, False, "seoul", last_at)

        try:
            kma = self.collectors.kma(self.s, now=now)
            # 3시간 강우는 초단기실황에 없어 모름 → 규칙 2 는 1시간 72mm 조건만 판단
            return _rain(kma.rain_1h_mm, None, rain_15m, kma.observed_at, True, "kma", last_at)
        except CollectorError as e:
            logger.warning("초단기실황 대체 수집도 실패: %s", e)

        prev = self._snapshot["rain"] if self._snapshot else None
        if prev is not None:
            return _rain(prev["rain_1h_mm"], prev["rain_3h_mm"], rain_15m,
                         datetime.fromisoformat(prev["updated_at"]), True, "previous", last_at)
        return _rain(0.0, None, rain_15m, None, True, "none", last_at)

    def _resolve_15m(self, now: datetime) -> float | None:
        try:
            aws = self.collectors.aws(self.s, now=now)
        except CollectorError as e:
            logger.warning("방재 AWS 15분 강우 수집 실패: %s", e)
            return None
        if now - aws.observed_at > timedelta(minutes=self.s.rain_delay_fallback_min):
            logger.warning("방재 AWS 관측이 오래됨 (%s) → 15분 강우 미사용", aws.observed_at)
            return None
        return aws.rain_15m_mm

    def _resolve_controls(self) -> list:
        try:
            self._last_controls = self.collectors.its(self.s)
        except CollectorError as e:
            # 통제 정보는 놓치면 위험하므로 실패 시 직전 목록을 유지
            logger.warning("ITS 통제 정보 수집 실패 → 직전 %d건 유지: %s", len(self._last_controls), e)
        return self._last_controls

    @staticmethod
    def _reasons(static, risk, ex1_by_kind, ex2) -> dict[int, list[str]]:
        out = {}
        for sid in set(risk).union(ex2, *ex1_by_kind.values()):
            tags = reasons(**static.info.get(sid, {}))
            tags += [f"도로 통제 ({kind})" for kind, segs in sorted(ex1_by_kind.items()) if sid in segs]
            if sid in ex2:
                tags.append(RULE2_REASON)
            if tags:
                out[sid] = tags
        return out

    def _next_interval(self, rain: dict) -> int:
        rate = max(rain["rain_1h_mm"], (rain["rain_15m_mm"] or 0.0) * 4)
        if rate >= self.s.rain_detect_mm_per_h:
            return self.s.collect_interval_rain_min
        return self.s.collect_interval_normal_min


def _rain(r1h, r3h, r15m, updated_at, delayed, source, last_normal_at) -> dict:
    return {
        "rain_1h_mm": float(r1h), "rain_3h_mm": r3h, "rain_15m_mm": r15m,
        "updated_at": updated_at, "delayed": delayed, "source": source,
        "last_normal_at": last_normal_at,
    }


def _iso(t: datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") if t else None


def is_data_unavailable(
    snapshot: Mapping | None, now: datetime | None = None, s: Settings = settings
) -> bool:
    """마지막 정상 강우값이 60분을 넘었거나 스냅샷이 없으면 True → /api/route 503 DATA_UNAVAILABLE"""
    if not snapshot:
        return True
    last = snapshot["rain"].get("last_normal_at")
    if last is None:
        return True
    return (now or now_kst()) - datetime.fromisoformat(last) > timedelta(minutes=s.rain_stale_min)


# ---------- 모듈 단위 함수 (스케줄러·API 가 쓰는 약속된 인터페이스) ----------
_cache = RiskCache()


def update_risk_cache() -> int:
    return _cache.update()


def get_snapshot() -> dict | None:
    return _cache.get_snapshot()
