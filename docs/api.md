# API 명세 (초안 v0.1)

> BE가 구현되기 전까지 FE는 아래 응답 예시로 화면을 개발합니다.
> 필드가 바뀌면 이 문서를 먼저 수정하고 FE에 알립니다.

- Base URL (로컬): `http://localhost:8000`
- 자동 문서 (서버 실행 시): `http://localhost:8000/docs`
- 형식: 모든 요청·응답은 JSON, 시간은 ISO 8601 (KST, `+09:00`)
- **좌표 순서 주의**
  - 요청 본문: `{ "lat": 위도, "lng": 경도 }` 객체
  - 응답의 경로·지도 데이터: GeoJSON 규칙대로 `[경도, 위도]` 순서

## 엔드포인트 요약

| 메서드 | 경로 | 화면 | 설명 |
| --- | --- | --- | --- |
| GET | `/health` | - | 서버 상태 확인 |
| POST | `/api/route` | 경로 비교 화면 | 출발·도착지로 모드별 경로 비교 |
| GET | `/api/risk-layer` | 지도 | 화면 범위의 구간별 위험 등급 |
| GET | `/api/observations` | 강우 상태 바 | 현재 강우, 특보, [확인]/[추정] 정보 |

---

## 1. POST `/api/route`

출발지와 도착지를 받아 모드별 경로를 계산합니다.

### 요청

```json
{
  "start": { "lat": 37.4979, "lng": 127.0276 },
  "end":   { "lat": 37.5045, "lng": 127.0250 },
  "modes": ["shortest", "balanced"]
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `start`, `end` | object | O | 출발·도착 좌표 |
| `modes` | string[] | X | `shortest`(α=0) / `balanced`(α=5) / `safety`(α=10). 기본값 `["shortest", "balanced"]` |

### 응답 200

```json
{
  "rain": { "rain_1h_mm": 48.0, "updated_at": "2026-10-28T19:50:00+09:00" },
  "routes": [
    {
      "mode": "balanced",
      "distance_m": 1500,
      "duration_min": 24,
      "walk_speed_mps": 1.05,
      "detour_min": 5,
      "risk_segment_count": 0,
      "max_grade": "주의",
      "geometry": {
        "type": "LineString",
        "coordinates": [[127.0276, 37.4979], [127.0281, 37.5003], [127.0250, 37.5045]]
      },
      "hazards": []
    },
    {
      "mode": "shortest",
      "distance_m": 1200,
      "duration_min": 19,
      "walk_speed_mps": 1.05,
      "detour_min": 0,
      "risk_segment_count": 3,
      "max_grade": "고위험",
      "geometry": {
        "type": "LineString",
        "coordinates": [[127.0276, 37.4979], [127.0262, 37.5010], [127.0250, 37.5045]]
      },
      "hazards": [
        {
          "segment_id": 1024,
          "grade": "고위험",
          "risk": 0.85,
          "location": [127.0265, 37.4998],
          "reasons": ["침수흔적 3회 (2010·2011·2022)"]
        }
      ]
    }
  ],
  "fallback": null
}
```

| 필드 | 설명 |
| --- | --- |
| `duration_min` | 강우별 보행 속도(1.2 / 1.05 / 0.9 m/s)로 계산한 소요 시간 |
| `detour_min` | 최단 경로보다 더 걸리는 시간 |
| `risk_segment_count` | '위험' 등급(위험도 0.6) 이상 구간 수 |
| `max_grade` | 경로에서 가장 높은 등급: `안전` / `주의` / `위험` / `고위험` |
| `hazards` | 경로 위의 위험 구간과 이유 (화면의 "왜 이 길을 피하나요?") |
| `fallback` | 통행 제외 후 경로가 없을 때 대신 안내할 이재민 임시주거시설. 없으면 `null` |

경로가 없을 때 `fallback` 예시:

```json
"fallback": {
  "type": "shelter",
  "name": "(예시) 인근 임시주거시설",
  "location": [127.0330, 37.4950],
  "distance_m": 650,
  "reason": "침수경보 기준 충족으로 지하 구간이 통제되어 목적지까지 경로가 없습니다."
}
```

---

## 2. GET `/api/risk-layer`

지도 화면 범위 안의 구간별 위험 등급을 GeoJSON으로 돌려줍니다. 지도를 움직일 때마다 호출합니다.

### 요청

`GET /api/risk-layer?bbox=127.020,37.492,127.035,37.505`

| 파라미터 | 설명 |
| --- | --- |
| `bbox` | `최소경도,최소위도,최대경도,최대위도` |

### 응답 200

```json
{
  "type": "FeatureCollection",
  "updated_at": "2026-10-28T19:50:00+09:00",
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "LineString", "coordinates": [[127.0276, 37.4979], [127.0279, 37.4981]] },
      "properties": {
        "segment_id": 1024,
        "risk": 0.85,
        "grade": "고위험",
        "excluded": false,
        "reasons": ["침수흔적 3회 (2010·2011·2022)"]
      }
    }
  ]
}
```

- `안전` 등급 구간은 응답에서 뺍니다 (데이터 양을 줄이기 위해).
- `excluded: true`는 통행 제외 구간입니다 (공식 통제 또는 침수경보 시 지하 구간).

---

## 3. GET `/api/observations`

화면 위쪽 강우 상태 바에 표시할 정보입니다.

### 응답 200

```json
{
  "updated_at": "2026-10-28T19:50:00+09:00",
  "delayed": false,
  "rain_1h_mm": 38.0,
  "rain_3h_mm": 61.5,
  "rain_15m_mm": 22.0,
  "forecast_1h_mm": 45.0,
  "badges": [
    { "kind": "confirmed", "label": "호우경보", "source": "기상청 기상특보", "issued_at": "2026-10-28T19:40:00+09:00" },
    { "kind": "estimated", "label": "침수예보 기준 충족", "source": "방재 AWS 15분 강우", "issued_at": null }
  ]
}
```

| 필드 | 설명 |
| --- | --- |
| `delayed` | 서울시 강우 자료가 지연·결측되어 기상청 초단기실황으로 대체 중이면 `true` → 화면에 "갱신 지연" 표시 |
| `badges[].kind` | `confirmed` = 공식 발표 정보 [확인], `estimated` = 서비스가 계산한 정보 [추정] |

---

## 오류 응답

```json
{ "error": { "code": "OUT_OF_PILOT_AREA", "message": "시범 지역(강남역 반경 1km) 밖입니다." } }
```

| HTTP | code | 상황 |
| --- | --- | --- |
| 400 | `OUT_OF_PILOT_AREA` | 출발·도착지가 시범 지역 밖 |
| 404 | `NO_ROUTE` | 경로도, 대체 임시주거시설도 없음 |
| 422 | - | 요청 형식 오류 (FastAPI 기본) |
| 503 | `DATA_UNAVAILABLE` | 강우 자료를 모두 받을 수 없음 |

---

## 변경 기록

- v0.1 (10/28): 초안 작성 (/route, /risk-layer, /observations)