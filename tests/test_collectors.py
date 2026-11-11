"""수집기: 녹화된 샘플 응답(fixture)만으로 응답 해석·집계 규칙을 확인 (실제 API 호출 없음)"""

import json
from datetime import datetime

import pytest

from app.config import Settings
from collectors import aws_rain, its_control, kma_ultra, seoul_rain
from collectors.common import KST, CollectorError, read_fixture

S = Settings(_env_file=None)
LIVE = Settings(_env_file=None, collector_mode="live")
NOW = datetime(2026, 11, 9, 14, 23, tzinfo=KST)


# ---------- 서울시 10분 강우 ----------
def test_seoul_fixture_sums_max_of_target_gauges():
    rows = seoul_rain.parse_rows(read_fixture(seoul_rain.FIXTURE))
    rain = seoul_rain.aggregate(rows, ["서초구청", "강남구청"])
    # 10분마다 서초구청·강남구청 중 큰 값의 합. 대상 밖 '방배2동'(10분 20mm) 은 무시
    assert rain.rain_1h_mm == pytest.approx(49.5)
    assert rain.rain_3h_mm == pytest.approx(85.5)
    assert rain.observed_at == datetime(2026, 10, 28, 20, 0, tzinfo=KST)


def test_seoul_window_and_missing_values():
    rows = [
        {"RAINGAUGE_NAME": "A", "RAINFALL10": "5", "RECEIVE_TIME": "2026-10-28 20:00"},
        {"RAINGAUGE_NAME": "A", "RAINFALL10": "3", "RECEIVE_TIME": "2026-10-28 19:10"},  # 50분 전: 1h 포함
        {"RAINGAUGE_NAME": "A", "RAINFALL10": "7", "RECEIVE_TIME": "2026-10-28 19:00"},  # 60분 전: 제외
        {"RAINGAUGE_NAME": "A", "RAINFALL10": "-1", "RECEIVE_TIME": "2026-10-28 19:50"},  # 결측
        {"RAINGAUGE_NAME": "A", "RAINFALL10": "", "RECEIVE_TIME": "2026-10-28 19:40"},
    ]
    rain = seoul_rain.aggregate(rows, ["A"])
    assert rain.rain_1h_mm == 8.0
    assert rain.rain_3h_mm == 15.0


def test_seoul_errors():
    with pytest.raises(CollectorError):
        seoul_rain.parse_rows('{"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키가 유효하지 않습니다."}}')
    with pytest.raises(CollectorError):
        seoul_rain.aggregate([], ["서초구청"])


def test_fixture_mode_rebases_time_to_now():
    rain = seoul_rain.fetch(S, now=NOW)
    assert rain.observed_at == NOW.replace(second=0)
    assert rain.rain_1h_mm == pytest.approx(49.5)


# ---------- 초단기실황 RN1 ----------
def test_kma_rn1():
    rain = kma_ultra.parse_rn1(read_fixture(kma_ultra.FIXTURE))
    assert rain.rain_1h_mm == 45.5
    assert rain.observed_at == datetime(2026, 10, 28, 19, 0, tzinfo=KST)


def test_kma_base_time():
    # 정시 자료는 40분 뒤에 올라옴
    assert kma_ultra.base_time(datetime(2026, 11, 9, 14, 39, tzinfo=KST)).hour == 13
    assert kma_ultra.base_time(datetime(2026, 11, 9, 14, 40, tzinfo=KST)).hour == 14
    assert kma_ultra.base_time(datetime(2026, 11, 9, 0, 10, tzinfo=KST)).day == 8


def test_kma_error_code():
    body = {"response": {"header": {"resultCode": "03", "resultMsg": "NO_DATA"}}}
    with pytest.raises(CollectorError):
        kma_ultra.parse_rn1(json.dumps(body))


# ---------- 방재 AWS 15분 ----------
def test_aws_15m_max_of_stations():
    rain = aws_rain.parse(read_fixture(aws_rain.FIXTURE), ["400", "401"])
    assert rain.rain_15m_mm == 21.5
    # 결측(-99) 지점만 있으면 오류
    with pytest.raises(CollectorError):
        aws_rain.parse(read_fixture(aws_rain.FIXTURE), ["402"])


# ---------- ITS 통제 지점 ----------
def test_its_only_full_block_or_sinkhole_in_bbox():
    pts = its_control.parse(read_fixture(its_control.FIXTURE), its_control.pilot_bbox(S))
    # 강남역 사거리 전면통제만 남음 (테헤란로 1차로 공사 = 부분 통제, 영동대로 땅꺼짐 = 범위 밖)
    assert [p.kind for p in pts] == ["전면통제"]
    p = pts[0]
    # EPSG:5186 미터 좌표로 변환됨 (강남역 부근 x≈203,000 y≈544,000)
    assert 200_000 < p.x < 206_000 and 541_000 < p.y < 547_000


def test_its_sinkhole_detected():
    item = {"eventType": "재난", "message": "영동대로 지반침하(땅꺼짐) 발생", "lanesBlocked": "전면통제"}
    assert its_control.classify(item) == "땅꺼짐"
    assert its_control.classify({"message": "1차로 공사"}) is None


# ---------- live 모드인데 키가 없으면 호출하지 않고 오류 ----------
@pytest.mark.parametrize("fetch", [seoul_rain.fetch, kma_ultra.fetch, aws_rain.fetch])
def test_live_without_key(fetch):
    with pytest.raises(CollectorError):
        fetch(LIVE, now=NOW)


def test_its_live_without_key():
    with pytest.raises(CollectorError):
        its_control.fetch(LIVE)
