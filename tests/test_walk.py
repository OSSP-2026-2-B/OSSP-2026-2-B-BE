from routing.walk import duration_min, walk_speed


def test_walk_speed_by_rain():
    assert walk_speed(0) == 1.2
    assert walk_speed(0.9) == 1.2
    assert walk_speed(1.0) == 1.05     # 1mm 이상부터 약한 비
    assert walk_speed(19.9) == 1.05
    assert walk_speed(20.0) == 0.9     # 20mm 이상부터 강한 비
    assert walk_speed(116) == 0.9


def test_duration_min():
    # 1200m, 비 없음: 1200 / 1.2 = 1000초 = 16.7분 → 17분
    assert duration_min(1200, 0) == 17
    # 1200m, 48mm/h: 1200 / 0.9 = 1333초 = 22.2분 → 23분
    assert duration_min(1200, 48) == 23


def test_speed_does_not_change_route_choice():
    # 같은 강우에서는 모든 경로가 같은 속도라, 더 짧은 경로가 항상 더 빠르다
    assert duration_min(1200, 48) < duration_min(1500, 48)