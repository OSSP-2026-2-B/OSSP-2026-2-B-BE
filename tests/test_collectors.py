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
def row(name, mm, time):
    """실제 응답과 같은 모양의 한 행 (2026-11-11 live 확인)"""
    return {"RF_CD": 9999.0, "RF_NM": name, "GU_CD": 124.0, "GU_NM": "서초구",
            "RN_10M": mm, "DATA_CLCT_TM": time}


def test_seoul_fixture_max_of_gauge_sums():
    rows = seoul_rain.parse_rows(read_fixture(seoul_rain.FIXTURE))
    rain = seoul_rain.aggregate(rows, ["서초구청", "강남구청"])
    # 서초구청(1h 49.5) · 강남구청(1h 47.0) 중 큰 값. 대상 밖 '방배2동'(10분 20mm) 은 무시
    assert rain.rain_1h_mm == pytest.approx(49.5)
    assert rain.rain_3h_mm == pytest.approx(85.5)
    assert rain.observed_at == datetime(2026, 10, 28, 20, 0, tzinfo=KST)


def test_seoul_window_and_missing_values():
    rows = [
        row("A", "5", "2026-10-28 20:00"),
        row("A", "3", "2026-10-28 19:10"),   # 50분 전: 1h 포함
        row("A", "7", "2026-10-28 19:00"),   # 60분 전: 제외
        row("A", "-1", "2026-10-28 19:50"),  # 결측
        row("A", "", "2026-10-28 19:40"),
        row("A", None, "2026-10-28 19:30"),
    ]
    rain = seoul_rain.aggregate(rows, ["A"])
    assert rain.rain_1h_mm == 8.0
    assert rain.rain_3h_mm == 15.0


def test_seoul_takes_larger_gauge_sum_not_sum_of_both():
    # 두 강우량계의 수집 시각이 1분 어긋나도 서로 더하지 않는다 (A 1h 12, B 1h 18 → 18)
    a_times = ["14:00", "14:10", "14:20", "14:30", "14:40", "14:50"]
    b_times = ["13:59", "14:09", "14:19", "14:29", "14:39", "14:49"]
    rows = [row("A", "2", f"2026-11-11 {hm}") for hm in a_times]
    rows += [row("B", "3", f"2026-11-11 {hm}") for hm in b_times]
    rain = seoul_rain.aggregate(rows, ["A", "B"])
    assert rain.observed_at == datetime(2026, 11, 11, 14, 50, tzinfo=KST)
    assert rain.rain_1h_mm == 18.0


def test_seoul_stale_gauge_left_out():
    # B 는 2시간 전에 끊김 → 최근 1시간에는 A 만, 3시간에는 B 의 옛 값도 들어간다
    rows = [row("A", "1", "2026-11-11 15:00"), row("B", "30", "2026-11-11 13:00")]
    rain = seoul_rain.aggregate(rows, ["A", "B"])
    assert rain.rain_1h_mm == 1.0
    assert rain.rain_3h_mm == 30.0


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
def test_aws_ignores_help_lines():
    # 실제 응답(help=1)의 설명 줄 '#  RN-15m : ...' 이 열 이름 줄 뒤에 와도 열 위치를 잃지 않는다
    lines = read_fixture(aws_rain.FIXTURE).splitlines()
    i = next(n for n, ln in enumerate(lines) if "STN" in ln and "RN-15m" in ln)
    lines.insert(i + 1, "#  RN-15m : 15분 누적 강수량 (mm)")
    assert aws_rain.parse("\n".join(lines), ["400"]).rain_15m_mm == 21.5


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
