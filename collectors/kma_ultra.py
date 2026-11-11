"""기상청 초단기실황 RN1 (공공데이터포털 VilageFcstInfoService_2.0/getUltraSrtNcst).

서울시 10분 강우가 20분 넘게 없을 때만 쓰는 대체 소스. 1시간 강우(RN1)만 있다.
정시 관측이 약 40분 뒤에 올라오므로, 40분 전이면 한 시간 앞 자료를 요청한다.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import Settings, settings
from collectors.common import KST, CollectorError, http_get, is_live, now_kst, read_fixture

URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
FIXTURE = "kma_ultra.json"
READY_MINUTE = 40


@dataclass(frozen=True)
class KmaRain:
    rain_1h_mm: float
    observed_at: datetime


def base_time(now: datetime) -> datetime:
    """요청할 관측 시각 (정시)"""
    t = now.astimezone(KST)
    if t.minute < READY_MINUTE:
        t -= timedelta(hours=1)
    return t.replace(minute=0, second=0, microsecond=0)


def parse_rn1(text: str) -> KmaRain:
    try:
        res = json.loads(text)["response"]
        code = res["header"]["resultCode"]
    except (ValueError, KeyError) as e:
        raise CollectorError(f"초단기실황 응답 해석 실패: {text[:200]}") from e
    if code != "00":
        raise CollectorError(f"초단기실황 API 오류 {code}: {res['header'].get('resultMsg')}")
    for it in res["body"]["items"]["item"]:
        if it.get("category") == "RN1":
            t = datetime.strptime(it["baseDate"] + it["baseTime"], "%Y%m%d%H%M").replace(tzinfo=KST)
            try:
                v = float(it["obsrValue"])
            except ValueError as e:
                raise CollectorError(f"RN1 값 해석 실패: {it['obsrValue']}") from e
            return KmaRain(max(v, 0.0), t)
    raise CollectorError("초단기실황 응답에 RN1 이 없습니다")


def fetch(s: Settings = settings, now: datetime | None = None) -> KmaRain:
    now = now or now_kst()
    if is_live(s):
        if not s.data_go_kr_key:
            raise CollectorError("DATA_GO_KR_KEY 가 없습니다")
        bt = base_time(now)
        params = {
            "serviceKey": s.data_go_kr_key, "pageNo": 1, "numOfRows": 10, "dataType": "JSON",
            "base_date": bt.strftime("%Y%m%d"), "base_time": bt.strftime("%H%M"),
            "nx": s.kma_grid_nx, "ny": s.kma_grid_ny,
        }
        return parse_rn1(http_get(URL, params))

    rain = parse_rn1(read_fixture(FIXTURE))
    return KmaRain(rain.rain_1h_mm, base_time(now))
