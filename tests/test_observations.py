"""기상특보 수집기와 /api/observations 확인"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.api.routes as routes
from app.api.observations import WarningCache, build_observations
from app.main import app
from collectors.common import KST, CollectorError, read_fixture
from collectors.kma_warning import FIXTURE, WeatherWarning, parse

T = datetime(2026, 11, 12, 13, 0, tzinfo=KST)


def snapshot(r1h, r3h=None, r15m=None):
    return {"rain": {
        "rain_1h_mm": r1h, "rain_3h_mm": r3h, "rain_15m_mm": r15m,
        "updated_at": T.isoformat(), "delayed": False,
    }}


def warn(level):
    return WeatherWarning("호우", level, "서울동남권", T)


def test_parse_picks_only_our_region_heavy_rain():
    # 샘플 3건: 동남권 호우경보 / 서북권 호우경보 / 대구 폭염경보
    out = parse(read_fixture(FIXTURE))
    assert [(w.label, w.region) for w in out] == [("호우경보", "서울동남권")]


def test_parse_empty_and_broken():
    assert parse("[]") == []
    with pytest.raises(CollectorError):
        parse("<html>")


def test_badges_from_fixture_like_rain():
    out = build_observations(snapshot(49.5, 85.5, 21.5), [warn("경보")])
    assert [(b["kind"], b["label"]) for b in out["badges"]] == [
        ("confirmed", "호우경보"),
        ("estimated", "침수예보 기준 충족"),
    ]


def test_flood_warning_badge_replaces_forecast():
    out = build_observations(snapshot(75.0), [])
    assert [b["label"] for b in out["badges"]] == ["침수경보 기준 충족"]


def test_no_rain_no_badges():
    out = build_observations(snapshot(0.0), [])
    assert out["badges"] == []
    assert out["forecast_1h_mm"] is None


def test_warning_level_shows_strongest():
    out = build_observations(snapshot(0.0), [warn("주의보"), warn("경보")])
    assert out["badges"][0]["label"] == "호우경보"


def test_warning_cache_reuses_and_keeps_last_on_failure():
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) > 1:
            raise CollectorError("기상청 응답 없음")
        return [warn("경보")]

    cache = WarningCache(fetch=fetch, ttl_min=10)
    assert len(cache.get(T)) == 1
    assert len(cache.get(T + timedelta(minutes=5))) == 1   # 10분 안: 다시 부르지 않음
    assert len(calls) == 1
    assert len(cache.get(T + timedelta(minutes=11))) == 1  # 실패해도 직전 값 유지
    assert len(calls) == 2


def test_observations_api(monkeypatch):
    monkeypatch.setattr(routes, "current_snapshot", lambda: snapshot(49.5, 85.5, 21.5))
    monkeypatch.setattr(routes, "warning_cache", WarningCache(fetch=lambda: [warn("경보")]))

    res = TestClient(app).get("/api/observations")
    assert res.status_code == 200
    body = res.json()
    assert body["rain_1h_mm"] == 49.5
    assert {b["kind"] for b in body["badges"]} == {"confirmed", "estimated"}