"""서비스 설정.
API 키는 .env 에서 읽고, 기준값은 이 파일 한 곳에서 관리한다.
.env 에 같은 이름(대문자)으로 값을 넣으면 코드 수정 없이 덮어쓸 수 있다. 예) ALPHA_BALANCED=6
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- API 키 (.env) ----
    its_api_key: str = ""         # 국토교통부 재난상황정보 (국가교통정보센터)
    kma_apihub_key: str = ""      # 기상청 API허브 기상특보
    data_go_kr_key: str = ""      # 공공데이터포털 (초단기실황·예보, 방재 AWS)
    seoul_opendata_key: str = ""  # 서울 열린데이터광장 (강우량, 하수관로·하천 수위)
    kakao_rest_key: str = ""      # 카카오 로컬 API

    # ---- 서버 ----
    app_env: str = "local"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ---- 회피 강도 α (기획안 4.4, 실측 보행망 실험 후 재확정) ----
    alpha_shortest: float = 0.0
    alpha_balanced: float = 2.0
    alpha_safety: float = 10.0

    # ---- 위험 등급 경계: 안전 < 0.3 ≤ 주의 < 0.6 ≤ 위험 < 0.8 ≤ 고위험 ----
    grade_caution: float = 0.3
    grade_danger: float = 0.6
    grade_severe: float = 0.8

    # ---- 통행 제외 규칙 2: 서울시 침수경보 상황판단 기준 ----
    flood_warning_1h_mm: float = 72.0      # 1시간 72mm 이상
    flood_warning_1h_alt_mm: float = 50.0  # 또는 1시간 50mm 이상이면서
    flood_warning_3h_mm: float = 90.0      #      3시간 90mm 이상

    # ---- 위험도 함수 파라미터 (기획안 3.3): s 강도, a 시작 강우, b 포화 강우 (mm/h) ----
    risk_h_s: float = 0.85   # H 침수이력
    risk_h_a: float = 30.0
    risk_h_b: float = 50.0
    risk_u_s: float = 0.5    # U 지하 구간
    risk_u_a: float = 30.0
    risk_u_b: float = 72.0
    risk_l_s: float = 0.5    # L 저지대 (2차)
    risk_l_a: float = 30.0
    risk_l_b: float = 50.0
    risk_w_s: float = 0.6    # W 하천 인접 (확장)
    risk_w_a: float = 30.0
    risk_w_b: float = 72.0

    # ---- O_H: 침수예보 강우 조건 (충족 시 g_H = 1) ----
    o_h_15m_mm: float = 20.0   # 15분 20mm 이상
    o_h_1h_mm: float = 55.0    # 또는 1시간 55mm 이상

    # ---- 통행 제외 규칙 1: ITS 통제 지점 반경 ----
    its_control_radius_m: float = 30.0

    # ---- 전처리: 보행망·침수이력 ----
    segment_max_m: float = 25.0          # 엣지를 이 길이 이하로 같은 길이 분할
    flood_full_depth_m: float = 0.5      # 침수심 ÷ 0.5m (최대 1)
    flood_nearby_radius_m: float = 30.0  # 건물·지목 없는 기록: 반경 30m
    flood_nearby_weight: float = 0.5     # 반경 기록은 × 0.5

    # ---- 수집 주기 ----
    collect_interval_normal_min: int = 30
    collect_interval_rain_min: int = 10
    scheduler_enabled: bool = True
    rain_detect_mm_per_h: float = 3.0      # 서울시 돌발강우 감지 기준

    # ---- 강우별 보행 속도 m/s (Shi et al., 2017) ----
    walk_speed_no_rain: float = 1.2
    walk_speed_light_rain: float = 1.05
    walk_speed_heavy_rain: float = 0.9
    light_rain_mm_per_h: float = 1.0       # 이 값 이상이면 약한 비 속도
    heavy_rain_mm_per_h: float = 20.0      # 이 값 이상이면 강한 비 속도

    # ---- 시범 지역: 강남역 반경 1km ----
    pilot_center_lat: float = 37.4979
    pilot_center_lon: float = 127.0276
    pilot_radius_m: float = 1000.0

    @property
    def alphas(self) -> dict[str, float]:
        """모드 이름 → α"""
        return {
            "shortest": self.alpha_shortest,
            "balanced": self.alpha_balanced,
            "safety": self.alpha_safety,
        }

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()