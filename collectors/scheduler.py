"""위험도 캐시 갱신 스케줄러

update_risk_cache() 를 부르고, 돌려받은 분(평소 30 / 강우 시 10) 뒤에 다시 실행되도록 예약한다.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from collectors.risk_stub import update_risk_cache

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
JOB_ID = "update_risk_cache"

scheduler = BackgroundScheduler(timezone=KST)


def _schedule(sched: BackgroundScheduler, run_at: datetime) -> None:
    """run_at 시각에 run_update 가 한 번 실행되도록 예약 (같은 id 면 덮어씀)"""
    sched.add_job(
        run_update,
        "date",
        run_date=run_at,
        id=JOB_ID,
        replace_existing=True,
        kwargs={"sched": sched},
    )


def run_update(sched: BackgroundScheduler = scheduler, now: datetime | None = None) -> int:
    """위험도 캐시를 갱신하고 다음 실행을 예약한다. 다음 실행까지의 분을 돌려준다."""
    normal = settings.collect_interval_normal_min
    allowed = {normal, settings.collect_interval_rain_min}

    try:
        minutes = update_risk_cache()
    except Exception:
        logger.exception("위험도 갱신 실패 → 평소 주기로 다시 시도")
        minutes = normal

    if minutes not in allowed:
        logger.warning("예상 밖 주기 %s분 → %s분으로 대체", minutes, normal)
        minutes = normal

    next_run = (now or datetime.now(KST)) + timedelta(minutes=minutes)
    _schedule(sched, next_run)
    logger.info("다음 위험도 갱신: %s (%s분 뒤)", next_run.strftime("%H:%M"), minutes)
    return minutes


def start_scheduler(sched: BackgroundScheduler = scheduler) -> None:
    """서버가 켜질 때: 바로 한 번 실행하도록 예약하고 스케줄러 시작"""
    if sched.running:
        return
    _schedule(sched, datetime.now(KST))
    sched.start()


def stop_scheduler(sched: BackgroundScheduler = scheduler) -> None:
    """서버가 꺼질 때: 스케줄러 정지"""
    if sched.running:
        sched.shutdown(wait=False)