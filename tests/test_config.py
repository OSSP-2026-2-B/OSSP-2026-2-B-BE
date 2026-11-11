from app.config import Settings


def test_alpha_modes():
    s = Settings(_env_file=None)  # .env 없이 기본값만으로 확인 (CI 환경과 같게)
    assert s.alphas == {"shortest": 0.0, "balanced": 2.0, "safety": 4.0}
    assert s.alpha_safety == 2 * s.alpha_balanced  # 11-06 팀 결정: 안전 = 균형 × 2


def test_cors_origin_list():
    s = Settings(_env_file=None, cors_origins="http://a.com, http://b.com")
    assert s.cors_origin_list == ["http://a.com", "http://b.com"]