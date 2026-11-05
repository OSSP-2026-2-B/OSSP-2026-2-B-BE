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
- **H** (겹침 기준 — 기획서의 지목 기준에서 변경, 팀 논의 중)
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
