"""스케줄러: update_risk_cache() 결과에 따라 다음 실행을 맞게 예약하는지 확인"""
import time
from datetime import datetime, timedelta

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

import collectors.scheduler as sch

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=sch.KST)


@pytest.fixture
def sched():
    # 켜지 않은 스케줄러: 예약은 받아 두지만 실제로 실행하지는 않음
    return BackgroundScheduler(timezone=sch.KST)


def next_run(sched):
    return sched.get_job(sch.JOB_ID).trigger.run_date


def test_normal_interval_30min(sched, monkeypatch):
    monkeypatch.setattr(sch, "update_risk_cache", lambda: 30)
    assert sch.run_update(sched, now=NOW) == 30
    assert next_run(sched) == NOW + timedelta(minutes=30)


def test_rain_interval_10min(sched, monkeypatch):
    # 비가 오면 update_risk_cache()가 10 을 돌려줌 → 10분 뒤 예약
    monkeypatch.setattr(sch, "update_risk_cache", lambda: 10)
    assert sch.run_update(sched, now=NOW) == 10
    assert next_run(sched) == NOW + timedelta(minutes=10)


def test_failure_retries_in_30min(sched, monkeypatch):
    # 계산 중 오류가 나도 스케줄러는 멈추지 않고 30분 뒤 다시 시도
    def broken():
        raise RuntimeError("기상청 API 응답 없음")

    monkeypatch.setattr(sch, "update_risk_cache", broken)
    assert sch.run_update(sched, now=NOW) == 30
    assert next_run(sched) == NOW + timedelta(minutes=30)


def test_unexpected_value_falls_back(sched, monkeypatch):
    # 0분 같은 이상한 값은 무한 반복을 막기 위해 30분으로 대체
    monkeypatch.setattr(sch, "update_risk_cache", lambda: 0)
    assert sch.run_update(sched, now=NOW) == 30


def test_start_and_stop(monkeypatch):
    monkeypatch.setattr(sch, "update_risk_cache", lambda: 30)
    s = BackgroundScheduler(timezone=sch.KST)
    sch.start_scheduler(s)
    assert s.running

    # 켜자마자 1회 실행 → 30분 뒤 예약이 생길 때까지 최대 3초 기다림
    deadline = time.time() + 3
    while time.time() < deadline:
        job = s.get_job(sch.JOB_ID)
        if job and job.next_run_time > datetime.now(sch.KST) + timedelta(minutes=20):
            break
        time.sleep(0.05)
    else:
        pytest.fail("첫 실행 후 다음 예약이 생기지 않음")

    sch.stop_scheduler(s)
    assert not s.running