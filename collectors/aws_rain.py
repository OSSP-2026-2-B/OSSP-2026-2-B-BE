"""방재 AWS 15분 강우 (기상청 API허브 nph-aws2_min, 매분 자료의 RN-15m 열).

침수예보 강우 조건 O_H(15분 20mm 이상) 판단에만 쓴다. 실패하면 None 으로 두고
위험도는 1시간 강우만으로 계산한다 (O_H 의 1시간 55mm 조건은 그대로 동작).

※ 키 발급 후 실제 응답의 열 이름·지점 번호를 확인할 것 (AWS_STATIONS, 기본 400 강남·401 서초).
"""

from dataclasses import dataclass
from datetime import datetime

from app.config import Settings, settings
from collectors.common import KST, CollectorError, http_get, is_live, now_kst, read_fixture

URL = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-aws2_min"
FIXTURE = "aws_min.txt"
COLUMN = "RN-15m"


@dataclass(frozen=True)
class AwsRain:
    rain_15m_mm: float
    observed_at: datetime


def parse(text: str, stations: list[str]) -> AwsRain:
    """주석(#) 줄의 열 이름에서 RN-15m 위치를 찾고, 대상 지점 중 가장 큰 값을 쓴다."""
    header: list[str] | None = None
    best: tuple[float, datetime] | None = None
    for line in text.splitlines():
        if line.startswith("#"):
            cols = line.lstrip("#").split()
            if COLUMN in cols:
                header = cols
            continue
        parts = line.split()
        if not parts or header is None or len(parts) < len(header):
            continue
        row = dict(zip(header, parts, strict=False))
        if row.get("STN") not in stations:
            continue
        try:
            v = float(row[COLUMN])
            t = datetime.strptime(row["YYMMDDHHMI"], "%Y%m%d%H%M").replace(tzinfo=KST)
        except (KeyError, ValueError):
            continue
        if v < 0:   # -99 등 결측
            continue
        if best is None or v > best[0]:
            best = (v, t)
    if header is None:
        raise CollectorError(f"AWS 응답에서 {COLUMN} 열을 찾지 못했습니다: {text[:200]}")
    if best is None:
        raise CollectorError(f"AWS 지점 {stations} 의 관측값이 없습니다")
    return AwsRain(best[0], best[1])


def fetch(s: Settings = settings, now: datetime | None = None) -> AwsRain:
    stations = s.aws_station_list
    if is_live(s):
        if not s.kma_apihub_key:
            raise CollectorError("KMA_APIHUB_KEY 가 없습니다")
        params = {"stn": 0, "disp": 0, "help": 1, "authKey": s.kma_apihub_key}
        return parse(http_get(URL, params), stations)

    rain = parse(read_fixture(FIXTURE), stations)
    return AwsRain(rain.rain_15m_mm, (now or now_kst()).replace(second=0, microsecond=0))
