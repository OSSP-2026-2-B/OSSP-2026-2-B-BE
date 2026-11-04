"""risk 모듈이 나오기 전까지 쓰는 임시 함수

약속한 인터페이스:
    update_risk_cache() -> int
    위험도 캐시를 갱신하고, 다음 실행까지 기다릴 분(30 또는 10)을 돌려준다.

risk 모듈이 올라오면 collectors/scheduler.py 의 import 를
    from risk.cache import update_risk_cache
로 바꾸고 이 파일은 지운다.
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def update_risk_cache() -> int:
    logger.info("[stub] 위험도 캐시 갱신 (아직 실제 계산 없음)")
    return settings.collect_interval_normal_min