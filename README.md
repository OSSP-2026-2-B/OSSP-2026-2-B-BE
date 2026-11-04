# OSSP-2026-2-B-BE
2026년 2학기 공개SW프로젝트 01분반 B조 백엔드 레포지토리입니다.

## 실행 방법

```powershell
conda env create -f environment.yml
conda activate flood_route
copy .env.example .env
uvicorn app.main:app --reload --port 8080
```

- API 문서: http://localhost:8080/docs

## 테스트

```powershell
ruff check .
pytest -q
```
