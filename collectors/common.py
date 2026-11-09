"""수집기 공통: 실제 호출(live) / 녹화된 샘플 응답(fixture) 모드.

- live   : 실제 API 호출. .env 에 키가 있어야 한다.
- fixture: collectors/fixtures/ 의 샘플 응답을 읽는다. 키 미발급 상태의 로컬 개발·테스트·CI 용.
           샘플의 관측 시각은 과거이므로, 가장 최근 관측이 "지금" 이 되도록 시각을 옮겨서 돌려준다
           (그렇지 않으면 지연 규칙에 걸려 항상 DATA_UNAVAILABLE 이 된다).

COLLECTOR_MODE 를 .env 에 live 로 적으면 실제 호출 모드가 된다 (기본 fixture).
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings, settings

KST = ZoneInfo("Asia/Seoul")
FIXTURE_DIR = Path(__file__).parent / "fixtures"
TIMEOUT_S = 10.0


class CollectorError(Exception):
    """API 호출·응답 해석 실패. 캐시는 이 오류를 받으면 대체 소스나 이전 값을 쓴다."""


def is_live(s: Settings = settings) -> bool:
    return s.collector_mode == "live"


def read_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def http_get(url: str, params: dict | None = None) -> str:
    """GET 후 본문 문자열. 네트워크·HTTP 오류는 CollectorError 로 바꾼다."""
    try:
        r = httpx.get(url, params=params, timeout=TIMEOUT_S)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise CollectorError(f"{url} 호출 실패: {e}") from e
    return r.text


def now_kst() -> datetime:
    return datetime.now(KST)
