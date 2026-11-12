# API 명세 (v0.1.6)

> 필드가 바뀌면 이 문서를 먼저 수정하고 FE에 알립니다.

- Base URL (로컬): `http://localhost:8080`
  - 실행: `uvicorn app.main:app --reload --port 8080`
- 자동 문서 (서버 실행 시): `http://localhost:8080/docs`
- 경로는 모두 `/api` 로 시작합니다 (`/health` 제외). baseURL 에는 `/api` 를 넣지 않습니다.

  ```js
  // FE: src/shared/api/axios.js 의 api 인스턴스 사용
  const { data } = await api.post('/api/route', {
    start: { lat: 37.4979, lng: 127.0276 },
    end: { lat: 37.5045, lng: 127.0250 },
    modes: ['shortest', 'balanced'],
  })
  ```
- 응답 제한: FE axios `timeout` 이 10초이므로 모든 API 는 10초 안에 응답합니다.
- 형식: 모든 요청·응답은 JSON, 시간은 ISO 8601 (KST, `+09:00`)
- **좌표 순서 주의**
  - 요청 본문: `{ "lat": 위도, "lng": 경도 }` 객체
  - 응답의 경로·지도 데이터: GeoJSON 규칙대로 `[경도, 위도]` 순서
- 위험도와 강우는 서버가 평소 30분, 비가 올 때 10분마다 갱신합니다.

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
| `start`, `end` | object | O | 출발·도착 좌표. 시범 지역(강남역 반경 1km) 안이어야 합니다 |
| `modes` | string[] | X | `shortest`(α=0) / `balanced`(α=2) / `safety`(α=4). 기본값 `["shortest", "balanced"]` |

### 응답 200

```json
{
  "rain": { "rain_1h_mm": 48.0, "updated_at": "2026-10-28T19:50:00+09:00" },
  "routes": [
    {
      "mode": "shortest",
      "distance_m": 1200,
      "duration_min": 23,
      "walk_speed_mps": 0.9,
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
    },
    {
      "mode": "balanced",
      "distance_m": 1500,
      "duration_min": 28,
      "walk_speed_mps": 0.9,
      "detour_min": 5,
      "risk_segment_count": 0,
      "max_grade": "주의",
      "geometry": {
        "type": "LineString",
        "coordinates": [[127.0276, 37.4979], [127.0281, 37.5003], [127.0250, 37.5045]]
      },
      "hazards": []
    }
  ],
  "fallback": null
}
```

| 필드 | 설명 |
| --- | --- |
| `rain` | 위험도 계산에 쓴 1시간 강우와 관측 시각 |
| `routes` | 요청한 `modes` 순서대로 |
| `distance_m` | 경로 길이 (m) |
| `duration_min` | 강우별 보행 속도(1.2 / 1.05 / 0.9 m/s)로 계산한 소요 시간 (분, 올림) |
| `walk_speed_mps` | 적용한 보행 속도. 1시간 강우 1mm 미만 1.2, 20mm 미만 1.05, 20mm 이상 0.9 |
| `detour_min` | 최단 경로보다 더 걸리는 시간 |
| `risk_segment_count` | '위험' 등급(위험도 0.6) 이상 구간 수 |
| `max_grade` | 경로에서 가장 높은 등급: `안전` / `주의` / `위험` / `고위험` |
| `geometry` | 경로 선. 요청한 출발 지점에서 시작해 도착 지점에서 끝납니다 |
| `hazards` | 경로 위 '위험' 등급 이상 구간과 이유 (화면의 "왜 이 길을 피하나요?"). `location` 은 구간 가운데 |
| `fallback` | 통행 제외로 경로가 없을 때 대신 안내할 이재민 임시주거시설. 없으면 `null` |

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

- 출발·도착지가 시범 지역 밖이면 `400 OUT_OF_PILOT_AREA`
- 통행 제외로 갈 수 있는 경로가 없으면 `404 NO_ROUTE`
- 강우 자료를 받을 수 없으면 `503 DATA_UNAVAILABLE`

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

- `안전` 등급 구간은 응답에서 뺍니다. 단, 통행 제외 구간은 등급과 관계없이 넣습니다.
- `excluded: true` 는 통행 제외 구간입니다 (공식 통제 또는 침수경보 시 지하 구간).
- 구간의 외곽 사각형이 `bbox` 와 겹치면 포함합니다.
- `updated_at` 은 위험도 계산에 쓴 강우 관측 시각입니다 (`/api/route` 의 `rain.updated_at` 과 같은 값).
- `bbox` 형식이 틀리면(숫자 4개가 아니거나 최소값 ≥ 최대값) `422 INVALID_REQUEST`
- 강우 자료를 받을 수 없으면 `503 DATA_UNAVAILABLE`

---

## 3. GET `/api/observations`

화면 위쪽 강우 상태 바에 표시할 정보입니다.

### 응답 200

```json
{
  "updated_at": "2026-11-12T13:51:00+09:00",
  "delayed": false,
  "rain_1h_mm": 49.5,
  "rain_3h_mm": 85.5,
  "rain_15m_mm": 21.5,
  "forecast_1h_mm": null,
  "badges": [
    { "kind": "confirmed", "label": "호우경보", "source": "기상청 기상특보", "issued_at": "2022-08-08T12:50:00+09:00" },
    { "kind": "estimated", "label": "침수예보 기준 충족", "source": "방재 AWS 15분 강우", "issued_at": null }
  ]
}
```

| 필드 | 설명 |
| --- | --- |
| `updated_at` | 강우 관측 시각 |
| `delayed` | 서울시 강우 자료가 지연되어 기상청 초단기실황으로 대체 중이면 `true` → 화면에 "갱신 지연" 표시 |
| `rain_3h_mm`, `rain_15m_mm` | 자료가 없으면 `null` |
| `forecast_1h_mm` | 예보 강우. 현재 미제공 (항상 `null`) |
| `badges[].kind` | `confirmed` = 공식 발표 정보 [확인], `estimated` = 서비스가 계산한 정보 [추정] |

배지 규칙
- `confirmed`: 서울동남권(강남·서초) 호우주의보·경보. 둘 다 있으면 경보만
- `estimated`: 아래 중 하나. 경보 기준을 충족하면 경보 배지만
  - `침수경보 기준 충족`: 1시간 72mm 이상, 또는 1시간 50mm 이상이면서 3시간 90mm 이상
  - `침수예보 기준 충족`: 15분 20mm 이상, 또는 1시간 55mm 이상
- 해당 사항이 없으면 `badges` 는 빈 배열
- 강우 자료를 받을 수 없으면 `503 DATA_UNAVAILABLE`

---

## 오류 응답

```json
{ "error": { "code": "OUT_OF_PILOT_AREA", "message": "시범 지역(강남역 반경 1km) 밖입니다." } }
```

422 는 잘못된 필드를 `details` 에 함께 보냅니다.

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "요청 형식이 올바르지 않습니다.",
    "details": [{ "field": "modes.0", "reason": "Input should be 'shortest', 'balanced' or 'safety'" }]
  }
}
```

| HTTP | code | 상황 |
| --- | --- | --- |
| 400 | `OUT_OF_PILOT_AREA` | 출발·도착지가 시범 지역 밖 |
| 404 | `NO_ROUTE` | 경로도, 대체 임시주거시설도 없음 |
| 404 | `NOT_FOUND` | 없는 주소 |
| 405 | `METHOD_NOT_ALLOWED` | 허용되지 않는 요청 방식 |
| 422 | `INVALID_REQUEST` | 요청 형식 오류 (`bbox` 형식 오류 포함) |
| 503 | `DATA_UNAVAILABLE` | 강우 자료를 받을 수 없음 (서버 시작 직후 첫 갱신 전, 또는 서울시 강우 관측이 60분 넘게 끊김) |

---

## 변경 기록

- v0.1 (10/28): 초안 작성 (/route, /risk-layer, /observations)
- v0.1.1 (11/4): 로컬 포트 8080 으로 변경, axios 호출 예시·응답 제한 10초 추가, 오류 응답 형식 통일
- v0.1.2 (11/9): 균형 모드 α 5 → 2
- v0.1.3 (11/10): /api/route 실제 경로 계산 적용
- v0.1.4 (11/11): 안전 모드 α 10 → 4
- v0.1.5 (11/11): /api/route·/api/risk-layer 실제 위험도 연결, 강우 자료가 없으면 503 DATA_UNAVAILABLE
- v0.1.6 (11/12): /api/observations 실제 연결 (기상특보 [확인] 배지, 침수예보·경보 기준 [추정] 배지)