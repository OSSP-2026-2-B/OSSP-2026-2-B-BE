"""기상청 기상특보 현황 → /api/observations 의 [확인] 배지

시범 지역 특보 구역(서울동남권)의 호우 주의보·경보만 고른다.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from app.config import Settings, settings
from collectors.common import KST, CollectorError, http_get, is_live, read_fixture

URL = "https://apihub.kma.go.kr/api/typ01/url/wrn_now_data.php"
FIXTURE = "kma_warning.json"
LEVELS = ("주의보", "경보")  # 예비특보는 제외


@dataclass(frozen=True)
class WeatherWarning:
    kind: str           # 호우
    level: str          # 주의보 / 경보
    region: str         # 서울동남권
    issued_at: datetime

    @property
    def label(self) -> str:
        return f"{self.kind}{self.level}"


def parse(text: str, s: Settings = settings) -> list[WeatherWarning]:
    try:
        items = json.loads(text)
    except ValueError as e:
        raise CollectorError(f"기상특보 응답 해석 실패: {text[:200]}") from e
    if not isinstance(items, list):
        raise CollectorError(f"기상특보 응답 형식이 다릅니다: {text[:200]}")

    out = []
    for it in items:
        # 응답 값 뒤에 공백이 붙어 온다 ("호우  ")
        v = {k: str(it.get(k, "")).strip() for k in ("REG_ID", "REG_KO", "WRN", "LVL", "CMD", "TM_EF")}
        if v["REG_ID"] != s.kma_warning_region or v["WRN"] != "호우":
            continue
        if v["LVL"] not in LEVELS or v["CMD"] == "해제":
            continue
        try:
            t = datetime.strptime(v["TM_EF"], "%Y%m%d%H%M").replace(tzinfo=KST)
        except ValueError as e:
            raise CollectorError(f"특보 발효 시각 해석 실패: {v['TM_EF']}") from e
        out.append(WeatherWarning(v["WRN"], v["LVL"], v["REG_KO"], t))
    return out


def fetch(s: Settings = settings) -> list[WeatherWarning]:
    if is_live(s):
        if not s.kma_apihub_key:
            raise CollectorError("KMA_APIHUB_KEY 가 없습니다")
        params = {"fe": "f", "tm": "", "disp": 1, "help": 1, "authKey": s.kma_apihub_key}
        return parse(http_get(URL, params), s)
    return parse(read_fixture(FIXTURE), s)