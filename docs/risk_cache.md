# 위험도 캐시와 수집기

팀 논의 3번(위험도 모듈 인터페이스)·4번(수집기 중 위험도 입력 담당분) 구현.

## 흐름

```
스케줄러 (collectors/scheduler.py, 평소 30분 / 강우 시 10분)
  └─ risk.cache.update_risk_cache() -> int
       ├─ collectors.seoul_rain   서울시 10분 강우 → 1h·3h       (기본 소스)
       ├─ collectors.kma_ultra    초단기실황 RN1 → 1h             (서울시 20분 넘게 없을 때 대체)
       ├─ collectors.aws_rain     방재 AWS RN-15m → 15m           (O_H 침수예보 조건)
       ├─ collectors.its_control  ITS 전면통제·땅꺼짐 지점         (통행 제외 규칙 1)
       └─ risk.model / risk.exclusion 으로 위험도·통행 제외·이유 태그 계산 → 스냅샷 교체
API (app/api/routes.py)
  ├─ risk.cache.get_snapshot() / is_data_unavailable(snapshot)   (True 면 503 DATA_UNAVAILABLE)
  ├─ POST /api/route       스냅샷의 risk·excluded·reasons·rain → routing.service.plan_routes
  └─ GET  /api/risk-layer  스냅샷 + risk.cache.get_static() 의 구간 모양 → risk.layer.build_layer
```

## 스냅샷 (`get_snapshot()`)

| 키 | 설명 |
| --- | --- |
| `risk` | `{segment_id: 위험도}`. 0 인 구간 생략 → `find_route(risk=...)` 에 그대로 |
| `excluded` | 통행 제외 `segment_id` 집합 (규칙 1 ∪ 규칙 2) → `find_route(excluded=...)` |
| `reasons` | `{segment_id: [이유 태그]}` — 위험도 > 0 또는 통행 제외 구간만. 예) `["침수흔적 2회 (2011·2022)", "도로 통제 (전면통제)"]` |
| `rain` | `rain_1h_mm`, `rain_3h_mm`(모르면 null), `rain_15m_mm`(모르면 null), `updated_at`, `delayed`, + `source`(seoul/kma/previous/none), `last_normal_at`(마지막 서울시 정상 관측) |
| `control_points` | 규칙 1 통제 지점 `[{lng, lat, kind, message}]` |
| `computed_at` | 계산 시각 |

첫 갱신 전에는 `None`.

## 강우 지연 규칙

1. 서울시 10분 관측이 20분 이내 → 그대로 사용 (`delayed: false`)
2. 20분 넘게 없음 → 초단기실황 RN1 대체, `delayed: true`. 3시간 강우는 알 수 없어 `null`
   (규칙 2 는 1시간 72mm 조건만 판단)
3. 대체도 실패 → 직전 스냅샷의 강우 재사용 (`source: previous`)
4. 마지막 서울시 정상 관측이 60분 초과 → `is_data_unavailable()` True → `/api/route` 503 `DATA_UNAVAILABLE`

- 방재 AWS 15분 값이 실패하거나 20분보다 오래되면 `null` (1시간 55mm 조건은 계속 동작).
- ITS 수집 실패 시 직전 통제 목록을 유지 (통제 누락이 더 위험하므로).

## 수집기 모드

`.env` 의 `COLLECTOR_MODE`

- `fixture` (기본): `collectors/fixtures/` 의 샘플 응답 사용. 키 없이 로컬 개발·테스트·CI.
  샘플의 관측 시각은 "지금"으로 옮겨서 돌려준다. 샘플 값: 1h 49.5mm / 3h 85.5mm / 15분 21.5mm /
  강남역 사거리 전면통제 1건.
- `live`: 실제 API 호출. 키가 없으면 해당 수집기는 실패로 처리되어 위 지연 규칙을 따른다.

## 키 발급 후 확인할 것

- 서울시 `ListRainfallService`: 강우량계 이름(`SEOUL_RAIN_GAUGES`, 기본 `서초구청,강남구청`)이 실제 응답과 맞는지
- 방재 AWS: API허브 `nph-aws2_min` 의 `RN-15m` 열 이름, 지점 번호(`AWS_STATIONS`, 기본 `400,401`)
- ITS `eventInfo`: 전면통제·땅꺼짐 판별에 쓰는 필드(`lanesBlocked`, `message` 등)와 문구
- 초단기실황 격자(`KMA_GRID_NX/NY`, 기본 61·125)
