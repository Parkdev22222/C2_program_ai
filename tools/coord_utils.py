"""[shim] 구현은 c2.domain.wargame.coordinates 로 이동됨. (Slice 5에서 제거 예정)"""
from c2.domain.wargame.coordinates import (  # noqa: F401
    xy_to_latlon, latlon_to_xy, waypoints_xy_to_latlon,
    waypoints_latlon_to_xy, is_latlon_coords,
)
