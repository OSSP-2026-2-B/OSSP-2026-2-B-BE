"""
오류 응답을 모두 같은 모양으로 통일

    { "error": { "code": "OUT_OF_PILOT_AREA", "message": "시범 지역(강남역 반경 1km) 밖입니다." } }
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """우리 서비스가 직접 내는 오류. 예: raise ApiError(400, "OUT_OF_PILOT_AREA", "시범 지역 밖입니다.")"""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_body(code: str, message: str, details: list | None = None) -> dict:
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


# FastAPI 가 자동으로 내는 오류 → 우리 code 로 바꾸기
HTTP_CODES = {
    404: ("NOT_FOUND", "없는 주소입니다."),
    405: ("METHOD_NOT_ALLOWED", "허용되지 않는 요청 방식입니다."),
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "reason": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body("INVALID_REQUEST", "요청 형식이 올바르지 않습니다.", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        code, message = HTTP_CODES.get(exc.status_code, ("HTTP_ERROR", str(exc.detail)))
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message),
            headers=getattr(exc, "headers", None),
        )