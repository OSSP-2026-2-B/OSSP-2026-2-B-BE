"""FastAPI 진입점. 실행: uvicorn app.main:app --reload"""

from fastapi import FastAPI

app = FastAPI(title="Flood Risk-Aware Pedestrian Routing API")


@app.get("/health")
def health():
    return {"status": "ok"}