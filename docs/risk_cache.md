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
       └─ build_risk_cache(정적, 강우, 통제 지점) → 스냅샷 교체
API (app/api/routes.py)
  ├─ 시나리오 없음: risk.cache.get_snapshot() / is_data_unavailable(snapshot)  (True 면 503 DATA_UNAVAILABLE)
  ├─ rain_mm 있음: risk.cache.build_scenario(rain_mm, closures)  (요청마다 계산, 캐시는 그대로, 503 검사 없음)
  ├─ closures 만:  503 검사 후 build_scenario(closures=..., base_rain=실시간 rain)
  ├─ POST /api/route       스냅샷의 risk·excluded·reasons·rain → routing.service.plan_routes
  └─ GET  /api/risk-layer  스냅샷 + risk.cache.get_static() 의 구간 모양 → risk.layer.build_layer
```

## 계산 함수 `build_risk_cache(static, rain, controls, now, scenario=False)`

정적 데이터 + 강우 + 통제 지점 → 스냅샷 (아래 모양). 수집기·캐시 상태를 모르는 순수 함수라
실서비스와 발표 시연이 **같은 계산 코드**를 지난다. 다른 것은 강우·통제 지점을 어디서 가져오는지뿐이다.

| | 실서비스 `RiskCache.update()` | 시연 `RiskCache.scenario(rain_mm, closures, base_rain)` |
| --- | --- | --- |
| 강우 | 수집기 (서울시 → 초단기실황 → 이전 값) | `rain_mm` 이 있으면 `scenario_rain(rain_mm)`: 1h = rain_mm, 3h·15m = None, `source: "scenario"`. 없으면 `base_rain`(실시간 스냅샷의 rain) 그대로 |
| 통제 지점 | ITS 수집 (실패 시 직전 목록) | 마지막으로 수집한 실제 통제 + `closures`(종류 `시나리오` → 이유 태그 `도로 통제 (시나리오)`) |
| `scenario` | `False` | `True` |
| 결과 | 캐시 교체 → `get_snapshot()` | 돌려주기만 함 (캐시 그대로) |

- 안에서 하는 일: `risk.model.compute_risks`(위험도) → `risk.exclusion.rule1_excluded`(통제 종류별) ·
  `rule2_excluded`(침수경보 기준) → 이유 태그(정적 요인 → 도로 통제 → 침수경보 순).
- 3시간 강우를 모르면 규칙 2 는 1시간 72mm 조건만 본다 (초단기실황 대체 때와 같음).
  그래서 강우 입력이 같으면 초단기실황 대체 결과와 시나리오 결과가 같다 (테스트로 확인).
- 실제 강남역 데이터(통제 지점 1건)에서 강우 값 하나 계산에 약 30ms → API 요청마다 불러도 된다.

## 스냅샷 (`get_snapshot()`)

| 키 | 설명 |
| --- | --- |
| `risk` | `{segment_id: 위험도}`. 0 인 구간 생략 → `find_route(risk=...)` 에 그대로 |
| `excluded` | 통행 제외 `segment_id` 집합 (규칙 1 ∪ 규칙 2) → `find_route(excluded=...)` |
| `reasons` | `{segment_id: [이유 태그]}` — 위험도 > 0 또는 통행 제외 구간만. 예) `["침수흔적 2회 (2011·2022)", "도로 통제 (전면통제)"]` |
| `rain` | `rain_1h_mm`, `rain_3h_mm`(모르면 null), `rain_15m_mm`(모르면 null), `updated_at`, `delayed`, + `source`(seoul/kma/previous/none, 시나리오는 scenario), `last_normal_at`(마지막 서울시 정상 관측) |
| `control_points` | 규칙 1 통제 지점 `[{lng, lat, kind, message}]` (시나리오면 `closures` 도 `kind: "시나리오"` 로 포함) |
| `computed_at` | 계산 시각 |
| `scenario` | 가정(`rain_mm`·`closures`)이 들어간 시연 계산이면 `true`. 실시간 스냅샷은 `false` |

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

## live 점검 결과 (2026-11-11, `COLLECTOR_MODE=live`)

| 수집기 | 결과 | 조치 |
| --- | --- | --- |
| 서울시 `ListRainfallService` | 정상 응답. 구 필터 동작(서초구 11,979건·강남구 17,982건), 서초구청·강남구청 있음, 10분 간격(두 강우량계 모두 매시 9·19·…·59분). **필드 이름이 달랐음**: `RF_NM`·`RN_10M`·`DATA_CLCT_TM` (+ `RF_CD`·`GU_CD`·`GU_NM`) | `seoul_rain.py`·샘플 응답·테스트를 실제 필드 이름으로 수정 → 재점검에서 `source: seoul` 확인 |
| 초단기실황 | 정상 (격자 61·125, RN1 읽음) | 없음 |
| 방재 AWS (API허브 `nph-aws2_min`) | 처음엔 403(이 API 활용신청 전) → 신청 후 정상. `RN-15m` 열, 지점 `400`·`401` 있음(응답 지점 737개). 응답에 `#  RN-15m : 15분 누적 강수량 (mm)` 같은 설명 줄이 섞여 있음 | 열 이름 줄을 `YYMMDDHHMI`·`STN`·`RN-15m` 이 함께 있는 줄로 찾도록 보완 |
| ITS `eventInfo` | 정상. 범위 안 1건(공사). 실제 응답은 `eventDetailType`·`lanesBlocked` 가 비어 있고 `message` 가 `<공사>::도로::구간::…::부분통제::기관` 형식 | 판별은 `message` 단어(전면통제·땅꺼짐 등)에 의존. 실제 통제·땅꺼짐 사례가 나오면 문구 확인 |

- 서울시 1시간 강우는 강우량계마다 최근 60분 10분 값을 합한 뒤 서초구청·강남구청 중 큰 값을 쓴다
  (기획서 R(t)). 강우량계마다 따로 합하므로 두 강우량계의 수집 시각이 어긋나도 두 번 더해지지 않는다.
- 방재 AWS 가 실패하면 15분 강우는 `null` → O_H 의 15분 20mm 조건만 빠지고 1시간 55mm 조건은 동작한다.
