"""U(지하 구간) 판별 (기획안 3.2).

OSM 태그 중 하나라도 맞으면 U = 1
- layer < 0        : 지면보다 아래층 (지하보도, 지하상가 통로)
- tunnel = yes     : 터널 (지하보도·지하차도)
- indoor = yes     : 실내 통로 (지하상가 등)
이유 태그(U_reason)는 화면의 "왜 이 길을 피하나요?" 에 그대로 나간다.
"""

PEDESTRIAN = {"footway", "pedestrian", "path", "steps", "corridor", "living_street", "elevator"}


def _values(v) -> list[str]:
    """OSMnx 는 여러 OSM 길이 하나로 합쳐지면 태그를 리스트로 준다 → 항상 문자열 리스트로."""
    if v is None:
        return []
    if isinstance(v, float) and v != v:  # NaN
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if x is not None]
    # graphml 로 저장했다 읽으면 리스트가 "['footway', 'steps']" 같은 문자열이 된다
    text = str(v).strip().strip("[]")
    return [s.strip().strip("'\"") for s in text.replace(",", ";").split(";") if s.strip()]


def _min_layer(v) -> float | None:
    nums = []
    for s in _values(v):
        try:
            nums.append(float(s))
        except ValueError:
            continue
    return min(nums) if nums else None


def classify_underground(layer=None, tunnel=None, indoor=None, highway=None) -> tuple[int, str]:
    """→ (U, U_reason). 지하가 아니면 (0, "")."""
    lay = _min_layer(layer)
    is_below = lay is not None and lay < 0
    is_tunnel = "yes" in _values(tunnel)
    is_indoor = "yes" in _values(indoor)
    if not (is_below or is_tunnel or is_indoor):
        return 0, ""

    if is_indoor and not (is_below or is_tunnel):
        return 1, "실내 통로"
    if is_indoor:
        return 1, "지하상가 통로"
    hw = set(_values(highway))
    if hw and not (hw & PEDESTRIAN):
        return 1, "지하차도"
    return 1, "지하보도"
