"""FastAPI 진입점. 실행: uvicorn app.main:app --reload"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.routes import router
from app.config import settings
from collectors.scheduler import start_scheduler, stop_scheduler

# 우리 코드의 logger.info(...) 가 터미널에 보이도록
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버 켜질 때
    if settings.scheduler_enabled:
        start_scheduler()
    yield
    # 서버 꺼질 때
    stop_scheduler()


app = FastAPI(title="Flood Risk-Aware Pedestrian Routing API", version="0.1.0", lifespan=lifespan)

# 백엔드가 프론트 주소에서 오는 요청을 허용 (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,  # .env 의 CORS_ORIGINS
    allow_methods=["*"],
    allow_headers=["*"],
)
# 프론트 연동용 API 등록 (/api/route, /api/risk-layer, /api/observations)
app.include_router(router)
# 오류 응답을 { "error": { "code", "message" } } 모양으로 통일
register_error_handlers(app)


@app.get("/health")
def health():
    return {"status": "ok"}