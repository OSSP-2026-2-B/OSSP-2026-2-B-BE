# API 명세 (v1.0)

침수 위험 회피 보행 경로 서비스 백엔드 API 입니다. 필드가 바뀌면 이 문서를 먼저 수정하고 FE 에 알립니다.

> `fallback`(경로가 없을 때 임시주거시설 안내), `danger_length_m`, `risk_cost` 는 11/15 서버에 반영됩니다.
> 그 전까지는 경로가 없으면 `404 NO_ROUTE` 가 나가고, 두 필드는 응답에 없습니다.

---

## 0. 공통

| 항목 | 내용 |
| --- | --- |
| Base URL (로컬) | `http://localhost:8080` |
| 서버 실행 | `uvicorn app.main:app --reload --port 8080` (레포 README 참고, API 키 없이 실행 가능) |
| 자동 문서 | `http://localhost:8080/docs` (서버 실행 중일 때) |
| 경로 | 모두 `/api` 로 시작 (`/health` 제외). axios baseURL 에는 `/api` 를 넣지 않습니다 |
| 형식 | 요청·응답 모두 JSON |
| 시간 | ISO 8601, 한국 시간 (`2026-11-13T15:50:00+09:00`) |
| 응답 시간 | 모든 API 는 10초(FE axios timeout) 안에 응답합니다 |
| 시범 지역 | 강남역 반경 1km. 출발·도착지는 이 안이어야 합니다 |
| 갱신 주기 | 위험도와 강우는 서버가 평소 30분, 비가 올 때 10분마다 갱신합니다 |

**좌표 순서 (중요)**

| 위치 | 형식 | 예 |
| --- | --- | --- |
| 요청 (`start`, `end`, `closures`) | `{ "lat": 위도, "lng": 경도 }` | `{ "lat": 37.4979, "lng": 127.0276 }` |
| 응답 (경로 선, 위치, 지도 데이터) | GeoJSON 규칙 `[경도, 위도]` | `[127.0276, 37.4979]` |
| 쿼리 문자열 (`bbox`, `closures`) | `경도,위도` | `127.0276,37.4979` |

카카오맵에 그릴 때는 `[경도, 위도]` → `{ lat: 위도, lng: 경도 }` 로 바꿔야 합니다.

```js
// src/shared/api/axios.js 의 api 인스턴스 사용
const { data } = await api.post('/api/route', {
  start: { lat: 37.4979, lng: 127.0276 },
  end: { lat: 37.5045, lng: 127.0250 },
  modes: ['shortest', 'balanced'],
})

// 응답 좌표 → 카카오맵 좌표
const path = data.routes[0].geometry.coordinates.map(([lng, lat]) => ({ lat, lng }))
```

## 엔드포인트 요약

| 메서드 | 경로 | 화면 | 설명 |
| --- | --- | --- | --- |
| POST | `/api/route` | 경로 비교 화면 | 출발·도착지로 모드별 경로 계산 |
| GET | `/api/risk-layer` | 지도 | 화면 범위 안 구간별 위험 등급 |
| GET | `/api/observations` | 강우 상태 바 | 현재 강우, 특보, [확인]/[추정] 배지 |
| GET | `/health` | - | 서버 상태 확인. `{ "status": "ok" }` |

---

## 1. POST `/api/route`

출발지와 도착지를 받아 모드별 경로를 계산합니다.

### 요청

```json
{
  "start": { "lat": 37.4979, "lng": 127.0276 },
  "end": { "lat": 37.5045, "lng": 127.0250 },
  "modes": ["shortest", "balanced"]
}
```

| 필드 | 타입 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- | --- |
| `start` | `{lat, lng}` | O | | 출발 좌표 |
| `end` | `{lat, lng}` | O | | 도착 좌표 |
| `modes` | string[] | X | `["shortest", "balanced"]` | 계산할 경로 모드. 응답 `routes` 가 이 순서로 나옵니다 |
| `rain_mm` | number | X | 없음 | 발표 시연용 가정 강우 (0~200). [4. 시나리오](#4-시나리오-발표-시연용) 참고 |
| `closures` | `{lat, lng}`[] | X | `[]` | 발표 시연용 가정 통제 지점 (최대 20개). [4. 시나리오](#4-시나리오-발표-시연용) 참고 |

**경로 모드**

| 값 | 이름 | 설명 |
| --- | --- | --- |
| `shortest` | 최단 | 위험을 보지 않는 가장 짧은 경로 (화면의 "빠른 경로") |
| `balanced` | 균형 (기본) | 고위험 구간은 피하되 크게 돌아가지 않는 경로 (화면의 "안전 경로") |
| `safety` | 안전 우선 | 균형보다 위험을 더 피하는 경로. 더 많이 돌아갈 수 있음 |

통행 제외 구간(공식 도로 통제, 침수경보 기준 충족 시 지하 구간)은 모든 모드에서 지나가지 않습니다.

### 응답 200: 경로가 있을 때

```json
{
  "rain": { "rain_1h_mm": 48.0, "updated_at": "2026-11-13T15:50:00+09:00", "scenario": false },
  "routes": [
    {
      "mode": "shortest",
      "distance_m": 804,
      "duration_min": 15,
      "walk_speed_mps": 0.9,
      "detour_min": 0,
      "risk_segment_count": 3,
      "danger_length_m": 75,
      "risk_cost": 182.4,
      "max_grade": "고위험",
      "geometry": {
        "type": "LineString",
        "coordinates": [[127.0276, 37.4979], [127.027531, 37.497866], [127.0250, 37.5045]]
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
      "distance_m": 896,
      "duration_min": 17,
      "walk_speed_mps": 0.9,
      "detour_min": 2,
      "risk_segment_count": 0,
      "danger_length_m": 0,
      "risk_cost": 41.0,
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

**`rain`**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `rain_1h_mm` | number | 경로 계산에 쓴 1시간 강우 (mm) |
| `updated_at` | string \| null | 강우 관측 시각. `rain_mm` 시나리오면 `null` |
| `scenario` | boolean | `rain_mm`·`closures` 가정으로 계산했으면 `true` → 화면에 "시나리오" 표시 |

**`routes[]`**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `mode` | string | `shortest` / `balanced` / `safety` |
| `distance_m` | integer | 경로 길이 (m) |
| `duration_min` | integer | 소요 시간 (분, 올림). 강우에 따른 보행 속도로 계산 |
| `walk_speed_mps` | number | 적용한 보행 속도 (m/s). 1시간 강우 1mm 미만 1.2, 20mm 미만 1.05, 20mm 이상 0.9 |
| `detour_min` | integer | 최단 경로보다 더 걸리는 시간 (분). 최단은 0 |
| `risk_segment_count` | integer | '위험' 등급 이상 구간 수 (구간은 최대 25m) |
| `danger_length_m` | integer | '위험' 등급 이상 구간의 길이 합 (m). 비교 카드의 "위험 구간 통과 거리" |
| `risk_cost` | number | 누적 위험: 경로의 모든 구간에 대해 위험도 × 길이(m) 를 더한 값. 클수록 위험에 오래 노출됨 |
| `max_grade` | string | 경로에서 가장 높은 등급: `안전` / `주의` / `위험` / `고위험` |
| `geometry` | GeoJSON LineString | 경로 선 `[경도, 위도]` 목록. 요청한 출발 지점에서 시작해 도착 지점에서 끝남 |
| `hazards` | object[] | 경로 위 '위험' 등급 이상 구간 (화면의 "왜 이 길을 피하나요?"). 없으면 `[]` |

**`hazards[]`**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `segment_id` | integer | 구간 번호 (최대 25m 구간) |
| `grade` | string | `위험` / `고위험` |
| `risk` | number | 위험도 (0~1) |
| `location` | `[경도, 위도]` | 구간 가운데 위치 |
| `reasons` | string[] | 위험 이유 태그. [6. 참고 값](#6-참고-값) 참고 |

- 위험 구간은 25m 단위라 같은 도로의 이어진 구간이 여러 개로 나옵니다. 화면에서는 이어진 같은 이유의 구간을 묶어 표시합니다.

### 응답 200: 경로가 없을 때 (임시주거시설 안내)

통행 제외 때문에 목적지까지 갈 수 있는 경로가 없으면, `routes` 를 빈 배열로 두고 `fallback` 에 가장 가까운 이재민 임시주거시설을 담아 보냅니다.

```json
{
  "rain": { "rain_1h_mm": 80.0, "updated_at": null, "scenario": true },
  "routes": [],
  "fallback": {
    "type": "shelter",
    "shelter_id": "746",
    "name": "역삼1문화센터",
    "address": "서울특별시 강남구 역삼로7길 16",
    "capacity": 98,
    "location": [127.033266, 37.495395],
    "reachable": true,
    "distance_m": 650,
    "duration_min": 13,
    "geometry": {
      "type": "LineString",
      "coordinates": [[127.0276, 37.4979], [127.0301, 37.4968], [127.033266, 37.495395]]
    },
    "reason": "통행 제외 구간 때문에 목적지까지 갈 수 있는 경로가 없습니다."
  }
}
```

**`fallback`**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `type` | string | 항상 `shelter` |
| `shelter_id` | string | 시설 번호 |
| `name` | string | 시설 이름 |
| `address` | string | 주소 |
| `capacity` | integer \| null | 수용 인원 (자료가 없으면 `null`) |
| `location` | `[경도, 위도]` | 시설 위치 |
| `reachable` | boolean | 출발지에서 시설까지 걸어갈 수 있는 경로가 있으면 `true` |
| `distance_m` | integer | `reachable: true` 면 시설까지 경로 길이, `false` 면 직선거리 (m) |
| `duration_min` | integer \| null | 시설까지 소요 시간 (분). `reachable: false` 면 `null` |
| `geometry` | GeoJSON LineString \| null | 시설까지 경로 선 (균형 모드). `reachable: false` 면 `null` |
| `reason` | string | 목적지 경로가 없는 이유 (화면에 그대로 표시 가능) |

- 경로가 있는 시설 중 가장 가까운 곳을 안내합니다.
- 시설까지 가는 길도 모두 막혔으면 직선거리로 가장 가까운 시설의 위치·주소만 보냅니다 (`reachable: false`).
- 경로가 있을 때 `fallback` 은 항상 `null` 입니다.

### 오류

| HTTP | code | 상황 |
| --- | --- | --- |
| 400 | `OUT_OF_PILOT_AREA` | 출발지나 도착지가 시범 지역(강남역 반경 1km) 밖 |
| 404 | `NO_ROUTE` | 경로도, 안내할 임시주거시설도 없음 (서버가 시설 자료를 읽지 못한 경우) |
| 422 | `INVALID_REQUEST` | 요청 형식 오류 (필수 값 누락, 없는 모드, `rain_mm` 이 0~200 밖, `closures` 20개 초과 등) |
| 503 | `DATA_UNAVAILABLE` | 강우 자료를 받을 수 없음 (서버를 켠 직후 몇 초, 또는 강우 관측이 60분 넘게 끊김). `rain_mm` 을 주면 발생하지 않음 |

---

## 2. GET `/api/risk-layer`

지도 화면 범위 안의 구간별 위험 등급을 GeoJSON 으로 돌려줍니다. 지도를 움직일 때마다 호출합니다.

### 요청

```
GET /api/risk-layer?bbox=127.020,37.492,127.035,37.505
GET /api/risk-layer?bbox=127.020,37.492,127.035,37.505&rain_mm=60&closures=127.0263,37.5010
```

| 파라미터 | 필수 | 형식 | 설명 |
| --- | --- | --- | --- |
| `bbox` | O | `최소경도,최소위도,최대경도,최대위도` | 지도 화면 범위 |
| `rain_mm` | X | 숫자 (0~200) | 발표 시연용 가정 강우. `/api/route` 와 같음 |
| `closures` | X | `경도,위도;경도,위도` (최대 20개) | 발표 시연용 가정 통제 지점. `/api/route` 와 같은 지점을 넣으면 됨 |

- `closures` 는 배열이 아니라 **문자열 하나**입니다 (axios 는 배열을 `closures[]=` 로 보내 서버가 읽지 못함).
  ```js
  const closuresParam = closures.map(({ lat, lng }) => `${lng},${lat}`).join(';')
  await api.get('/api/risk-layer', { params: { bbox, rain_mm, closures: closuresParam || undefined } })
  ```

### 응답 200

```json
{
  "type": "FeatureCollection",
  "updated_at": "2026-11-13T15:50:00+09:00",
  "scenario": false,
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

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `updated_at` | string \| null | 강우 관측 시각 (`/api/route` 의 `rain.updated_at` 과 같음). `rain_mm` 시나리오면 `null` |
| `scenario` | boolean | 시나리오로 계산했으면 `true` |
| `features[].geometry` | GeoJSON LineString | 구간 선 `[경도, 위도]` 목록 |
| `properties.segment_id` | integer | 구간 번호 (`/api/route` 의 `hazards[].segment_id` 와 같은 번호) |
| `properties.risk` | number | 위험도 (0~1) |
| `properties.grade` | string | `안전` / `주의` / `위험` / `고위험` |
| `properties.excluded` | boolean | `true` 면 통행 제외 구간 (지나갈 수 없음) |
| `properties.reasons` | string[] | 위험 이유 태그 |

- `안전` 등급 구간은 보내지 않습니다. 단, 통행 제외 구간은 등급과 관계없이 보냅니다.
- 비가 오지 않으면 `features` 는 빈 배열일 수 있습니다.

### 오류

| HTTP | code | 상황 |
| --- | --- | --- |
| 422 | `INVALID_REQUEST` | `bbox` 형식 오류 (숫자 4개가 아니거나 최소값 ≥ 최대값), `rain_mm`·`closures` 형식 오류 |
| 503 | `DATA_UNAVAILABLE` | 강우 자료를 받을 수 없음. `rain_mm` 을 주면 발생하지 않음 |

---

## 3. GET `/api/observations`

화면 위쪽 강우 상태 바에 표시할 정보입니다. 시나리오와 관계없이 항상 **실제 관측값**입니다.

### 응답 200

```json
{
  "updated_at": "2026-11-13T15:50:00+09:00",
  "delayed": false,
  "rain_1h_mm": 49.5,
  "rain_3h_mm": 85.5,
  "rain_15m_mm": 21.5,
  "forecast_1h_mm": null,
  "badges": [
    { "kind": "confirmed", "label": "호우경보", "source": "기상청 기상특보", "issued_at": "2026-11-13T12:50:00+09:00" },
    { "kind": "estimated", "label": "침수예보 기준 충족", "source": "방재 AWS 15분 강우", "issued_at": null }
  ]
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `updated_at` | string | 강우 관측 시각 |
| `delayed` | boolean | 서울시 강우 자료가 늦어 기상청 자료로 대체 중이면 `true` → 화면에 "갱신 지연" 표시 |
| `rain_1h_mm` | number | 최근 1시간 강우 (mm) |
| `rain_3h_mm` | number \| null | 최근 3시간 강우 (mm). 자료가 없으면 `null` |
| `rain_15m_mm` | number \| null | 최근 15분 강우 (mm). 자료가 없으면 `null` |
| `forecast_1h_mm` | null | 예상 강우. 현재 제공하지 않음 (항상 `null`) |
| `badges` | object[] | 상태 바 배지. 해당 사항이 없으면 `[]` |

**`badges[]`**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `kind` | string | `confirmed` = 공식 발표 정보 **[확인]**, `estimated` = 서비스가 강우로 계산한 정보 **[추정]** |
| `label` | string | 화면에 표시할 글자 |
| `source` | string | 출처 |
| `issued_at` | string \| null | 발효 시각 (`confirmed` 만). `estimated` 는 `null` |

**배지 종류**

| kind | label | 조건 |
| --- | --- | --- |
| `confirmed` | `호우주의보` / `호우경보` | 기상청이 서울동남권(강남·서초)에 발표. 둘 다 있으면 경보만 |
| `estimated` | `침수경보 기준 충족` | 1시간 72mm 이상, 또는 1시간 50mm 이상이면서 3시간 90mm 이상 |
| `estimated` | `침수예보 기준 충족` | 15분 20mm 이상, 또는 1시간 55mm 이상. 침수경보 기준을 충족하면 경보 배지만 보냄 |

`confirmed` 와 `estimated` 는 함께 올 수 있습니다 (최대 2개).

### 오류

| HTTP | code | 상황 |
| --- | --- | --- |
| 503 | `DATA_UNAVAILABLE` | 강우 자료를 받을 수 없음 |

---

## 4. 시나리오 (발표 시연용)

발표 날 비가 오지 않아도 폭우 상황을 보여 주기 위한 기능입니다. 같은 출발·도착지로 가정만 바꿔 `/api/route` 와 `/api/risk-layer` 를 다시 호출하면 됩니다.

| 화면 | 파라미터 | 설명 |
| --- | --- | --- |
| 강우 슬라이더 | `rain_mm` | 실제 강우 대신 이 1시간 강우(0~200mm)로 계산 |
| 통제 토글 | `closures` | 실제 도로 통제에 더해, 찍은 지점 반경 30m 안 구간을 통행 제외 |

- 둘 중 하나라도 주면 응답의 `scenario` 가 `true` 입니다.
- `rain_mm` 을 주면 강우 자료가 끊겨도 503 이 나지 않습니다 (발표 중 네트워크가 불안해도 시연 가능).
- 도착지 근처를 `closures` 로 막으면 "경로 없음 → 임시주거시설 안내" 화면을 보여 줄 수 있습니다.
- `/api/observations` 는 시나리오를 받지 않습니다 (강우 상태 바는 항상 실제 관측).

**강우 슬라이더 참고 값** (실제 강남역 보행망 기준)

| `rain_mm` | 결과 |
| --- | --- |
| 0~30 | 위험도 0. 모든 모드가 같은 경로 |
| 약 49 이상 | 침수흔적이 있는 구간이 '고위험' → 균형·안전 모드가 우회 |
| 72 이상 | 침수경보 기준 → 침수흔적과 겹치는 지하 구간 통행 제외 |

---

## 5. 오류 응답 형식

모든 오류는 같은 형식입니다. `error.code` 로 상황을 구분하고, `error.message` 는 그대로 화면에 띄워도 되는 문장입니다.

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

| HTTP | code | 상황 | 화면 처리 예 |
| --- | --- | --- | --- |
| 400 | `OUT_OF_PILOT_AREA` | 출발·도착지가 시범 지역 밖 | "강남역 반경 1km 안에서만 이용할 수 있어요" |
| 404 | `NO_ROUTE` | 경로도, 안내할 임시주거시설도 없음 | "지금은 안내할 수 있는 길이 없어요" |
| 404 | `NOT_FOUND` | 없는 주소로 요청 | (개발 오류) |
| 405 | `METHOD_NOT_ALLOWED` | 허용되지 않는 요청 방식 | (개발 오류) |
| 422 | `INVALID_REQUEST` | 요청 형식 오류 | (개발 오류) |
| 503 | `DATA_UNAVAILABLE` | 강우 자료를 받을 수 없음 | "강우 정보를 불러오는 중입니다. 잠시 후 다시 시도해 주세요" |

---

## 6. 참고 값

**위험 등급**

| 등급 | 위험도 |
| --- | --- |
| `안전` | 0.3 미만 |
| `주의` | 0.3 이상 0.6 미만 |
| `위험` | 0.6 이상 0.8 미만 |
| `고위험` | 0.8 이상 |

**이유 태그 (`reasons`)**

| 태그 예 | 뜻 |
| --- | --- |
| `침수흔적 3회 (2010·2011·2022)` | 과거 침수 기록이 있는 구간 (횟수와 연도) |
| `지하보도`, `지하 구간` 등 | 지하 구간 |
| `침수경보 기준 충족 (지하 구간 통행 제외)` | 강한 비로 통행 제외된 지하 구간 |
| `도로 통제 (전면통제)`, `도로 통제 (땅꺼짐)` | 실제 도로 통제로 통행 제외된 구간 |
| `도로 통제 (시나리오)` | 시연용 `closures` 로 통행 제외된 구간 |

---

## 변경 기록

- v0.1 (10/28): 초안
- v0.1.1 (11/4): 로컬 포트 8080, 오류 응답 형식 통일
- v0.1.2·v0.1.4 (11/9·11/11): 경로 모드 α 확정 (최단 0, 균형 2, 안전 우선 4)
- v0.1.3·v0.1.5·v0.1.6 (11/10~11/12): 세 API 실제 데이터 연결
- v0.1.7~v0.1.8 (11/13): 발표 시연용 시나리오 `rain_mm`·`closures`
- v1.0 (11/13): FE 공유용 최종본. 비교 카드 값 `danger_length_m`·`risk_cost` 추가, 경로가 없으면 404 대신 200 + `fallback` (11/15 서버 반영)