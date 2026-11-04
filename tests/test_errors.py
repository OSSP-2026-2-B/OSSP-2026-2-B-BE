"""오류 응답이 모두 { "error": { "code", "message" } } 모양인지 확인"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import ApiError, register_error_handlers
from app.main import app

client = TestClient(app)

START = {"lat": 37.4979, "lng": 127.0276}
END = {"lat": 37.5045, "lng": 127.0250}


def test_unknown_path_404():
    res = client.get("/api/없는주소")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_bad_mode_422_points_to_field():
    # modes 에 없는 값 → 어느 필드가 틀렸는지 details 에 담긴다
    res = client.post("/api/route", json={"start": START, "end": END, "modes": ["fast"]})
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["code"] == "INVALID_REQUEST"
    assert err["details"][0]["field"] == "modes.0"


def test_missing_query_422():
    # 필수 쿼리(bbox)가 빠져도 같은 모양
    res = client.get("/api/risk-layer")
    assert res.status_code == 422
    assert res.json()["error"]["details"][0]["field"] == "bbox"


def test_api_error_shape():
    # 우리가 직접 내는 오류 (예: 시범 지역 밖)
    test_app = FastAPI()
    register_error_handlers(test_app)

    @test_app.get("/boom")
    def boom():
        raise ApiError(400, "OUT_OF_PILOT_AREA", "시범 지역(강남역 반경 1km) 밖입니다.")

    res = TestClient(test_app).get("/boom")
    assert res.status_code == 400
    assert res.json() == {
        "error": {"code": "OUT_OF_PILOT_AREA", "message": "시범 지역(강남역 반경 1km) 밖입니다."}
    }


def test_error_has_cors_header():
    # 오류 응답에도 CORS 허용 헤더를 포함 (FE의 error.code 확인용)
    res = client.get("/api/없는주소", headers={"Origin": "http://localhost:5173"})
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"