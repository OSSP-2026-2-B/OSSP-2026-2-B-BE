"""서울시 10분 강우량 (서울 열린데이터광장 ListRainfallService) → 1시간·3시간 강우.

위험도 계산의 기본 강우 소스. 강우량계마다 10분 단위 강우(RAINFALL10)와 수신 시각이 온다.
- 설정한 강우량계(기본 서초구청·강남구청) 중 10분 구간마다 가장 큰 값을 쓴다 (보수적).
- 1시간 = 가장 최근 관측 시각부터 60분 안의 10분 값 합, 3시간 = 180분 안의 합.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import Settings, settings
from collectors.common import KST, CollectorError, http_get, is_live, now_kst, read_fixture

URL = "http://openapi.seoul.go.kr:8088/{key}/json/ListRainfallService/1/{rows}/{gu}"
FIXTURE = "seoul_rain.json"
ROWS = 300  # 구별 최근 기록 수. 강우량계 수 × 18(3시간) 보다 넉넉하게


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
    """10분 구간마다 대상 강우량계의 최댓값 → 1시간·3시간 합"""
    per_time: dict[datetime, float] = {}
    for r in rows:
        if r.get("RAINGAUGE_NAME", "").strip() not in gauges:
            continue
        try:
            t = _parse_time(r["RECEIVE_TIME"])
            v = float(r["RAINFALL10"])
        except (KeyError, ValueError):
            continue
        if v < 0:          # 결측
            continue
        per_time[t] = max(per_time.get(t, 0.0), v)
    if not per_time:
        raise CollectorError(f"대상 강우량계 {gauges} 의 관측값이 없습니다")

    latest = max(per_time)

    def window_sum(minutes: int) -> float:
        start = latest - timedelta(minutes=minutes)
        return round(sum(v for t, v in per_time.items() if start < t <= latest), 1)

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
