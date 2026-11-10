"""좌표 변환, 시범 지역 검사, 가장 가까운 교차점 찾기

요청은 위도·경도(WGS84, EPSG:4326)로 오고, 보행망은 미터 좌표(EPSG:5186)로 되어 있다.
"""

import math

import networkx as nx
import numpy as np
from pyproj import Transformer

from app.config import Settings, settings

_TO_5186 = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
_TO_4326 = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)


def to_xy(lat: float, lng: float) -> tuple[float, float]:
    """위도·경도 → 미터 좌표 (x, y)"""
    x, y = _TO_5186.transform(lng, lat)
    return x, y


def to_lnglat(x: float, y: float) -> list[float]:
    """미터 좌표 → GeoJSON 순서 [경도, 위도] (소수 6자리 ≈ 10cm)"""
    lng, lat = _TO_4326.transform(x, y)
    return [round(lng, 6), round(lat, 6)]


def in_pilot_area(lat: float, lng: float, s: Settings = settings) -> bool:
    """강남역 중심에서 반경(1km)에 존재하는지 여부"""
    cx, cy = to_xy(s.pilot_center_lat, s.pilot_center_lon)
    x, y = to_xy(lat, lng)
    return math.hypot(x - cx, y - cy) <= s.pilot_radius_m


class NodeIndex:
    """가장 가까운 교차점 찾기."""

    def __init__(self, G: nx.MultiGraph):
        self.ids = list(G.nodes)
        self.xy = np.array([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in self.ids])

    def nearest(self, x: float, y: float) -> tuple[int, float]:
        """(교차점 번호, 그 교차점까지 거리 m)"""
        d = np.hypot(self.xy[:, 0] - x, self.xy[:, 1] - y)
        i = int(d.argmin())
        return self.ids[i], float(d[i])