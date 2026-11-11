"""서울시 10분 강우량 (서울 열린데이터광장 ListRainfallService) → 1시간·3시간 강우.

위험도 계산의 기본 강우 소스. 강우량계마다 10분 강우(RN_10M)와 자료 수집 시각(DATA_CLCT_TM)이 온다.
(필드 이름은 2026-11-11 실제 응답으로 확인: RF_CD·RF_NM·GU_CD·GU_NM·RN_10M·DATA_CLCT_TM)

- 강우량계마다 1시간 = 가장 최근 관측 시각부터 60분 안의 10분 값 합 (최근 6개), 3시간 = 180분 안의 합.
- 설정한 강우량계(기본 서초구청·강남구청) 중 합이 큰 값을 쓴다 (기획서 R(t): 두 관측소 중 큰 값).
- 강우량계마다 따로 합하므로 관측 시각이 1~2분 어긋나도 두 번 더해지지 않고,
  관측이 끊긴 강우량계는 최근 60분 값이 없어 자연히 빠진다.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import Settings, settings
from collectors.common import KST, CollectorError, http_get, is_live, now_kst, read_fixture

URL = "http://openapi.seoul.go.kr:8088/{key}/json/ListRainfallService/1/{rows}/{gu}"
FIXTURE = "seoul_rain.json"
ROWS = 300  # 구별 최근 기록 수. 강우량계 수 × 18(3시간) 보다 넉넉하게
NAME, VALUE, TIME = "RF_NM", "RN_10M", "DATA_CLCT_TM"  # 강우량계 이름, 10분 강우(mm), 수집 시각


@dataclass(frozen=True)
class SeoulRain:
    rain_1h_mm: float
    rain_3h_mm: float
    observed_at: datetime       # 가장 최근 10분 관측 시각 (KST)


def parse_rows(text: str) -> list[dict]:
    try:
        body = json.loads(text)["ListRainfallService"]
    except (ValueError, KeyError) as e:
        # 키 오류 등은 {"RESULT": {"CODE": "INFO-100", ...}} 만 온다
        raise CollectorError(f"서울시 강우 응답 해석 실패: {text[:200]}") from e
    code = body.get("RESULT", {}).get("CODE", "")
    if code not in ("INFO-000", ""):
        raise CollectorError(f"서울시 강우 API 오류 {code}: {body['RESULT'].get('MESSAGE')}")
    return body.get("row", [])


def _parse_time(s: str) -> datetime:
    return datetime.strptime(s.strip()[:16], "%Y-%m-%d %H:%M").replace(tzinfo=KST)


def aggregate(rows: list[dict], gauges: list[str]) -> SeoulRain:
    """강우량계마다 최근 1시간·3시간 합 → 대상 강우량계 중 큰 값"""
    obs: dict[str, dict[datetime, float]] = {}   # 강우량계 → {시각: 10분 강우}
    for r in rows:
        name = str(r.get(NAME) or "").strip()
        if name not in gauges:
            continue
        try:
            t = _parse_time(str(r[TIME]))
            v = float(r[VALUE])
        except (KeyError, ValueError, TypeError):   # 필드 없음, 빈 값, null
            continue
        if v < 0:          # 결측
            continue
        obs.setdefault(name, {})[t] = v
    if not obs:
        raise CollectorError(f"대상 강우량계 {gauges} 의 관측값이 없습니다")

    latest = max(max(per) for per in obs.values())

    def window_sum(minutes: int) -> float:
        start = latest - timedelta(minutes=minutes)
        sums = (sum(v for t, v in per.items() if start < t <= latest) for per in obs.values())
        return round(max(sums), 1)

    return SeoulRain(window_sum(60), window_sum(180), latest)


def fetch(s: Settings = settings, now: datetime | None = None) -> SeoulRain:
    gauges = s.seoul_rain_gauge_list
    if is_live(s):
        if not s.seoul_opendata_key:
            raise CollectorError("SEOUL_OPENDATA_KEY 가 없습니다")
        rows = []
        for gu in s.seoul_rain_gu_list:
            rows += parse_rows(http_get(URL.format(key=s.seoul_opendata_key, rows=ROWS, gu=gu)))
        return aggregate(rows, gauges)

    rain = aggregate(parse_rows(read_fixture(FIXTURE)), gauges)
    return SeoulRain(rain.rain_1h_mm, rain.rain_3h_mm, (now or now_kst()).replace(second=0, microsecond=0))
