# 데이터 준비 (전처리)

원본 데이터(SHP, graphml)는 용량과 라이선스 때문에 git 에 올리지 않습니다. 아래 순서대로 각자 받고,
전처리 결과 `data/segments.gpkg` 만 커밋합니다.

## 1. 원본 데이터

| 데이터 | 받는 곳 | 키 | 둘 위치 |
| --- | --- | --- | --- |
| OSM 보행망 | 전처리 스크립트가 Overpass API 로 자동 다운로드 | 필요 없음 | `data/raw/walk.graphml` (자동 저장) |
| 서울시 침수흔적도 2010~2025 (2015·2021 없음) | data.go.kr 파일데이터 "서울특별시_침수흔적도" 검색 → 연도별 다운로드 | 필요 없음 | `data/raw/flood/<연도>/*.shp` |

- 침수흔적도는 연도별 압축 파일을 풀어 **폴더나 파일 이름에 연도(예: `2022`)가 들어가게** 둡니다.
  `.shp` 와 같은 이름의 `.shx`, `.dbf`, `.prj` 도 같은 폴더에 있어야 합니다.
- 좌표계: 원본 EPSG:5179 → 전처리에서 EPSG:5186 으로 변환합니다 (`.prj` 가 없으면 5179 로 간주).

```
data/raw/flood/
├── 2010/침수흔적도_2010.shp (.shx .dbf .prj)
├── 2011/...
└── 2025/...
```

## 2. 실행

```bash
conda activate flood_route
# (1) 침수흔적도 열 이름 확인 — 침수심(F_SHIM)·침수 연도 열이 코드와 맞는지 먼저 확인
python -m preprocess.build_segments --inspect
# (2) 전처리 실행 — OSM 다운로드 + 25m 분할 + U·H 계산 → data/segments.gpkg
python -m preprocess.build_segments
# (3) 다음부터는 저장된 보행망으로 (OSM 은 계속 바뀌므로 같은 결과 재현용)
python -m preprocess.build_segments --graphml data/raw/walk.graphml
```

열 이름이 다르면 `preprocess/flood_history.py` 의 `DEPTH_COLUMNS`(침수심), `DATE_COLUMNS`(침수 시작일) 에 추가합니다.
실행이 끝나면 구간 수, 지하 구간 수, 연도별 침수이력 구간 수가 출력됩니다.
**2022 년 H>0 구간이 0 개면 무언가 잘못된 것**입니다 (강남역은 2022 년 침수 지역).

## 3. 결과 파일 `data/segments.gpkg` (EPSG:5186)

### `segments` 레이어 — 25m 이하 보행 구간

| 열 | 형식 | 설명 |
| --- | --- | --- |
| `segment_id` | int | 구간 번호 (1 부터) |
| `u`, `v` | int | 양 끝 노드 번호 |
| `length` | float | 길이 m |
| `U_flag` | int 0/1 | 지하 구간 (OSM layer<0, tunnel=yes, indoor=yes). GeoPackage 는 열 이름 대소문자를 구분하지 않아 `u` 와 겹치므로 `U` 대신 `U_flag` 로 저장. `routing.graph` 가 엣지 속성 `U` 로 읽음 |
| `U_reason` | str | 이유 태그: `지하보도` / `지하차도` / `지하상가 통로` / `실내 통로` |
| `H` | float 0~1 | 침수이력 |
| `H_count` | int | 흔적이 있는 연도 수 |
| `H_years` | str | 예: `2010,2011,2022` |
| `geometry` | LineString | |

### `nodes` 레이어

| 열 | 형식 | 설명 |
| --- | --- | --- |
| `node_id` | int | OSM 노드는 OSM ID, 분할로 생긴 중간 노드는 (최대 ID + 1) 부터 |
| `geometry` | Point | |

## 4. 계산 규칙 요약

- **25m 분할**: 엣지 길이 L → `ceil(L/25)` 개의 같은 길이 구간.
- **U**: 위 OSM 태그 중 하나면 1. OSMnx 단순화 때 지상·지하 길이 한 엣지로 합쳐지지 않게
  `layer·tunnel·indoor` 가 다르면 엣지를 나눔.
- **H** (겹침 기준 — 기획서의 지목 기준에서 변경, 2026-11-06 팀 논의에서 확정)
  - 침수흔적도 2010~2025 어느 파일에도 지목 열이 없어 도로 필지를 구분할 수 없음
  - 흔적 폴리곤이 구간과 **겹치면** `min(1, F_SHIM / 0.5m)`
  - 겹치지 않고 **반경 30m 안이면** `min(1, F_SHIM / 0.5m) × 0.5`
  - 침수심이 비어 있으면 흔적이 있다는 사실만으로 1 (보수적)
  - 구간의 H = 모든 흔적 중 최댓값
- **침수 연도** (`H_years`): 파일 연도가 아니라 침수 시작일의 연도
  (`F_SAT_YMD` → `피해일시` → `F_YR` → 파일 연도 순). 예) 2018년 파일에 2020년 호우 기록,
  2024년 파일에 2023년 기록이 섞여 있음.

## 5. 원본 데이터 특이사항 (2026-11 확인)

- 13개 연도 모두 침수심 `F_SHIM` 있음. 2010~2020 은 열 구성 동일.
- 2022 부터 `TYPE`(상가·주택 등 건물 유형) 열이 생김. 2024·2025 는 `PNU`(필지 고유번호) 있음.
- 2025 는 열 이름이 다름 (`피해일시` = "20250822 - 1031" 형식, `F_SAT_YMD` 없음).
- 2022 파일은 일부 글자가 CP949 → UTF-8 변환 경고를 내지만 계산에 쓰는 열에는 영향 없음.

## 6. 이재민 임시주거시설 `data/shelters.csv` (경로 없음 안내용, 팀 논의 10번)

통행 제외 때문에 목적지까지 경로가 없을 때 안내할 시설 목록입니다. 원본은 API 로 받고 결과 CSV 만 커밋합니다.

| 항목 | 내용 |
| --- | --- |
| 출처 | 서울 열린데이터광장 OA-21068 "서울시 이재민임시주거시설" (Open API, 매월 갱신, 서울시 약 1,200곳) |
| 키 | `SEOUL_OPENDATA_KEY` (강우 수집기와 같은 키) |
| 서비스명 | `TbGtnVictP` (`.env` 의 `SEOUL_SHELTER_SERVICE` 로 바꿀 수 있음). 비슷한 `TlInetqkP` 는 지진겸용(OA-21064) 1,100곳이고 수용인원이 없음 |
| 사용 필드 | `FCLT_NO`(번호) · `ACTC_FCLT_NM`(시설명) · `DADDR`(도로명 주소) · `LAT`·`LOT`(위경도) · `VICTIM_ACTC_PWR`(이재민 수용능력) |
| 좌표 | 원본은 위경도(GPS)와 X·Y(GRS80 중부원점)를 함께 줌 → 위경도를 그대로 사용 (WGS84) |

```bash
conda activate flood_route
# (1) 응답 필드 이름 확인 (저장 안 함). 시설명·주소·위경도·수용인원 필드가 '사용할 필드'에 잡히는지 본다
COLLECTOR_MODE=live python -m preprocess.shelters --inspect
# (2) 저장 + 개수 출력 → data/shelters.csv
COLLECTOR_MODE=live python -m preprocess.shelters
```

- 필드 이름이 바뀌면 `preprocess/shelters.py` 의 `FIELDS` 후보에 추가합니다.
- `COLLECTOR_MODE` 없이(fixture) 실행하면 가상 샘플로 `data/shelters_sample.csv` 를 만듭니다 (커밋 안 됨).
- 실행이 끝나면 저장한 시설 수, 시범 지역(1km) 안 시설 수, **보행 그래프 노드 100m 안 시설 수**
  (= 그래프로 경로를 낼 수 있는 시설)와 그 목록이 출력됩니다.

처리 규칙
- 좌표가 없거나 서울 밖(입력 오류) → 버림
- 강남역에서 2km(`SHELTER_KEEP_RADIUS_M`) 안만 저장. 그래프는 반경 1km 가 아니라 약 2km×2km 사각형이라
  모서리 쪽(최대 약 1.4km) 시설도 그래프 안일 수 있음
- 시설명·주소에 '지하'가 있으면(‘지하철’ 제외) 침수 때 갈 곳이 아니므로 뺌
- 이름이 같고(공백·괄호 설명 무시) 20m 안이면 같은 건물의 여러 공간으로 보고 한 줄로 합치며 수용인원을 더함
- 강남역에서 가까운 순으로 저장

| 열 | 형식 | 설명 |
| --- | --- | --- |
| `shelter_id` | str | 원본 시설 번호 (없으면 `S0001` 같은 순번) |
| `name` | str | 시설명 |
| `address` | str | 주소 (층·공간 설명 포함 가능) |
| `lng`, `lat` | float | 경도·위도 (WGS84, 소수 6자리) |
| `capacity` | int | 이재민 수용능력, 명 (`VICTIM_ACTC_PWR`. 0 이거나 없으면 빈칸) |

live 결과 (2026-11-13): 서울시 1,200행 → 좌표 오류 6 제외, 강남역 2km 안 **20곳** 저장
(지하 시설·같은 건물 0). 시범 지역(1km) 안 6곳, **보행 그래프 노드 100m 안 7곳**:
서운중학교(강남역 513m)·역삼1문화센터(573m)·서초2동주민센터(689m)·서초4동주민센터(711m)·
서이초등학교(713m)·원명초등학교(987m)·반포고등학교(1,105m). 나머지 13곳은 그래프 밖(경로 계산 불가).

읽기: `from preprocess.shelters import load_shelters` → `Shelter(shelter_id, name, address, lng, lat, capacity)` 목록.
경로 안내는 시설 좌표를 `routing.locate.to_xy` 로 바꿔 가장 가까운 그래프 노드에 붙이면 됩니다.
