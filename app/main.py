"""FastAPI 진입점. 실행: uvicorn app.main:app --reload"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

app = FastAPI(title="Flood Risk-Aware Pedestrian Routing API", version="0.1.0")

# 백엔드가 프론트 주소에서 오는 요청을 허용 (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,  # .env 의 CORS_ORIGINS
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}